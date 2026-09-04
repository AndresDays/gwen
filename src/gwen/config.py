import json
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = "development"
    telegram_bot_token: str
    telegram_allowed_user_id: int
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-4-5"
    database_url: str = "sqlite+aiosqlite:///./gwen.db"
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_tts_model: str = "eleven_multilingual_v2"
    elevenlabs_web_tts_model: str = "eleven_flash_v2_5"
    elevenlabs_stt_model: str = "scribe_v1"
    elevenlabs_web_stt_model: str = "scribe_v2"
    history_limit: int = Field(default=20, ge=2, le=100)
    max_input_chars: int = Field(default=12_000, ge=500, le=100_000)
    daily_token_limit: int = Field(default=100_000, ge=1_000)
    daily_token_limit_enabled: bool = False
    web_remote_enabled: bool = False
    web_password_hash: str | None = None
    web_session_secret: str | None = None
    web_public_origin: str = "http://127.0.0.1:8765"
    backup_retention_days: int = Field(default=14, ge=1, le=365)
    daily_voice_seconds_limit: int = Field(default=900, ge=60)
    daily_tts_character_limit: int = Field(default=20_000, ge=100)

    @property
    def voice_enabled(self) -> bool:
        return bool(self.elevenlabs_api_key and self.elevenlabs_voice_id)


@lru_cache
def get_settings() -> Settings:
    auth_path = Path(".gwen-web-auth.json")
    overrides: dict[str, object] = {}
    if auth_path.is_file():
        stored = json.loads(auth_path.read_text(encoding="utf-8"))
        allowed = {
            "web_remote_enabled",
            "web_password_hash",
            "web_session_secret",
            "web_public_origin",
        }
        overrides = {key: value for key, value in stored.items() if key in allowed}
    return Settings(**overrides)  # type: ignore[call-arg]
