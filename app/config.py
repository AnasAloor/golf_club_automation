from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="GCA_",
        extra="ignore",
    )

    club_caddie_executable: Path = Field(
        default=Path(r"C:\Program Files\Club Caddie GMS, Inc\Club Caddie GMS\POSApp.exe")
    )
    club_code: str = "CC18"
    username: str = "ashwin"
    password: str = "0000"
    automation_timeout_seconds: int = 30
    window_wait_seconds: int = 60
    tesseract_executable: Path = Field(
        default=Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
