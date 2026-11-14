"""Groq-only speech adapter. No OpenAI fallback; raw audio never logged."""
import httpx
from .telemetry import measured


class GroqTranscription:
    def __init__(self, key, *, snapshot=None, authority=None):
        self.authority = authority
        self.max_bytes = snapshot.payload["runtime"]["attachment_bytes"] if snapshot else 20 * 1024 * 1024
        self.key = key
        self.http = httpx.Client(
            base_url="https://api.groq.com/openai/v1", timeout=httpx.Timeout(60, connect=1, pool=5),
            limits=httpx.Limits(keepalive_expiry=60),
            headers={"Authorization": "Bearer " + key},
        )
        self.usage = []

    @measured("groq")
    def transcribe(self, mime, raw):
        fence = self.authority.data() if self.authority else {}
        if not self.key:
            raise RuntimeError("groq_nao_configurada")
        # Runtime already caps all media at 20 MiB, below the legacy 23 MB split threshold.
        if len(raw) > self.max_bytes:
            raise ValueError("midia_grande")
        ext = {"audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "mp4",
               "audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm"}[mime]
        r = self.http.post(
            "/audio/transcriptions",
            data={"model": "whisper-large-v3-turbo", "language": "pt", "response_format": "verbose_json"},
            files={"file": ("audio." + ext, raw, mime)},
        )
        if r.status_code != 200:
            raise RuntimeError("groq_status_" + str(r.status_code))
        data = r.json()
        self.usage.append({"duration": data.get("duration"), "model": "whisper-large-v3-turbo",
                           **({"configuration": fence} if fence else {})})
        return data["text"]
