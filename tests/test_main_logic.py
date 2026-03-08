from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gbtw.content import load_content_index
from gbtw.db import Database

try:
    from gbtw.main import GBTWApp

    HAS_TEXTUAL = True
except ModuleNotFoundError:
    GBTWApp = object  # type: ignore[assignment]
    HAS_TEXTUAL = False


class FakeEditor:
    def __init__(self) -> None:
        self.text = ""
        self.disabled = False
        self.focused = False

    def load_text(self, text: str) -> None:
        self.text = text

    def focus(self) -> None:
        self.focused = True


class HarnessApp(GBTWApp):
    def __init__(self, *, database: Database, content_root: Path) -> None:
        super().__init__(
            database=database,
            content_index=load_content_index(content_root),
            autosave_delay_seconds=0.01,
            sprint_tick_seconds=0.01,
        )
        self.editor = FakeEditor()
        self.focus_target: str | None = None
        self.markdown_text = ""
        self.bottom_bar_updates = 0
        self.word_count_value = 0
        self.bell_count = 0
        self.pending_tasks: list[asyncio.Task[object]] = []

    def query_one(self, selector: str, expected_type=None):  # type: ignore[override]
        if selector == "#editor":
            return self.editor
        raise AssertionError(f"unexpected selector {selector}")

    async def _update_exercise_markdown(self, markdown_text: str) -> None:
        self.markdown_text = markdown_text

    def _update_bottom_bar(self) -> None:
        self.bottom_bar_updates += 1
        self.word_count_value = len([word for word in self.editor.text.split() if word.strip()])

    def _update_word_count(self) -> None:
        self.word_count_value = len([word for word in self.editor.text.split() if word.strip()])

    def _set_save_indicator(self, text: str, state: str) -> None:
        self._last_save_message = text
        self._save_indicator_state = state

    def _focus_editor(self) -> None:
        self.focus_target = "editor"
        self.editor.focus()

    def _focus_exercise(self) -> None:
        self.focus_target = "exercise"

    def _apply_layout(self) -> None:
        self.bottom_bar_updates += 1

    def set_timer(self, *args, **kwargs):  # type: ignore[override]
        return None

    def set_interval(self, *args, **kwargs):  # type: ignore[override]
        return None

    def bell(self) -> None:
        self.bell_count += 1

    def run_worker(self, work, *args, **kwargs):  # type: ignore[override]
        task = asyncio.create_task(work)
        self.pending_tasks.append(task)
        return task

    async def wait_for_workers(self) -> None:
        if not self.pending_tasks:
            return
        tasks = list(self.pending_tasks)
        self.pending_tasks.clear()
        await asyncio.gather(*tasks)


@unittest.skipUnless(HAS_TEXTUAL, "Textual is not installed")
class MainLogicTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_restore_stays_clean_until_user_types(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "a.md").write_text(
                """---
title: A
part: 1
module: M
type: exercise
---

Body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/a.md", save_current=False)
            self.assertEqual(app._save_indicator_state, "saved")
            self.assertEqual(app._ignored_loaded_text, "")

            app.on_editor_changed(None)  # type: ignore[arg-type]
            self.assertEqual(app._save_indicator_state, "saved")
            self.assertIsNone(app._ignored_loaded_text)

            app.editor.text = "hello world"
            app.on_editor_changed(None)  # type: ignore[arg-type]
            self.assertEqual(app._save_indicator_state, "unsaved")
            self.assertEqual(app.word_count_value, 2)
            db.close()

    async def test_autosave_updates_current_row_without_creating_new_one(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "a.md").write_text(
                """---
title: A
part: 1
module: M
type: exercise
---

Body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/a.md", save_current=False)
            first_entry_id = app.current_entry_id
            app.editor.text = "hello world"
            app._set_save_indicator("Unsaved •", "unsaved")
            app._autosave_if_current(app._autosave_generation)
            await app.wait_for_workers()

            self.assertEqual(app.current_entry_id, first_entry_id)
            self.assertEqual(len(db.list_history("part1/a.md")), 1)
            self.assertEqual(db.get_latest_entry("part1/a.md").content, "hello world")
            self.assertEqual(app._save_indicator_state, "saved")
            db.close()

    async def test_switching_exercises_saves_dirty_text_and_restores_target_draft(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "a.md").write_text(
                """---
title: First
part: 1
module: M
type: exercise
---

Body A
""",
                encoding="utf-8",
            )
            (root / "part1" / "b.md").write_text(
                """---
title: Second
part: 1
module: M
type: exercise
---

Body B
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/a.md", save_current=False)
            app.editor.text = "draft a"
            app._set_save_indicator("Unsaved •", "unsaved")

            await app._open_exercise_by_id("part1/b.md")
            self.assertEqual(db.get_latest_entry("part1/a.md").content, "draft a")
            self.assertEqual(app.current_exercise.exercise_id, "part1/b.md")
            self.assertEqual(app.editor.text, "")

            app.editor.text = "draft b"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._open_exercise_by_id("part1/a.md")

            self.assertEqual(db.get_latest_entry("part1/b.md").content, "draft b")
            self.assertEqual(app.current_exercise.exercise_id, "part1/a.md")
            self.assertEqual(app.editor.text, "draft a")
            db.close()

    async def test_ctrl_j_creates_new_row_only_for_session_mode(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "session.md").write_text(
                """---
title: Session
part: 1
module: M
type: long-term
save_mode: session
---

Body
""",
                encoding="utf-8",
            )
            (root / "part1" / "project.md").write_text(
                """---
title: Project
part: 1
module: M
type: long-term
save_mode: project
---

Body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/session.md", save_current=False)
            first_session_entry = app.current_entry_id
            await app.action_new_session_now()

            self.assertNotEqual(app.current_entry_id, first_session_entry)
            self.assertEqual(len(db.list_history("part1/session.md")), 2)
            self.assertEqual(app.editor.text, "")

            await app._open_exercise_by_id("part1/project.md", save_current=False)
            first_project_entry = app.current_entry_id
            await app.action_new_session_now()

            self.assertEqual(app.current_entry_id, first_project_entry)
            self.assertEqual(len(db.list_history("part1/project.md")), 1)
            self.assertEqual(app.bell_count, 1)
            db.close()

    async def test_project_mode_reopens_same_draft_row(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "exercise.md").write_text(
                """---
title: Exercise
part: 1
module: M
type: exercise
---

Body
""",
                encoding="utf-8",
            )
            (root / "part1" / "project.md").write_text(
                """---
title: Project
part: 1
module: M
type: long-term
save_mode: project
---

Body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/project.md", save_current=False)
            first_project_entry = app.current_entry_id
            app.editor.text = "ongoing draft"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")

            await app._open_exercise_by_id("part1/exercise.md")
            await app._open_exercise_by_id("part1/project.md")

            self.assertEqual(app.current_entry_id, first_project_entry)
            self.assertEqual(app.editor.text, "ongoing draft")
            self.assertEqual(len(db.list_history("part1/project.md")), 1)
            db.close()

    async def test_layout_and_split_changes_persist_preferences(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "a.md").write_text(
                """---
title: A
part: 1
module: M
type: exercise
---

Body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app.action_set_mode("read")
            await app.action_set_mode("stack")
            await app.action_increase_split()
            await app.action_decrease_split()

            self.assertEqual(app.current_layout_mode, "stack")
            self.assertEqual(app.side_ratio_index, 1)
            self.assertEqual(db.get_preference("last_mode"), "stack")
            self.assertEqual(db.get_preference("last_split_ratio"), "50/50")
            db.close()

    async def test_reading_exercises_disable_editor_layouts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "reading.md").write_text(
                """---
title: Reading
part: 1
module: M
type: reading
---

Body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)
            app.current_layout_mode = "side"

            await app._open_exercise_by_id("part1/reading.md", save_current=False)

            self.assertTrue(app.editor.disabled)
            self.assertEqual(app._effective_layout_mode(), "read")
            self.assertEqual(app.focus_target, "exercise")

            await app.action_set_mode("write")

            self.assertEqual(app.current_layout_mode, "side")
            self.assertEqual(app.bell_count, 1)
            db.close()

    async def test_writing_exercises_reenable_editor_layouts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "reading.md").write_text(
                """---
title: Reading
part: 1
module: M
type: reading
---

Body
""",
                encoding="utf-8",
            )
            (root / "part1" / "exercise.md").write_text(
                """---
title: Exercise
part: 1
module: M
type: exercise
---

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/reading.md", save_current=False)
            await app._open_exercise_by_id("part1/exercise.md", save_current=False)
            await app.action_set_mode("write")

            self.assertFalse(app.editor.disabled)
            self.assertEqual(app.current_layout_mode, "write")
            self.assertEqual(app._effective_layout_mode(), "write")
            self.assertEqual(app.focus_target, "editor")
            db.close()


if __name__ == "__main__":
    unittest.main()
