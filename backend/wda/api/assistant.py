"""Der Assistent: Unterhaltung als Strom von Ereignissen (eine JSON-Zeile je Ereignis).

Das Sprachmodell bekommt nur, was auch exportiert werden darf: Felder, Wertelisten und
Zählungen. Es kann nichts speichern; Vorschläge übernimmt der Admin in der Oberfläche."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from ..errors import UnknownInstance, error_body
from ..llm.assistant import converse, system_prompt
from ..llm.tools import Toolbox

router = APIRouter(prefix="/assistant")
log = logging.getLogger(__name__)
MAX_MESSAGES, MAX_CHARS, MAX_MEMO = 30, 4000, 3000


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: Annotated[StrictStr, Field(max_length=MAX_CHARS)]
    # was der Assistent in dieser Antwort vorgeschlagen hat (Name und Definition), damit er
    # sich in der nächsten Runde darauf beziehen kann
    memo: Annotated[StrictStr, Field(max_length=MAX_MEMO)] | None = None


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
    run_id: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None = None
    definition_id: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None = None


@router.post("/chat")
async def chat(body: ChatIn, request: Request) -> Any:
    services = request.app.state.services
    if body.messages[-1].role != "user" or not body.messages[-1].content.strip():
        return JSONResponse(error_body("invalid_request", "Ungültig: 'messages' – die letzte "
                                       "Nachricht muss eine Frage sein."), status_code=422)
    config = services.llm
    if not config.ready:
        return JSONResponse(error_body(
            "llm_not_configured",
            "Für das Sprachmodell ist kein Schlüssel hinterlegt (Einstellungen)."),
            status_code=409)
    try:
        toolbox = Toolbox(services, body.instance, body.run_id,
                          definition_id=None if body.run_id else body.definition_id)
    except KeyError:
        raise UnknownInstance(body.instance) from None
    if body.run_id:
        await services.runner.get_run(body.run_id)  # 404, bevor der Strom beginnt
    elif body.definition_id:
        await services.definitions.get(body.definition_id)
    history: list[dict[str, str]] = []
    for message in body.messages:
        text = message.content
        if message.role == "assistant" and message.memo:
            text += "\n\n[Dem Nutzer gezeigte Vorschläge: " + message.memo + "]"
        history.append({"role": message.role, "content": text})
    zone = ZoneInfo(request.app.state.settings.timezone)
    system = await system_prompt(services, toolbox, datetime.now(zone).strftime("%Y-%m-%d"))

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for event in converse(services.llm_client(config), toolbox, system, history):
                yield (json.dumps(event, ensure_ascii=False, allow_nan=False, default=str)
                       + "\n").encode("utf-8")
        except Exception:  # noqa: BLE001 - der Strom läuft schon; der Fehler wird ein Ereignis
            log.exception("Der Assistent ist an einem unerwarteten Fehler gescheitert")
            yield (json.dumps({"type": "error", "kind": "internal",
                               "detail": "Unerwarteter Fehler im Assistenten."})
                   + "\n").encode("utf-8")

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"X-Accel-Buffering": "no"})
