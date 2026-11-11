"""The HTTP API: voice key setup, account view, and WebRTC call signalling."""

from __future__ import annotations

# Imported first: it sets up certificates and model paths before anything else loads.
from .config import settings  # isort: skip

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from loguru import logger
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from .agent import run_call
from .bank import Bank
from .voice_key import VoiceKey

SENTENCES = (
    "Seven bright kites drifted over the harbour while the evening ferry pulled away. "
    "The gulls followed it past the old lighthouse.",
    "My voice is my own, and I am setting up this key on a quiet afternoon. "
    "Nobody else can say these words for me.",
    "Please check the river bridge and the old clock tower before the market closes. "
    "Then bring the blue boxes back to the station.",
)

voice_key = VoiceKey(settings)
bank = Bank()
signalling = SmallWebRTCRequestHandler()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The models take a few seconds to load. The page shows when they're ready.
    threading.Thread(target=voice_key.load, daemon=True).start()
    yield
    await signalling.close()


app = FastAPI(title="Prove It", lifespan=lifespan)


@app.get("/api/status")
def status() -> dict[str, Any]:
    return {
        "models_ready": voice_key.ready,
        "models_error": voice_key.error,
        "can_call": settings.can_call,
        "has_voice_key": voice_key.exists,
        "limits": {"speaker_min": settings.speaker_min, "spoof_max": settings.spoof_max},
    }


@app.get("/api/account")
def account() -> dict[str, Any]:
    return bank.snapshot()


@app.get("/api/voice-key")
def voice_key_info() -> dict[str, Any]:
    return {"exists": voice_key.exists, "sentences": list(SENTENCES)}


@app.post("/api/voice-key")
async def create_voice_key(clips: list[UploadFile] = File(...)) -> dict[str, Any]:
    if not voice_key.ready:
        raise HTTPException(503, "The voice models are still loading.")
    if len(clips) != len(SENTENCES):
        raise HTTPException(400, f"Send one recording per sentence ({len(SENTENCES)}).")
    audio = [await clip.read() for clip in clips]
    try:
        return await asyncio.to_thread(voice_key.enrol, audio)
    except Exception as exc:  # noqa: BLE001
        logger.exception("voice key setup failed")
        raise HTTPException(400, f"Couldn't read those recordings: {exc}") from exc


@app.delete("/api/voice-key")
def delete_voice_key() -> dict[str, str]:
    voice_key.delete()
    return {"status": "deleted"}


@app.post("/api/offer")
async def offer(request: Request, tasks: BackgroundTasks) -> dict[str, Any]:
    webrtc = SmallWebRTCRequest.from_dict(await request.json())
    is_new_call = webrtc.pc_id is None  # otherwise it's a renegotiation of a running call
    if is_new_call and not settings.can_call:
        raise HTTPException(503, "Calls need OPENAI_API_KEY, CARTESIA_API_KEY and CARTESIA_VOICE_ID in .env")
    if is_new_call and not voice_key.ready:
        raise HTTPException(503, "The voice models are still loading.")

    async def on_connection(connection: SmallWebRTCConnection) -> None:
        if is_new_call:
            tasks.add_task(run_call, connection, settings, bank, voice_key)

    answer = await signalling.handle_web_request(webrtc, on_connection)
    return answer or {}


@app.patch("/api/offer")
async def ice(request: Request) -> dict[str, str]:
    body = await request.json()
    await signalling.handle_patch_request(
        SmallWebRTCPatchRequest(
            pc_id=body["pc_id"],
            candidates=[
                IceCandidate(
                    candidate=item["candidate"],
                    sdp_mid=item["sdp_mid"],
                    sdp_mline_index=item["sdp_mline_index"],
                )
                for item in body.get("candidates", [])
            ],
        )
    )
    return {"status": "ok"}
