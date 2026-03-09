from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest

from gbtw.content import Exercise, load_content_index
from gbtw.db import Database
from gbtw.profiles import ProfileStore

try:
    from gbtw.main import (
        ExerciseListScreen,
        FooterControl,
        GBTWApp,
        ProfilePickerResult,
        WritingTextArea,
        format_footer_control_label,
        format_project_indicator,
    )

    HAS_TEXTUAL = True
except ModuleNotFoundError:
    ExerciseListScreen = object  # type: ignore[assignment]
    FooterControl = object  # type: ignore[assignment]
    GBTWApp = object  # type: ignore[assignment]
    ProfilePickerResult = object  # type: ignore[assignment]
    WritingTextArea = object  # type: ignore[assignment]
    format_footer_control_label = lambda *args, **kwargs: None  # type: ignore[assignment]
    format_project_indicator = lambda exercise: ""  # type: ignore[assignment]
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


class FakeInput:
    def __init__(self) -> None:
        self.value = ""
        self.focused = False

    def focus(self) -> None:
        self.focused = True


class FakeLabel:
    def __init__(self) -> None:
        self.value = ""
        self.display = True

    def update(self, value) -> None:
        self.value = str(value)


class FakeButton:
    def __init__(self) -> None:
        self.display = True


class FakeProjectTree:
    def __init__(self) -> None:
        self.options: list = []
        self.focused = False

    def clear_options(self) -> None:
        self.options = []

    def add_options(self, options) -> None:
        self.options.extend(options)

    def focus(self) -> None:
        self.focused = True


class FakeFooterControl:
    def __init__(self, label: str) -> None:
        self.label = label
        self.active = False
        self.disabled = False
        self.detail = ""
        self.indicator = False

    def set_active(self, active: bool) -> None:
        self.active = active

    def set_disabled(self, disabled: bool) -> None:
        self.disabled = disabled

    def set_detail(self, detail: str) -> None:
        self.detail = detail

    def set_indicator(self, show_indicator: bool) -> None:
        self.indicator = show_indicator


class HarnessApp(GBTWApp):
    def __init__(
        self,
        *,
        database: Database | None = None,
        profile_store: ProfileStore | None = None,
        content_root: Path,
    ) -> None:
        super().__init__(
            database=database,
            profile_store=profile_store,
            content_index=load_content_index(content_root),
            autosave_delay_seconds=0.01,
            sprint_tick_seconds=0.01,
        )
        self.editor = FakeEditor()
        self.project_doc_editor = FakeEditor()
        self.project_doc_title = FakeInput()
        self.project_name_label = FakeLabel()
        self.proj_prev_btn = FakeButton()
        self.proj_next_btn = FakeButton()
        self.project_tree = FakeProjectTree()
        self.footer_controls = {
            "#mode-read": FakeFooterControl("Read"),
            "#mode-side": FakeFooterControl("Side"),
            "#mode-stack": FakeFooterControl("Stack"),
            "#mode-freewrite": FakeFooterControl("Write"),
            "#mode-exercise": FakeFooterControl("Exercise"),
            "#mode-project": FakeFooterControl("Project"),
            "#show-profiles": FakeFooterControl("Profiles"),
        }
        self.focus_target: str | None = None
        self.markdown_text = ""
        self.bottom_bar_updates = 0
        self.word_count_value = 0
        self.bell_count = 0
        self.pending_tasks: list[asyncio.Task[object]] = []
        self.pushed_screen = None
        self.screen_results: list[object] = []
        self.push_screen_wait_calls = 0
        self.draft_toolbar_visible = False
        self.draft_label = ""
        self.draft_previous_disabled = True
        self.draft_next_disabled = True
        self.draft_delete_disabled = True
        self.draft_undo_disabled = True

    def query_one(self, selector: str, expected_type=None):  # type: ignore[override]
        if selector == "#editor":
            return self.editor
        if selector == "#project-doc-editor":
            return self.project_doc_editor
        if selector == "#project-doc-title":
            return self.project_doc_title
        if selector == "#project-name-label":
            return self.project_name_label
        if selector == "#proj-prev":
            return self.proj_prev_btn
        if selector == "#proj-next":
            return self.proj_next_btn
        if selector == "#project-tree":
            return self.project_tree
        if selector in self.footer_controls:
            return self.footer_controls[selector]
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
            self.draft_delete_disabled = True
            self.draft_undo_disabled = True
            return
        entries = self._current_freewrite_entries()
        count = len(entries)
        current_index = next((index for index, entry in enumerate(entries) if entry.id == self.current_entry_id), 0)
        self.draft_label = "Draft" if count == 0 else f"Draft {current_index + 1}/{count}"
        self.draft_previous_disabled = count <= 1
        self.draft_next_disabled = count <= 1
        self.draft_delete_disabled = count == 0
        self.draft_undo_disabled = not self._can_undo_deleted_freewrite()

    def _focus_editor(self) -> None:
        self.focus_target = "editor"
        self.editor.focus()

    def _focus_exercise(self) -> None:
        self.focus_target = "exercise"

    def _apply_layout(self) -> None:
        self.bottom_bar_updates += 1

    def sync_footer_controls(self) -> None:
        GBTWApp._update_bottom_bar(self)

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

    async def push_screen_wait(self, screen, *args, **kwargs):  # type: ignore[override]
        self.pushed_screen = screen
        self.push_screen_wait_calls += 1
        if not self.screen_results:
            raise AssertionError("missing queued screen result")
        result = self.screen_results.pop(0)
        return result() if callable(result) else result

    async def wait_for_workers(self) -> None:
        if not self.pending_tasks:
            return
        tasks = list(self.pending_tasks)
        self.pending_tasks.clear()
        await asyncio.gather(*tasks)


class FakeOptionList:
    def __init__(self) -> None:
        self.options = []
        self.highlighted = 0
        self.focused = False

    def clear_options(self) -> None:
        self.options = []

    def add_options(self, options) -> None:
        self.options.extend(options)

    @property
    def option_count(self) -> int:
        return len(self.options)

    def focus(self) -> None:
        self.focused = True


class FakeTextWidget:
    def __init__(self) -> None:
        self.value = ""

    def update(self, value) -> None:
        self.value = value


class FakeTabbedContent:
    def __init__(self, active: str) -> None:
        self.active = active


class HarnessExerciseListScreen(ExerciseListScreen):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.dismissed = None
        self._fake_widgets = {
            "#exercise-list-hint": FakeTextWidget(),
            "#exercise-list": FakeOptionList(),
            "#project-list": FakeOptionList(),
            "#project-document-list": FakeOptionList(),
            "#project-documents-title": FakeTextWidget(),
            "#exercise-list-tabs": FakeTabbedContent(self.TAB_EXERCISES),
        }

    def query_one(self, selector: str, expected_type=None):  # type: ignore[override]
        return self._fake_widgets[selector]

    def dismiss(self, result=None) -> None:  # type: ignore[override]
        self.dismissed = result


@unittest.skipUnless(HAS_TEXTUAL, "Textual is not installed")
class WritingTextAreaTests(unittest.TestCase):
    def test_double_space_replaces_previous_space_with_period_space(self) -> None:
        editor = WritingTextArea("hello ")
        editor.cursor_location = (0, 6)

        replaced = editor._replace_previous_space_with_period()

        self.assertTrue(replaced)
        self.assertEqual(editor.text, "hello. ")

    def test_double_space_rule_skips_existing_sentence_punctuation(self) -> None:
        editor = WritingTextArea("hello. ")
        editor.cursor_location = (0, 7)

        replaced = editor._replace_previous_space_with_period()

        self.assertFalse(replaced)
        self.assertEqual(editor.text, "hello. ")

    def test_editor_uses_indent_tab_behavior(self) -> None:
        editor = WritingTextArea()

        self.assertEqual(editor.tab_behavior, "indent")

    def test_auto_capitalizes_at_start_of_text(self) -> None:
        editor = WritingTextArea("")

        should_capitalize = editor._auto_capitalize_character(
            SimpleNamespace(is_printable=True, character="h")
        )

        self.assertTrue(should_capitalize)

    def test_auto_capitalizes_after_period(self) -> None:
        editor = WritingTextArea("hello. ")
        editor.cursor_location = (0, 7)

        should_capitalize = editor._auto_capitalize_character(
            SimpleNamespace(is_printable=True, character="n")
        )

        self.assertTrue(should_capitalize)

    def test_auto_capitalization_skips_mid_sentence(self) -> None:
        editor = WritingTextArea("hello ")
        editor.cursor_location = (0, 6)

        should_capitalize = editor._auto_capitalize_character(
            SimpleNamespace(is_printable=True, character="n")
        )

        self.assertFalse(should_capitalize)


@unittest.skipUnless(HAS_TEXTUAL, "Textual is not installed")
class MainLogicTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_writing_exercise_locks_current_draft_after_ten_minutes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part1" / "a.md").write_text(
                """---
title: A
part: 1
module: M
type: long-term
save_mode: session
---

Write
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app.on_mount()
            self.assertIsNotNone(app.current_entry_id)
            self.assertIsNotNone(app._timed_state)
            assert app._timed_state is not None
            self.assertIsNone(app._timed_state.started_at)

            app.editor.text = "h"
            app.on_editor_changed(None)  # type: ignore[arg-type]

            self.assertIsNotNone(app._timed_state.started_at)
            app._timed_state.started_at = datetime.now().astimezone() - timedelta(seconds=app._timed_limit_seconds)
            app._tick_timed_draft()

            self.assertTrue(app._timed_state.locked)
            self.assertTrue(app.editor.disabled)
            db.close()

    async def test_on_mount_with_injected_database_bypasses_profile_picker(self) -> None:
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
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "legacy.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            app = HarnessApp(database=db, profile_store=store, content_root=root)

            await app.on_mount()

            self.assertEqual(app.push_screen_wait_calls, 0)
            self.assertIsNone(app.current_profile)
            self.assertIsNotNone(app.current_exercise)
            db.close()

    async def test_on_mount_without_database_requires_profile_selection(self) -> None:
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
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "legacy.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            default_profile = store.list_profiles()[0]
            app = HarnessApp(profile_store=store, content_root=root)
            app.screen_results = [ProfilePickerResult("open", default_profile.profile_id)]

            await app.on_mount()
            await app.wait_for_workers()

            self.assertEqual(app.push_screen_wait_calls, 1)
            self.assertIsNotNone(app.current_profile)
            assert app.current_profile is not None
            self.assertEqual(app.current_profile.profile_id, "default")
            self.assertIsNotNone(app.database)
            self.assertIsNotNone(app.current_exercise)
            app.database.close()

    async def test_on_mount_can_create_profile_before_opening_it(self) -> None:
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
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "legacy.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            app = HarnessApp(profile_store=store, content_root=root)
            app.screen_results = [
                ProfilePickerResult("new"),
                "Alice",
                ProfilePickerResult("open", "alice"),
            ]

            await app.on_mount()
            await app.wait_for_workers()

            self.assertEqual(app.push_screen_wait_calls, 3)
            self.assertIsNotNone(app.current_profile)
            assert app.current_profile is not None
            self.assertEqual(app.current_profile.profile_id, "alice")
            self.assertEqual(store.list_profiles()[0].profile_id, "alice")
            assert app.database is not None
            app.database.close()

    async def test_action_show_profiles_can_rename_current_profile(self) -> None:
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
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "legacy.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            default_profile = store.list_profiles()[0]
            app = HarnessApp(profile_store=store, content_root=root)
            app.screen_results = [ProfilePickerResult("open", default_profile.profile_id)]

            await app.on_mount()
            await app.wait_for_workers()

            app.screen_results = [
                ProfilePickerResult("rename", "default"),
                "Family",
                None,
            ]

            await app.action_show_profiles()

            self.assertIsNotNone(app.current_profile)
            assert app.current_profile is not None
            self.assertEqual(app.current_profile.display_name, "Family")
            self.assertEqual(store.get_profile("default").display_name, "Family")
            assert app.database is not None
            app.database.close()

    async def test_action_show_profiles_switches_database_after_saving_current_work(self) -> None:
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
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "legacy.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            alice = store.create_profile("Alice")
            bob = store.create_profile("Bob")
            app = HarnessApp(profile_store=store, content_root=root)
            app.screen_results = [ProfilePickerResult("open", alice.profile_id)]

            await app.on_mount()
            await app.wait_for_workers()

            await app._open_exercise_by_id("part1/a.md", save_current=False)
            app.editor.text = "alice draft"
            app._set_save_indicator("Unsaved •", "unsaved")

            app.screen_results = [ProfilePickerResult("open", bob.profile_id)]
            await app.action_show_profiles()

            old_db = Database(alice.db_path)
            self.assertEqual(old_db.get_latest_entry("part1/a.md", "freewrite").content, "alice draft")
            old_db.close()

            self.assertIsNotNone(app.current_profile)
            assert app.current_profile is not None
            self.assertEqual(app.current_profile.profile_id, "bob")
            self.assertEqual(app.editor.text, "")
            assert app.database is not None
            self.assertEqual(app.database.get_latest_entry("part1/a.md", "freewrite").content, "")
            app.database.close()

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
            await app.action_set_mode("freewrite")
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
            self.assertEqual(app.draft_label, "Draft 1/2")
            self.assertFalse(app.draft_previous_disabled)
            self.assertFalse(app.draft_next_disabled)
            self.assertFalse(app.draft_delete_disabled)
            self.assertTrue(app.draft_undo_disabled)

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

    async def test_freewrite_draft_delete_and_undo_restore_content(self) -> None:
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
            await app.action_set_mode("freewrite")
            app.editor.text = "draft one"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")
            first_entry_id = app.current_entry_id

            await app.action_new_draft()
            app.editor.text = "draft two"
            app._set_save_indicator("Unsaved •", "unsaved")
            await app._save_current_entry("manual")

            await app.action_delete_draft()

            self.assertEqual(len(db.list_history("part1/a.md", "freewrite")), 1)
            self.assertEqual(app.current_entry_id, first_entry_id)
            self.assertEqual(app.editor.text, "draft one")
            self.assertFalse(app.draft_undo_disabled)

            await app.action_undo_delete_draft()

            self.assertEqual(len(db.list_history("part1/a.md", "freewrite")), 2)
            self.assertNotEqual(app.current_entry_id, first_entry_id)
            self.assertEqual(app.editor.text, "draft two")
            self.assertTrue(app.draft_undo_disabled)
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
            await app.action_set_mode("freewrite")
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
            self.assertEqual(app.active_project_key, "part2-novel")
            self.assertTrue(app.project_tree.focused)
            db.close()

    async def test_action_show_exercise_list_passes_database_project_titles(self) -> None:
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
---

Body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)
            db.connection.execute(
                "UPDATE projects SET title = ? WHERE project_key = ?",
                ("Dark Fantasy Novel", "part2-novel"),
            )
            db.connection.commit()

            await app.action_show_exercise_list()

            self.assertIsInstance(app.pushed_screen, ExerciseListScreen)
            self.assertEqual(app.pushed_screen.project_titles["part2-novel"], "Dark Fantasy Novel")
            db.close()

    def test_format_project_indicator_handles_seed_and_consolidator(self) -> None:
        exercise = Exercise(
            exercise_id="part4/p4-09-final-portfolio.md",
            source_path=Path("/tmp/part4/p4-09-final-portfolio.md"),
            title="Portfolio",
            part=4,
            module="Final Portfolio",
            type="long-term",
            status="ongoing",
            save_mode="project",
            body="Body",
            guided_questions=(),
            project_key="part4-portfolio",
            project_title=None,
            project_seed=True,
            project_role="consolidator",
        )

        self.assertEqual(
            format_project_indicator(exercise),
            "part4-portfolio · consolidator [seed]",
        )

    def test_format_footer_control_label_adds_project_dot(self) -> None:
        self.assertEqual(
            format_footer_control_label("Project", show_indicator=False).plain,
            "Project",
        )
        self.assertEqual(
            format_footer_control_label("Project", show_indicator=True).plain,
            "Project •",
        )

    def test_footer_control_only_exposes_project_detail_when_enabled(self) -> None:
        control = FooterControl("Project", control_id="mode-project")

        control.set_detail("dark-fantasy-novel · seed")
        self.assertEqual(control.tooltip, "dark-fantasy-novel · seed")

        control.set_disabled(True)
        self.assertIsNone(control.tooltip)

        control.set_disabled(False)
        self.assertEqual(control.tooltip, "dark-fantasy-novel · seed")

    async def test_project_button_indicator_is_absent_without_project_key(self) -> None:
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
            app.sync_footer_controls()

            self.assertFalse(app.footer_controls["#mode-project"].indicator)
            db.close()

    async def test_project_button_indicator_appears_on_linked_reading(self) -> None:
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
---

Body
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part2/reading.md", save_current=False)
            app.sync_footer_controls()

            self.assertTrue(app.footer_controls["#mode-project"].indicator)
            db.close()

    async def test_project_button_indicator_appears_on_project_save_mode_lesson(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part2").mkdir(parents=True)
            (root / "part2" / "project.md").write_text(
                """---
title: Project Draft
part: 2
module: Novel
type: long-term
save_mode: project
project_key: part2-novel
---

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part2/project.md", save_current=False)
            app.sync_footer_controls()

            self.assertTrue(app.footer_controls["#mode-project"].indicator)
            db.close()

    async def test_project_button_indicator_remains_visible_in_project_mode(self) -> None:
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
---

Prompt
""",
                encoding="utf-8",
            )
            db = Database(Path(tmp) / "progress.db")
            app = HarnessApp(database=db, content_root=root)

            await app._open_exercise_by_id("part2/linked.md", save_current=False)
            await app.action_set_mode("project")
            app.sync_footer_controls()

            self.assertTrue(app.footer_controls["#mode-project"].indicator)
            self.assertTrue(app.footer_controls["#mode-project"].active)
            db.close()

    def test_exercise_list_projects_tab_refreshes_documents_and_dismisses_selected_contributor(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "content"
            (root / "part1").mkdir(parents=True)
            (root / "part2").mkdir(parents=True)
            (root / "part1" / "20-seed.md").write_text(
                """---
title: Seed
part: 1
module: Novel
type: reading
project_key: dark-fantasy-novel
project_seed: true
---

Body
""",
                encoding="utf-8",
            )
            (root / "part2" / "p2-10-end.md").write_text(
                """---
title: End
part: 2
module: Novel
type: long-term
save_mode: project
status: ongoing
project_key: dark-fantasy-novel
project_role: consolidator
---

Body
""",
                encoding="utf-8",
            )
            screen = HarnessExerciseListScreen(
                load_content_index(root),
                "part2/p2-10-end.md",
                {"dark-fantasy-novel": "dark-fantasy-novel"},
            )

            screen._refresh_project_options()
            screen.query_one("#exercise-list-tabs").active = screen.TAB_PROJECTS
            project_list = screen.query_one("#project-list")
            document_list = screen.query_one("#project-document-list")

            self.assertEqual(project_list.option_count, 1)
            self.assertEqual(document_list.option_count, 2)

            screen.on_project_selected(SimpleNamespace(option_id="project:dark-fantasy-novel"))
            self.assertTrue(document_list.focused)

            screen.on_project_document_selected(SimpleNamespace(option_id="exercise:part1/20-seed.md"))
            self.assertEqual(screen.dismissed, "part1/20-seed.md")

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

    async def test_project_mode_keeps_active_project_across_linked_docs(self) -> None:
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
            self.assertEqual(app.active_project_key, "part2-novel")

            # Create a doc to track persistence across exercise navigation
            doc = db.create_project_doc("part2-novel", "Characters", "Hero")
            app._load_project_doc(doc.id)
            saved_doc_id = app.current_project_doc_id

            await app._open_exercise_by_id("part2/reading.md")

            # Active project and selected doc remain the same
            self.assertEqual(app.active_project_key, "part2-novel")
            self.assertEqual(app.current_project_doc_id, saved_doc_id)
            db.close()

    async def test_project_mode_stays_available_after_unlock_on_unlinked_doc(self) -> None:
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
            # Once unlocked, project mode is always available
            await app._open_exercise_by_id("part2/freewrite.md")

            # Project mode should still be active (permanently unlocked)
            self.assertEqual(app._effective_layout_mode(), "project")
            self.assertTrue(app._project_mode_available())
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

    async def test_project_workspace_loads_and_unlocks_on_project_exercise(self) -> None:
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
            # Project mode should be unlocked after navigating to a project exercise
            self.assertEqual(db.get_preference("project_unlocked"), "1")

            await app.action_set_mode("project")
            # Active project key should be set
            self.assertEqual(app.active_project_key, "part4-portfolio")
            # Project tree should have been rebuilt with categories
            self.assertTrue(len(app.project_tree.options) > 0)
            # No docs yet — current_project_doc_id should be None
            self.assertIsNone(app.current_project_doc_id)
            # Create a doc and verify save works
            doc = db.create_project_doc("part4-portfolio", "Characters", "Mira")
            app._load_project_doc(doc.id)
            self.assertEqual(app.current_project_doc_id, doc.id)
            app.project_doc_editor.text = "Mira is a courier in the lower city."
            app._mark_project_doc_dirty()
            await app._save_project_doc("manual")
            saved = db.get_project_doc(doc.id)
            self.assertEqual(saved.content, "Mira is a courier in the lower city.")
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
            await app.action_set_mode("freewrite")
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
