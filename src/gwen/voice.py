import httpx


class ElevenLabsVoice:
    def __init__(
        self,
        api_key: str,
        voice_id: str,
        tts_model: str,
        stt_model: str,
        output_format: str = "mp3_44100_128",
    ) -> None:
        self.api_key = api_key
        self.voice_id = voice_id
        self.tts_model = tts_model
        self.stt_model = stt_model
        self.output_format = output_format
        self.base_url = "https://api.elevenlabs.io/v1"
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))

    async def transcribe(
        self, audio: bytes, filename: str = "voice.ogg", content_type: str = "audio/ogg"
    ) -> str:
        response = await self.client.post(
            f"{self.base_url}/speech-to-text",
            headers={"xi-api-key": self.api_key},
            data={"model_id": self.stt_model},
            files={"file": (filename, audio, content_type)},
        )
        response.raise_for_status()
        return response.json()["text"].strip()

    async def synthesize(self, text: str) -> bytes:
        response = await self.client.post(
            f"{self.base_url}/text-to-speech/{self.voice_id}",
            params={"output_format": self.output_format},
            headers={"xi-api-key": self.api_key, "Accept": "audio/mpeg"},
            json={"text": text, "model_id": self.tts_model},
        )
        response.raise_for_status()
        return response.content

    async def aclose(self) -> None:
        await self.client.aclose()
