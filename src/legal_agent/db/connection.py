from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from legal_agent.core.config import Settings


@contextmanager
def connect(settings: Settings) -> Iterator[psycopg.Connection[Any]]:
    conn = psycopg.connect(settings.database_dsn, row_factory=dict_row)
    try:
        yield conn
    finally:
        conn.close()


def apply_sql_file(settings: Settings, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    with connect(settings) as conn:
        with conn.transaction():
            conn.execute(sql)
