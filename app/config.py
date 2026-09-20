"""환경 변수 로딩 및 앱 설정."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    law_api_oc: str | None
    openai_api_key: str | None
    openai_model: str
    supabase_url: str | None
    supabase_key: str | None


def get_settings() -> Settings:
    return Settings(
        law_api_oc=os.getenv("LAW_API_OC"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        supabase_url=os.getenv("SUPABASE_URL"),
        supabase_key=os.getenv("SUPABASE_KEY"),
    )


def missing_credentials(settings: Settings) -> list[str]:
    missing = []
    if not settings.law_api_oc:
        missing.append("LAW_API_OC")
    if not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    return missing
