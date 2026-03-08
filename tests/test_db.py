from __future__ import annotations

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

            first = db.resolve_entry_for_exercise(exercise, now=morning)
            second = db.resolve_entry_for_exercise(exercise, now=evening)

            self.assertEqual(first.id, second.id)
            db.close()

    def test_session_creates_new_day_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            exercise = build_exercise("part1/session.md", "long-term", "session")
            day_one = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
            day_two = datetime(2026, 3, 9, 9, 0, tzinfo=UTC)

            first = db.resolve_entry_for_exercise(exercise, now=day_one)
            second = db.resolve_entry_for_exercise(exercise, now=day_two)

            self.assertNotEqual(first.id, second.id)
            db.close()

    def test_project_reuses_latest_entry_across_days(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "progress.db")
            exercise = build_exercise("part1/project.md", "long-term", "project")
            day_one = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
            day_two = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)

            first = db.resolve_entry_for_exercise(exercise, now=day_one)
            second = db.resolve_entry_for_exercise(exercise, now=day_two)

            self.assertEqual(first.id, second.id)
            db.close()


if __name__ == "__main__":
    unittest.main()
