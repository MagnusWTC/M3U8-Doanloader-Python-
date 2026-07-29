from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="M3U8_", extra="ignore")

    data_root: Path = Path("data")
    download_root: Path = Path("downloads")
    max_concurrent_tasks: int = Field(default=2, ge=1, le=8)
    concurrent_fragments: int = Field(default=4, ge=1, le=32)
    fragment_retries: int = Field(default=10, ge=0, le=100)
    task_max_attempts: int = Field(default=3, ge=1, le=10)
    socket_timeout: int = Field(default=30, ge=5, le=300)
    max_upload_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)

    @property
    def work_root(self) -> Path:
        return self.data_root / "work"

    @property
    def log_root(self) -> Path:
        return self.data_root / "logs"

    def ensure_directories(self) -> None:
        for directory in (self.data_root, self.work_root, self.log_root, self.download_root):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
