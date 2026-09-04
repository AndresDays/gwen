import httpx


class ElevenLabsVoice:
    def __init__(self, api_key: str, voice_id: str, tts_model: str, stt_model: str) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.tts_model = tts_model
        self.stt_model = stt_model
        self.base_url = "https://api.elevenlabs.io/v1"

    async def transcribe(self, audio: bytes, filename: str = "voice.ogg") -> str:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/speech-to-text",
                headers={"xi-api-key": self.api_key},
                data={"model_id": self.stt_model},
                files={"file": (filename, audio, "audio/ogg")},
            )
            response.raise_for_status()
            return response.json()["text"].strip()

    async def synthesize(self, text: str) -> bytes:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"{self.base_url}/text-to-speech/{self.voice_id}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": self.api_key, "Accept": "audio/mpeg"},
                json={"text": text, "model_id": self.tts_model},
            )
            response.raise_for_status()
            return response.content
