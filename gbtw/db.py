from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .content import Exercise

APP_DATA_DIR = Path.home() / ".local" / "share" / "gbtw"
DB_PATH = APP_DATA_DIR / "progress.db"


@dataclass(slots=True, frozen=True)
class EntryRecord:
    id: int
    exercise_id: str
    created_at: datetime
    updated_at: datetime
    content: str


class Database:
    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries(
              id INTEGER PRIMARY KEY,
              exercise_id TEXT NOT NULL,
              created_at TIMESTAMP NOT NULL,
              updated_at TIMESTAMP NOT NULL,
              content TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS preferences(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def get_preference(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM preferences WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_preference(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO preferences(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.connection.commit()

    def resolve_entry_for_exercise(
        self,
        exercise: Exercise,
        *,
        now: datetime | None = None,
    ) -> EntryRecord:
        now = now or datetime.now().astimezone()
        if exercise.is_long_term:
            if exercise.save_mode == "session":
                record = self.get_latest_entry_for_local_day(exercise.exercise_id, now.date())
                if record is not None:
                    return record
                return self.create_entry(exercise.exercise_id, "", now=now)
            record = self.get_latest_entry(exercise.exercise_id)
            if record is not None:
                return record
            return self.create_entry(exercise.exercise_id, "", now=now)
        record = self.get_latest_entry(exercise.exercise_id)
        if record is not None:
            return record
        return self.create_entry(exercise.exercise_id, "", now=now)

    def create_entry(
        self,
        exercise_id: str,
        content: str,
        *,
        now: datetime | None = None,
    ) -> EntryRecord:
        now = _normalize_datetime(now)
        cursor = self.connection.execute(
            """
            INSERT INTO entries(exercise_id, created_at, updated_at, content)
            VALUES(?, ?, ?, ?)
            """,
            (exercise_id, now.isoformat(), now.isoformat(), content),
        )
        self.connection.commit()
        return self.get_entry_by_id(int(cursor.lastrowid))

    def update_entry(
        self,
        entry_id: int,
        content: str,
        *,
        now: datetime | None = None,
    ) -> EntryRecord:
        now = _normalize_datetime(now)
        self.connection.execute(
            "UPDATE entries SET content = ?, updated_at = ? WHERE id = ?",
            (content, now.isoformat(), entry_id),
        )
        self.connection.commit()
        return self.get_entry_by_id(entry_id)

    def get_entry_by_id(self, entry_id: int) -> EntryRecord:
        row = self.connection.execute(
            """
            SELECT id, exercise_id, created_at, updated_at, content
            FROM entries
            WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"entry {entry_id} not found")
        return _row_to_entry(row)

    def get_latest_entry(self, exercise_id: str) -> EntryRecord | None:
        row = self.connection.execute(
            """
            SELECT id, exercise_id, created_at, updated_at, content
            FROM entries
            WHERE exercise_id = ?
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT 1
            """,
            (exercise_id,),
        ).fetchone()
        return None if row is None else _row_to_entry(row)

    def get_latest_entry_for_local_day(self, exercise_id: str, local_day: date) -> EntryRecord | None:
        rows = self.connection.execute(
            """
            SELECT id, exercise_id, created_at, updated_at, content
            FROM entries
            WHERE exercise_id = ?
            ORDER BY datetime(updated_at) DESC, id DESC
            """,
            (exercise_id,),
        ).fetchall()
        for row in rows:
            entry = _row_to_entry(row)
            if entry.created_at.astimezone().date() == local_day:
                return entry
        return None

    def list_history(self, exercise_id: str) -> list[EntryRecord]:
        rows = self.connection.execute(
            """
            SELECT id, exercise_id, created_at, updated_at, content
            FROM entries
            WHERE exercise_id = ?
            ORDER BY datetime(updated_at) DESC, id DESC
            """,
            (exercise_id,),
        ).fetchall()
        return [_row_to_entry(row) for row in rows]


def _normalize_datetime(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).astimezone()
    return value.astimezone()


def _row_to_entry(row: sqlite3.Row) -> EntryRecord:
    return EntryRecord(
        id=int(row["id"]),
        exercise_id=str(row["exercise_id"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        content=str(row["content"]),
    )
