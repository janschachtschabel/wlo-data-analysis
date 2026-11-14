"""Versand nach einem geplanten Lauf: der Bericht per E-Mail und eine kurze Nachricht an einen
Webhook. Nach außen gehen nur Auswertungen (der Bericht, Zahlen der Veränderungen), nie
Datensätze. Ein Fehler beim Versand ändert nichts am Lauf; er steht als Satz am Zeitplan."""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from collections.abc import Awaitable, Callable
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

import httpx

from .api.export import file_name
from .config import Settings
from .report.build import build_report, format_number, format_time
from .report.formats import FORMATS, render
from .report.model import Report
from .store.history import History

log = logging.getLogger(__name__)

TIMEOUT = 20.0
MAX_ATTACHMENT = 15 * 1024 * 1024
FORMAT_LABELS = {"pdf": "PDF", "docx": "Word", "xlsx": "Excel", "html": "HTML"}


def change_line(item: dict[str, Any]) -> str:
    """Eine auffällige Veränderung als Satz, wie auf der Seite „Auffällig“."""
    where = f"{item['block']} · {item['row']} · {item['column']}"
    delta = item["delta"]
    signed = ("+" if delta > 0 else "−") + format_number(abs(delta))
    if item["unit"] == "pp":
        how = f"{signed} Punkte"
    elif item["percent"] is None:
        how = f"{signed}, vorher 0"
    else:
        percent = item["percent"]
        how = f"{signed}, {'+' if percent > 0 else '−'}{format_number(abs(percent), 1)} %"
    return (f"{where}: von {format_number(item['before'])} auf "
            f"{format_number(item['after'])} ({how})")


def missed_targets(report: Report) -> list[dict[str, str]]:
    """Tabellen des Berichts, deren Zielwert verfehlt ist, mit dem Satz dazu."""
    out = []
    for table in report.tables:
        target = table.target
        if not target or target.get("reached") == target.get("of"):
            continue
        text = next((n["text"] for n in table.notes if n.get("code") == "target"), "")
        out.append({"block": table.title, "text": text})
    return out


class Notifier:
    def __init__(self, settings: Settings, history: Callable[[], History]) -> None:
        self._settings = settings
        self._history = history
        # austauschbar (Tests): eine fertige Nachricht verschicken bzw. JSON an eine Adresse
        self.send_mail: Callable[[EmailMessage], None] = self._smtp
        self.post: Callable[[str, dict[str, Any]], Awaitable[None]] = self._post

    @property
    def mail_ready(self) -> bool:
        return bool(self._settings.smtp_host and self._settings.smtp_from)

    def _smtp(self, message: EmailMessage) -> None:
        cfg = self._settings
        context = ssl.create_default_context()
        if cfg.smtp_security == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port,
                                                    timeout=TIMEOUT, context=context)
        else:
            server = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=TIMEOUT)
        with server:
            if cfg.smtp_security == "starttls":
                server.starttls(context=context)
            if cfg.smtp_user and cfg.smtp_password is not None:
                server.login(cfg.smtp_user, cfg.smtp_password.get_secret_value())
            server.send_message(message)

    @staticmethod
    async def _post(url: str, payload: dict[str, Any]) -> None:
        # keine Weiterleitungen: Die Nachricht geht nur an die eingetragene Adresse
        async with httpx.AsyncClient(follow_redirects=False, timeout=TIMEOUT,
                                     headers={"User-Agent": "wlo-data-analysis"}) as client:
            (await client.post(url, json=payload)).raise_for_status()

    async def deliver(self, schedule: dict[str, Any], run: dict[str, Any]) -> str | None:
        """Verschickt den Bericht des Laufs, wie der Zeitplan es will. Liefert einen Satz
        für den Zeitplan; ``None``, wenn nichts zu verschicken ist."""
        send = schedule.get("send")
        if not send:
            return None
        report = build_report(run)
        targets = missed_targets(report)
        # verglichen wird mit dem letzten Lauf dieses Zeitplans, nicht mit einem Lauf, der
        # dazwischen von Hand oder über Nacht entstand und die Änderung schon gesehen hat
        changes = (await self._history().changes_since(report.run_id, schedule.get("last_run_id"))
                   if report.run_id else [])
        partial = report.status != "ok"  # ein lückenhafter Bericht ist selbst eine Nachricht
        if send.get("only_notable") and not targets and not changes and not partial:
            return "Nichts Auffälliges, nicht verschickt."
        base = self._settings.public_url
        link = f"{base}/lauf/{report.run_id}" if base and report.run_id else ""
        done, failed = [], []
        recipients = list(send.get("to") or [])
        if recipients:
            try:
                if not self.mail_ready:
                    raise RuntimeError("kein Mailserver eingerichtet (WDA_SMTP_HOST, "
                                       "WDA_SMTP_FROM)")
                message = await asyncio.to_thread(
                    self._message, report, recipients, send.get("format") or "pdf",
                    targets, changes, link)
                await asyncio.to_thread(self.send_mail, message)
                done.append(f"{len(recipients)} Empfänger")
            except Exception as exc:
                log.warning("Versand per E-Mail gescheitert (%s)", type(exc).__name__)
                failed.append(f"E-Mail: {_reason(exc)}")
        if send.get("webhook"):
            payload = {
                "title": report.title, "definition_id": report.definition_id,
                "run_id": report.run_id, "status": report.status,
                "started_at": report.started_at, "url": link or None,
                "missed_targets": targets,
                "changes": [{**{k: c[k] for k in ("block", "row", "column", "before", "after",
                                                   "delta", "percent", "unit")},
                             "text": change_line(c)} for c in changes]}
            try:
                await self.post(send["webhook"], payload)
                done.append("den Webhook")
            except Exception as exc:
                log.warning("Versand an den Webhook gescheitert (%s)", type(exc).__name__)
                failed.append(f"Webhook: {_reason(exc)}")
        text = f"Verschickt an {' und '.join(done)}." if done else ""
        if failed:
            text = (text + " " if text else "") + f"Versand gescheitert ({'; '.join(failed)})."
        return text

    def _message(self, report: Report, recipients: list[str], fmt: str,
                 targets: list[dict[str, str]], changes: list[dict[str, Any]],
                 link: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._settings.smtp_from
        message["To"] = ", ".join(recipients)
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(
            domain=self._settings.smtp_from.rpartition("@")[2].strip("> ") or None)
        notable = " (auffällig)" if targets or changes or report.status != "ok" else ""
        message["Subject"] = " ".join(f"{report.title}{notable}: Stand "
                                      f"{format_time(report.started_at)}".split())[:200]
        lines = [f"{report.title}", f"Stand: {format_time(report.started_at)}", ""]
        if report.status != "ok":
            lines += ["Der Lauf ist nur teilweise gelungen; einzelne Abfragen sind "
                      "gescheitert.", ""]
        if targets:
            lines += ["Zielwerte:"] + [f"- {t['block']}: {t['text']}" for t in targets] + [""]
        if changes:
            since = format_time(changes[0]["previous_started_at"])
            lines += [f"Auffällige Veränderungen seit dem Lauf vom {since}:"]
            lines += [f"- {change_line(c)}" for c in changes] + [""]
        if not targets and not changes:
            lines += ["Keine auffälligen Veränderungen, kein verfehlter Zielwert.", ""]
        attachment: bytes | None = None
        try:
            attachment = render(report, fmt)
        except Exception as exc:  # etwa PDF ohne die Satzbibliothek: dann als HTML
            log.warning("Anhang als %s nicht möglich (%s)", fmt, type(exc).__name__)
            fmt = "html"
            attachment = render(report, fmt)
        if len(attachment) > MAX_ATTACHMENT:
            lines += ["Der Bericht ist für einen Anhang zu groß; er steht in der App.", ""]
            attachment = None
        else:
            lines += [f"Der Bericht hängt als {FORMAT_LABELS.get(fmt, fmt)}-Datei an.", ""]
        if link:
            lines += [f"In der App: {link}"]
        message.set_content("\n".join(lines))
        if attachment is not None:
            media, extension = FORMATS[fmt]
            main, _, sub = media.partition(";")[0].partition("/")
            message.add_attachment(attachment, maintype=main, subtype=sub,
                                   filename=file_name(report.title, report.started_at, extension))
        return message


def _reason(exc: Exception) -> str:
    """Ein kurzer Grund ohne Adressen und Zugangsdaten."""
    if isinstance(exc, RuntimeError) and exc.args:
        return str(exc.args[0])[:120]
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Antwort {exc.response.status_code}"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "Anmeldung am Mailserver abgelehnt"
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "Empfänger abgelehnt"
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return "Zeitüberschreitung"
    if isinstance(exc, OSError | httpx.TransportError | smtplib.SMTPException):
        return "keine Verbindung"
    return type(exc).__name__
