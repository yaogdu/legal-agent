from __future__ import annotations

from pathlib import Path

from legal_agent.core.config import Settings

from .connection import apply_sql_file


def migrate(settings: Settings) -> None:
    root = Path(__file__).resolve().parents[3]
    apply_sql_file(settings, root / "migrations" / "001_initial.sql")
