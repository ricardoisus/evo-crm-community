"""Groq-only speech adapter. No OpenAI fallback; raw audio never logged."""
import httpx


class GroqTranscription:
    def __init__(self, key):
        self.key = key
        self.http = httpx.Client(
            base_url="https://api.groq.com/openai/v1", timeout=httpx.Timeout(60, connect=1, pool=5),
            limits=httpx.Limits(keepalive_expiry=60),
            headers={"Authorization": "Bearer " + key},
        )
        self.usage = []

    def transcribe(self, mime, raw):
        if not self.key:
            raise RuntimeError("groq_nao_configurada")
        # Runtime already caps all media at 20 MiB, below the legacy 23 MB split threshold.
        if len(raw) > 20 * 1024 * 1024:
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
        self.usage.append({"duration": data.get("duration"), "model": "whisper-large-v3-turbo"})
        return data["text"]
