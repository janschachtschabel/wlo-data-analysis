"""Aufruf eines OpenAI-kompatiblen Endpunkts (``/chat/completions``). Der Schlüssel reist nur
als Bearer an die eingestellte Adresse und kommt in keiner Meldung vor."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .config import LlmConfig

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
KINDS = ("llm_not_configured", "llm_auth", "llm_rejected", "llm_unreachable", "llm_bad_answer")


class LlmError(Exception):
    """``message`` ist für die Oberfläche bestimmt."""

    def __init__(self, kind: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.kind, self.message, self.status = kind, message, status


class LlmClient:
    def __init__(self, http: httpx.AsyncClient, config: LlmConfig, timeout: float = 120.0
                 ) -> None:
        self._http, self.config, self._timeout = http, config, timeout

    async def chat(self, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Die Antwortnachricht des Modells (``content`` und/oder ``tool_calls``)."""
        if not self.config.ready:
            raise LlmError("llm_not_configured",
                           "Für das Sprachmodell ist kein Schlüssel hinterlegt (Einstellungen).")
        body: dict[str, Any] = {"model": self.config.model, "messages": messages}
        if tools:
            body["tools"] = tools
        effort = self.config.reasoning_effort
        if effort:
            body["reasoning_effort"] = effort
        try:
            data = await self._post(body)
        except LlmError as exc:
            # Nicht jedes Modell kennt den Parameter; dann einmal ohne ihn.
            if not (effort and exc.status == 400 and "reasoning" in exc.message.lower()):
                raise
            del body["reasoning_effort"]
            data = await self._post(body)
        try:
            message = data["choices"][0]["message"]
            content = message.get("content")
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list) or not (content is None or isinstance(content, str)):
                raise TypeError
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LlmError("llm_bad_answer",
                           "Das Sprachmodell hat in einem unerwarteten Format geantwortet."
                           ) from None
        return {"role": "assistant", "content": content, "tool_calls": calls}

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        url = f"{self.config.base_url}/chat/completions"
        try:
            async with self._http.stream("POST", url, json=body, headers=headers,
                                         timeout=self._timeout) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw += chunk
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise LlmError("llm_bad_answer",
                                       "Die Antwort des Sprachmodells ist zu groß.")
                status = response.status_code
        except httpx.TimeoutException:
            raise LlmError("llm_unreachable",
                           "Das Sprachmodell hat nicht rechtzeitig geantwortet.") from None
        except httpx.HTTPError:
            raise LlmError("llm_unreachable",
                           "Das Sprachmodell ist unter der eingestellten Adresse nicht "
                           "erreichbar.") from None
        if status in (401, 403):
            raise LlmError("llm_auth", "Der Anbieter des Sprachmodells lehnt den Schlüssel ab.",
                           status)
        if 300 <= status < 400:
            raise LlmError("llm_unreachable",
                           "Die eingestellte Adresse leitet weiter; bitte die Basis-URL "
                           "prüfen.", status)
        if status >= 500 or status == 429:
            raise LlmError("llm_unreachable",
                           f"Der Anbieter des Sprachmodells meldet einen Fehler ({status}"
                           f"{': zu viele Anfragen' if status == 429 else ''}).", status)
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if status >= 400:
            raise LlmError("llm_rejected",
                           "Der Anbieter lehnt die Anfrage ab: " + self._reason(data, status),
                           status)
        if not isinstance(data, dict):
            raise LlmError("llm_bad_answer",
                           "Unter der eingestellten Adresse antwortet kein Sprachmodell.")
        return data

    def _reason(self, data: Any, status: int) -> str:
        text = ""
        if isinstance(data, dict):
            error = data.get("error")
            text = (error.get("message") if isinstance(error, dict) else error) or \
                data.get("message") or ""
        text = " ".join(str(text).split()) if isinstance(text, str) else ""
        if self.config.api_key:  # erst schwärzen, dann kürzen: kein Rest an der Schnittkante
            text = text.replace(self.config.api_key, "…")
        text = text[:300]
        return text or f"Status {status}"
