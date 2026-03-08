from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gbtw.content import Exercise
from gbtw.db import Database


def build_exercise(exercise_id: str, exercise_type: str, save_mode: str | None = None) -> Exercise:
    return Exercise(
        exercise_id=exercise_id,
        source_path=Path(f"/tmp/{exercise_id}"),
        title="Title",
        part=1,
        module="Module",
        type=exercise_type,
        status="active",
        save_mode=save_mode,
        body="Body",
        guided_questions=(),
        project_key=None,
        project_title=None,
        project_seed=False,
    )


class DatabaseTests(unittest.TestCase):
    def test_preferences_round_trip(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            db.set_preference("last_mode", "side")
            self.assertEqual(db.get_preference("last_mode"), "side")
            db.close()

    def test_session_reuses_same_day_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            exercise = build_exercise("part1/session.md", "long-term", "session")
            morning = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
            evening = datetime(2026, 3, 8, 20, 0, tzinfo=UTC)

            first = db.resolve_entry_for_exercise(exercise, "freewrite", now=morning)
            second = db.resolve_entry_for_exercise(exercise, "freewrite", now=evening)

            self.assertEqual(first.id, second.id)
            db.close()

    def test_session_creates_new_day_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            exercise = build_exercise("part1/session.md", "long-term", "session")
            day_one = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
            day_two = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)

            first = db.resolve_entry_for_exercise(exercise, "freewrite", now=day_one)
            second = db.resolve_entry_for_exercise(exercise, "freewrite", now=day_two)

            self.assertNotEqual(first.id, second.id)
            db.close()

    def test_project_reuses_latest_entry_across_days(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            exercise = build_exercise("part1/project.md", "long-term", "project")
            day_one = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
            day_two = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)

            first = db.resolve_entry_for_exercise(exercise, "freewrite", now=day_one)
            second = db.resolve_entry_for_exercise(exercise, "freewrite", now=day_two)

            self.assertEqual(first.id, second.id)
            db.close()

    def test_draft_kinds_are_separate_for_same_exercise(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            exercise = build_exercise("part1/project.md", "long-term", "project")
            now = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)

            freewrite = db.resolve_entry_for_exercise(exercise, "freewrite", now=now)
            guided = db.resolve_entry_for_exercise(exercise, "exercise", now=now)

            self.assertNotEqual(freewrite.id, guided.id)
            self.assertEqual(db.get_latest_entry("part1/project.md", "freewrite").id, freewrite.id)
            self.assertEqual(db.get_latest_entry("part1/project.md", "exercise").id, guided.id)
            db.close()

    def test_delete_entry_removes_row(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            exercise = build_exercise("part1/a.md", "exercise")
            entry = db.resolve_entry_for_exercise(exercise, "freewrite")

            db.delete_entry(entry.id)

            self.assertEqual(db.list_history("part1/a.md", "freewrite"), [])
            with self.assertRaises(KeyError):
                db.get_entry_by_id(entry.id)
            db.close()

    def test_migrates_legacy_rows_to_freewrite(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "progress.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE entries(
                  id INTEGER PRIMARY KEY,
                  exercise_id TEXT NOT NULL,
                  created_at TIMESTAMP NOT NULL,
                  updated_at TIMESTAMP NOT NULL,
                  content TEXT NOT NULL
                );

                CREATE TABLE preferences(
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                """
            )
            timestamp = datetime(2026, 3, 8, 9, 0, tzinfo=UTC).isoformat()
            connection.execute(
                """
                INSERT INTO entries(exercise_id, created_at, updated_at, content)
                VALUES(?, ?, ?, ?)
                """,
                ("part1/legacy.md", timestamp, timestamp, "legacy draft"),
            )
            connection.commit()
            connection.close()

            db = Database(path)
            legacy = db.get_latest_entry("part1/legacy.md", "freewrite")

            self.assertIsNotNone(legacy)
            assert legacy is not None
            self.assertEqual(legacy.draft_kind, "freewrite")
            self.assertEqual(legacy.content, "legacy draft")
            self.assertEqual(db.list_history("part1/legacy.md", "exercise"), [])
            db.close()

    def test_project_entries_reopen_same_row(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")

            first = db.resolve_project_entry("part2-novel")
            second = db.resolve_project_entry("part2-novel")

            self.assertEqual(first.id, second.id)
            db.close()

    def test_project_history_is_separate_by_project_key(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            first = db.create_project_entry("part2-novel", "draft one")
            second = db.create_project_entry("part4-portfolio", "draft two")

            self.assertEqual(db.list_project_history("part2-novel")[0].id, first.id)
            self.assertEqual(db.list_project_history("part4-portfolio")[0].id, second.id)
            db.close()

    def test_seed_project_entries_copies_freewrite_history(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            older = db.create_entry(
                "part2/p2-10-sustaining-novel.md",
                "freewrite",
                "early draft",
                now=datetime(2026, 3, 8, 9, 0, tzinfo=UTC),
            )
            newer = db.create_entry(
                "part2/p2-10-sustaining-novel.md",
                "freewrite",
                "later draft",
                now=datetime(2026, 3, 9, 9, 0, tzinfo=UTC),
            )

            seeded = db.seed_project_entries(
                "part2-novel",
                [newer, older],
            )

            self.assertEqual([entry.content for entry in seeded], ["later draft", "early draft"])
            self.assertEqual(db.list_history("part2/p2-10-sustaining-novel.md", "freewrite")[0].content, "later draft")
            db.close()


if __name__ == "__main__":
    unittest.main()
