from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHAT_", env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    database_url: str = (f"sqlite+aiosqlite:///{(PROJECT_ROOT / 'data' / 'chat.db').as_posix()}")

    session_ttl_seconds: int = Field(default=86400, ge=60)
    cookie_secure: bool = False
    allowed_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173", "http://127.0.0.1:5173"]
    frontend_dir: Path = PROJECT_ROOT / "frontend" / "dist"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


    @field_validator("database_url")
    @classmethod
    def sqlite_url(cls, value: str) -> str:
        url = make_url(value)
        if url.drivername != "sqlite+aiosqlite":
            raise ValueError("Use a sqlite+aiosqlite database URL")
            
        if url.database and url.database != ":memory:":
            path = Path(url.database)
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            url = url.set(database=path.resolve().as_posix())
        return url.render_as_string(hide_password=False)

    @property
    def database_path(self) -> Path | None:
        value = make_url(self.database_url).database
        return Path(value) if value and value != ":memory:" else None
