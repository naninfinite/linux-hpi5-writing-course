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
        self.pushed_screen = None
        self.draft_toolbar_visible = False
        self.draft_label = ""
        self.draft_previous_disabled = True
        self.draft_next_disabled = True

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

    def _update_draft_controls(self) -> None:
        visible = self.current_exercise is not None and self._can_manage_freewrite_drafts()
        self.draft_toolbar_visible = visible
        if not visible:
            self.draft_label = ""
            self.draft_previous_disabled = True
            self.draft_next_disabled = True
            return
        entries = self._current_freewrite_entries()
        count = len(entries)
        current_index = next((index for index, entry in enumerate(entries) if entry.id == self.current_entry_id), 0)
        current_entry = entries[current_index] if entries else None
        if current_entry is None:
            self.draft_label = "Draft 0 of 0"
        else:
            timestamp = current_entry.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
            self.draft_label = f"Draft {current_index + 1} of {count}  {timestamp}"
        self.draft_previous_disabled = count <= 1
        self.draft_next_disabled = count <= 1

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

    def push_screen(self, screen, callback=None, *args, **kwargs):  # type: ignore[override]
        self.pushed_screen = screen
        return None

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
            self.assertEqual(len(db.list_history("part1/a.md", "freewrite")), 1)
            self.assertEqual(db.get_latest_entry("part1/a.md", "freewrite").content, "hello world")
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
            self.assertEqual(db.get_latest_entry("part1/a.md", "freewrite").content, "draft a")
            self.assertEqual(app.current_exercise.exercise_id, "part1/b.md")
            self.assertEqual(app.editor.text, "")

            app.editor.text = "draft b"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._open_exercise_by_id("part1/a.md")

            self.assertEqual(db.get_latest_entry("part1/b.md", "freewrite").content, "draft b")
            self.assertEqual(app.current_exercise.exercise_id, "part1/a.md")
            self.assertEqual(app.editor.text, "draft a")
            db.close()

    async def test_freewrite_drafts_can_be_created_and_cycled(self) -> None:
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
            app.editor.text = "draft one"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")
            first_entry_id = app.current_entry_id

            await app.action_new_draft()
            second_entry_id = app.current_entry_id

            self.assertNotEqual(second_entry_id, first_entry_id)
            self.assertEqual(app.editor.text, "")
            self.assertEqual(len(db.list_history("part1/a.md", "freewrite")), 2)
            self.assertTrue(app.draft_toolbar_visible)
            self.assertTrue(app.draft_label.startswith("Draft 1 of 2"))
            self.assertFalse(app.draft_previous_disabled)
            self.assertFalse(app.draft_next_disabled)

            app.editor.text = "draft two"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app.action_previous_draft()

            self.assertEqual(db.get_entry_by_id(second_entry_id).content, "draft two")
            self.assertEqual(app.current_entry_id, first_entry_id)
            self.assertEqual(app.editor.text, "draft one")

            await app.action_next_draft()

            self.assertEqual(app.current_entry_id, second_entry_id)
            self.assertEqual(app.editor.text, "draft two")
            db.close()

    async def test_reopening_exercise_restores_selected_freewrite_draft(self) -> None:
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

Body A
""",
                encoding="utf-8",
            )
            (root / "part1" / "b.md").write_text(
                """---
title: B
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
            app.editor.text = "draft one"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")
            first_entry_id = app.current_entry_id

            await app.action_new_draft()
            app.editor.text = "draft two"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")

            await app.action_previous_draft()
            self.assertEqual(app.current_entry_id, first_entry_id)
            self.assertEqual(app.editor.text, "draft one")

            await app._open_exercise_by_id("part1/b.md")
            await app._open_exercise_by_id("part1/a.md")

            self.assertEqual(app.current_entry_id, first_entry_id)
            self.assertEqual(app.editor.text, "draft one")
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
            self.assertEqual(len(db.list_history("part1/session.md", "freewrite")), 2)
            self.assertEqual(app.editor.text, "")

            await app._open_exercise_by_id("part1/project.md", save_current=False)
            first_project_entry = app.current_entry_id
            await app.action_new_session_now()

            self.assertEqual(app.current_entry_id, first_project_entry)
            self.assertEqual(len(db.list_history("part1/project.md", "freewrite")), 1)
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
            self.assertEqual(len(db.list_history("part1/project.md", "freewrite")), 1)
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

    async def test_initial_load_skips_archived_last_exercise(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "archived.md").write_text(
                """---
title: Archived
part: 1
module: M
type: reading
status: archived
---

Old body
""",
                encoding="utf-8",
            )
            (root / "part1" / "active.md").write_text(
                """---
title: Active
part: 1
module: M
type: exercise
status: active
---

Current body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            db.set_preference("last_exercise_id", "part1/archived.md")
            app = HarnessApp(database=db, content_root=root)

            await app._load_initial_exercise()

            self.assertIsNotNone(app.current_exercise)
            assert app.current_exercise is not None
            self.assertEqual(app.current_exercise.exercise_id, "part1/active.md")
            db.close()

    async def test_next_previous_navigation_skips_archived_exercises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "a.md").write_text(
                """---
title: First
part: 1
module: M
type: exercise
status: active
---

Body A
""",
                encoding="utf-8",
            )
            (root / "part1" / "archived.md").write_text(
                """---
title: Archived
part: 1
module: M
type: reading
status: archived
---

Old body
""",
                encoding="utf-8",
            )
            (root / "part1" / "b.md").write_text(
                """---
title: Second
part: 1
module: M
type: exercise
status: active
---

Body B
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/a.md", save_current=False)
            await app.action_next_exercise()

            self.assertIsNotNone(app.current_exercise)
            assert app.current_exercise is not None
            self.assertEqual(app.current_exercise.exercise_id, "part1/b.md")

            await app.action_previous_exercise()

            self.assertIsNotNone(app.current_exercise)
            assert app.current_exercise is not None
            self.assertEqual(app.current_exercise.exercise_id, "part1/a.md")
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

            await app.action_set_mode("freewrite")

            self.assertEqual(app.current_layout_mode, "side")
            self.assertEqual(app.bell_count, 1)
            db.close()

    async def test_freewrite_mode_reenables_editor_layouts(self) -> None:
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
            await app.action_set_mode("freewrite")

            self.assertFalse(app.editor.disabled)
            self.assertEqual(app.current_layout_mode, "freewrite")
            self.assertEqual(app._effective_layout_mode(), "freewrite")
            self.assertEqual(app.focus_target, "editor")
            db.close()

    async def test_exercise_mode_is_disabled_without_guided_questions(self) -> None:
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

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/exercise.md", save_current=False)
            await app.action_set_mode("exercise")

            self.assertEqual(app.current_layout_mode, "side")
            self.assertEqual(app.bell_count, 1)
            db.close()

    async def test_project_mode_is_disabled_without_project_key(self) -> None:
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

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/exercise.md", save_current=False)
            await app.action_set_mode("project")

            self.assertEqual(app.current_layout_mode, "side")
            self.assertEqual(app.bell_count, 1)
            db.close()

    async def test_project_mode_is_available_on_linked_reading_docs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part2").mkdir(parents=True)
            (root / "part2" / "reading.md").write_text(
                """---
title: Reading
part: 2
module: Novel
type: reading
project_key: part2-novel
project_title: Part 2 Novel
---

Body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part2/reading.md", save_current=False)
            await app.action_set_mode("project")

            self.assertEqual(app.current_layout_mode, "project")
            self.assertEqual(app._effective_layout_mode(), "project")
            self.assertFalse(app.editor.disabled)
            self.assertEqual(app.focus_target, "editor")
            db.close()

    async def test_exercise_mode_seeds_blank_scaffold(self) -> None:
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

Prompt

## Reflection questions

1\\. What kind of cyberpunk world am I drawn to write?
2\\. What human cost am I most interested in exploring?
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/exercise.md", save_current=False)
            freewrite_entry_id = app.current_entry_id
            await app.action_set_mode("exercise")

            self.assertEqual(app.current_layout_mode, "exercise")
            self.assertEqual(app._effective_layout_mode(), "exercise")
            self.assertNotEqual(app.current_entry_id, freewrite_entry_id)
            self.assertIn("1. What kind of cyberpunk world am I drawn to write?", app.editor.text)
            self.assertIn("2. What human cost am I most interested in exploring?", app.editor.text)
            self.assertIn("Answer:", app.editor.text)
            db.close()

    async def test_freewrite_and_exercise_modes_persist_separately(self) -> None:
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

Prompt

## Reflection questions

1\\. What kind of cyberpunk world am I drawn to write?
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/exercise.md", save_current=False)
            freewrite_entry_id = app.current_entry_id
            app.editor.text = "freewrite draft"
            app._set_save_indicator("Unsaved •", "unsaved")

            await app.action_set_mode("exercise")
            exercise_entry_id = app.current_entry_id
            exercise_text = f"{app.editor.text}\nA costly, street-level future."
            app.editor.text = exercise_text
            app._set_save_indicator("Unsaved •", "unsaved")

            await app.action_set_mode("freewrite")

            self.assertEqual(app.current_entry_id, freewrite_entry_id)
            self.assertEqual(app.editor.text, "freewrite draft")
            self.assertNotEqual(exercise_entry_id, freewrite_entry_id)
            self.assertEqual(db.get_latest_entry("part1/exercise.md", "freewrite").content, "freewrite draft")
            self.assertEqual(db.get_latest_entry("part1/exercise.md", "exercise").id, exercise_entry_id)
            self.assertEqual(db.get_latest_entry("part1/exercise.md", "exercise").content, exercise_text)
            db.close()

    async def test_project_mode_reuses_same_shared_draft_across_linked_docs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part2").mkdir(parents=True)
            (root / "part2" / "seed.md").write_text(
                """---
title: Seed
part: 2
module: Novel
type: long-term
save_mode: project
project_key: part2-novel
project_title: Part 2 Novel
project_seed: true
---

Prompt
""",
                encoding="utf-8",
            )
            (root / "part2" / "reading.md").write_text(
                """---
title: Reading
part: 2
module: Novel
type: reading
project_key: part2-novel
project_title: Part 2 Novel
---

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part2/seed.md", save_current=False)
            await app.action_set_mode("project")
            first_project_entry = app.current_entry_id
            app.editor.text = "shared manuscript"
            app._set_save_indicator("Unsaved •", "unsaved")

            await app._open_exercise_by_id("part2/reading.md")

            self.assertEqual(app.current_entry_id, first_project_entry)
            self.assertEqual(app.editor.text, "shared manuscript")
            db.close()

    async def test_project_mode_falls_back_to_freewrite_on_unlinked_writable_doc(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part2").mkdir(parents=True)
            (root / "part2" / "linked.md").write_text(
                """---
title: Linked
part: 2
module: Novel
type: exercise
project_key: part2-novel
project_title: Part 2 Novel
---

Prompt
""",
                encoding="utf-8",
            )
            (root / "part2" / "freewrite.md").write_text(
                """---
title: Freewrite
part: 2
module: Novel
type: exercise
---

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part2/linked.md", save_current=False)
            await app.action_set_mode("project")
            await app._open_exercise_by_id("part2/freewrite.md")

            self.assertEqual(app._effective_layout_mode(), "freewrite")
            self.assertFalse(app.editor.disabled)
            db.close()

    async def test_history_scopes_to_current_draft_kind(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "project.md").write_text(
                """---
title: Project
part: 1
module: M
type: long-term
save_mode: project
---

Prompt

## Reflection questions

1\\. What kind of future do I want to keep exploring?
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part1/project.md", save_current=False)
            app.editor.text = "freewrite draft"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")

            await app.action_set_mode("exercise")
            app.editor.text = f"{app.editor.text}\nHope under pressure."
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")

            await app.action_set_mode("freewrite")
            await app.action_show_history()
            self.assertIsNotNone(app.pushed_screen)
            self.assertEqual(
                [entry.draft_kind for entry in app.pushed_screen.entries],
                ["freewrite"],
            )

            await app.action_set_mode("exercise")
            await app.action_show_history()
            self.assertIsNotNone(app.pushed_screen)
            self.assertEqual(
                [entry.draft_kind for entry in app.pushed_screen.entries],
                ["exercise"],
            )
            db.close()

    async def test_project_history_uses_project_entries_and_title(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part4").mkdir(parents=True)
            (root / "part4" / "portfolio.md").write_text(
                """---
title: Portfolio
part: 4
module: Portfolio
type: long-term
save_mode: project
project_key: part4-portfolio
project_title: Part 4 Portfolio
---

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part4/portfolio.md", save_current=False)
            await app.action_set_mode("project")
            app.editor.text = "portfolio manuscript"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")
            await app.action_show_history()

            self.assertEqual(app.pushed_screen.title, "Part 4 Portfolio")
            self.assertEqual(app.pushed_screen.entries[0].project_key, "part4-portfolio")
            db.close()

    async def test_project_mode_seeds_from_seed_doc_freewrite_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part2").mkdir(parents=True)
            (root / "part2" / "seed.md").write_text(
                """---
title: Seed
part: 2
module: Novel
type: long-term
save_mode: project
project_key: part2-novel
project_title: Part 2 Novel
project_seed: true
---

Prompt
""",
                encoding="utf-8",
            )
            (root / "part2" / "reading.md").write_text(
                """---
title: Reading
part: 2
module: Novel
type: reading
project_key: part2-novel
project_title: Part 2 Novel
---

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part2/seed.md", save_current=False)
            app.editor.text = "seed freewrite"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")
            await app.action_new_draft()
            app.editor.text = "latest freewrite"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")

            await app._open_exercise_by_id("part2/reading.md")
            await app.action_set_mode("project")

            self.assertEqual(app.editor.text, "latest freewrite")
            self.assertEqual(len(db.list_project_history("part2-novel")), 2)
            self.assertEqual(len(db.list_history("part2/seed.md", "freewrite")), 2)
            db.close()

    async def test_restore_preferences_maps_legacy_write_to_freewrite(self) -> None:
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

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            db.set_preference("last_mode", "write")
            app = HarnessApp(database=db, content_root=root)

            app._restore_preferences()

            self.assertEqual(app.current_layout_mode, "freewrite")
            db.close()


if __name__ == "__main__":
    unittest.main()
