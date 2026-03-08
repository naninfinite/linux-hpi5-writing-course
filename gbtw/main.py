from __future__ import annotations

import inspect
from datetime import datetime

from textual import on
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, Input, Label, MarkdownViewer, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .content import ContentIndex, Exercise, load_content_index, render_markdown_fallback
from .db import Database, EntryRecord

SIDE_RATIOS: tuple[tuple[int, int], ...] = ((40, 60), (50, 50), (60, 40))
LAYOUTS = {"read", "side", "stack", "write"}
SHORTCUT_TEXT = """\
F1  Reading mode
F2  Side split
F3  Stack split
F4  Write mode
F5  Top mode (alias of Stack)

[   decrease split ratio
]   increase split ratio

Ctrl+N  next exercise
Ctrl+P  previous exercise
Ctrl+S  save
Ctrl+J  start new long-term session now

Ctrl+E  exercise list
Ctrl+H  history modal (long-term exercises)
Ctrl+T  timed writing sprint modal

Tab     switch focus between panes
?       keyboard help

Ctrl+Q  quit
"""


def word_count(text: str) -> int:
    return len([word for word in text.split() if word.strip()])


def format_entry_label(entry: EntryRecord) -> str:
    timestamp = entry.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
    preview = entry.content.strip().splitlines()[0] if entry.content.strip() else "(empty)"
    return f"{timestamp}  {preview[:60]}"


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss_screen", "Close", show=False)]

    def compose(self) -> ComposeResult:
        with Container(id="modal"):
            yield Static("Keyboard Shortcuts", classes="modal-title")
            yield Static(SHORTCUT_TEXT, id="help-text")
            yield Button("Close", id="help-close")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "help-close":
            self.dismiss(None)


class EntryPreviewScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss_screen", "Close", show=False)]

    def __init__(self, entry: EntryRecord) -> None:
        super().__init__()
        self.entry = entry

    def compose(self) -> ComposeResult:
        with Container(id="modal", classes="wide-modal"):
            yield Static(
                f"Read-only entry from {self.entry.updated_at.astimezone():%Y-%m-%d %H:%M}",
                classes="modal-title",
            )
            with VerticalScroll(id="preview-scroll"):
                yield Static(self.entry.content or "(empty)", id="preview-body")
            yield Button("Close", id="preview-close")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preview-close":
            self.dismiss(None)


class SprintScreen(ModalScreen[int | None]):
    BINDINGS = [Binding("escape", "dismiss_screen", "Close", show=False)]

    def compose(self) -> ComposeResult:
        with Container(id="modal"):
            yield Static("Timed Writing Sprint", classes="modal-title")
            with Horizontal(classes="button-row"):
                yield Button("10 min", id="sprint-10")
                yield Button("20 min", id="sprint-20")
                yield Button("30 min", id="sprint-30")
            yield Label("Custom minutes", classes="field-label")
            yield Input(placeholder="Enter minutes", id="custom-minutes")
            yield Button("Start custom", id="sprint-custom")
            yield Button("Cancel", id="sprint-cancel")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {"sprint-10": 10, "sprint-20": 20, "sprint-30": 30}
        if event.button.id in mapping:
            self.dismiss(mapping[event.button.id] * 60)
            return
        if event.button.id == "sprint-custom":
            raw_value = self.query_one("#custom-minutes", Input).value.strip()
            try:
                minutes = int(raw_value)
            except ValueError:
                self.app.bell()
                return
            if minutes <= 0:
                self.app.bell()
                return
            self.dismiss(minutes * 60)
            return
        if event.button.id == "sprint-cancel":
            self.dismiss(None)


class HistoryScreen(ModalScreen[EntryRecord | None]):
    BINDINGS = [Binding("escape", "dismiss_screen", "Close", show=False)]

    def __init__(self, exercise: Exercise, entries: list[EntryRecord]) -> None:
        super().__init__()
        self.exercise = exercise
        self.entries = entries

    def compose(self) -> ComposeResult:
        with Container(id="modal", classes="wide-modal"):
            yield Static(f"History: {self.exercise.title}", classes="modal-title")
            if not self.entries:
                yield Static("No saved entries yet.", id="history-empty")
            else:
                options = [
                    Option(format_entry_label(entry), id=str(entry.id))
                    for entry in self.entries
                ]
                yield OptionList(*options, id="history-list")
            yield Button("Close", id="history-close")

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#history-list")
    def on_history_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option_id
        if option_id is None:
            return
        selected = next((entry for entry in self.entries if str(entry.id) == option_id), None)
        self.dismiss(selected)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "history-close":
            self.dismiss(None)


class ExerciseListScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close", show=False),
        Binding("ctrl+a", "toggle_archived", "Archived", show=False),
    ]

    show_archived = reactive(False)

    def __init__(self, content_index: ContentIndex, current_exercise_id: str | None) -> None:
        super().__init__()
        self.content_index = content_index
        self.current_exercise_id = current_exercise_id

    def compose(self) -> ComposeResult:
        with Container(id="modal", classes="wide-modal"):
            yield Static("Exercise List", classes="modal-title")
            yield Label("", id="exercise-list-hint")
            yield OptionList(id="exercise-list")
            yield Button("Close", id="exercise-list-close")

    def on_mount(self) -> None:
        self._refresh_options()

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def action_toggle_archived(self) -> None:
        self.show_archived = not self.show_archived
        self._refresh_options()

    @on(OptionList.OptionSelected, "#exercise-list")
    def on_exercise_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option_id
        if option_id and option_id.startswith("exercise:"):
            self.dismiss(option_id.split(":", 1)[1])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "exercise-list-close":
            self.dismiss(None)

    def _refresh_options(self) -> None:
        hint = self.query_one("#exercise-list-hint", Label)
        hint.update(
            "Ctrl+A toggles archived exercises "
            f"({'shown' if self.show_archived else 'hidden'})"
        )
        option_list = self.query_one("#exercise-list", OptionList)
        options: list[Option | None] = []
        for part, exercises in self.content_index.grouped_by_part(include_archived=self.show_archived).items():
            options.append(Option(f"Part {part}", id=f"header:{part}", disabled=True))
            for exercise in exercises:
                marker = " [optional]" if exercise.status == "optional" else ""
                current = " *" if exercise.exercise_id == self.current_exercise_id else ""
                label = f"{exercise.title}{marker}{current}"
                options.append(Option(label, id=f"exercise:{exercise.exercise_id}"))
        option_list.clear_options()
        option_list.add_options(options)


class GBTWApp(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #workspace {
        height: 1fr;
        width: 100%;
        background: #101316;
    }

    #exercise-pane, #writing-pane {
        border: solid #3a4148;
        padding: 0 1;
        min-height: 5;
    }

    #exercise-pane {
        background: #1a2024;
    }

    #writing-pane {
        background: #0f1519;
    }

    #exercise-markdown, #exercise-fallback {
        height: 1fr;
    }

    #exercise-fallback-view {
        height: 1fr;
    }

    #editor {
        height: 1fr;
        background: #0f1519;
    }

    #bottom-bar {
        height: auto;
        padding: 0 1;
        background: #1a1f22;
        color: #e8e6e3;
        border-top: solid #2f353b;
        align: left middle;
    }

    .button-row {
        height: auto;
    }

    #left-buttons, #right-status {
        height: auto;
    }

    #right-status {
        width: 1fr;
    }

    Button {
        margin-right: 1;
    }

    Button.active-mode {
        background: #d2c36b;
        color: #171717;
        text-style: bold;
    }

    #save-indicator.saving {
        color: #e9b96e;
    }

    #save-indicator.saved {
        color: #8ae234;
    }

    #save-indicator.unsaved {
        color: #ef6b73;
    }

    #modal {
        width: 72;
        max-width: 90%;
        height: auto;
        max-height: 90%;
        background: #1d2327;
        border: round #6e7f8d;
        padding: 1 2;
        align: center middle;
    }

    .wide-modal {
        width: 100;
    }

    .modal-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #help-text, #preview-scroll, #history-list, #exercise-list {
        height: 1fr;
    }

    #history-empty {
        margin-bottom: 1;
    }

    .field-label {
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("f1", "set_mode('read')", "Read", show=False),
        Binding("f2", "set_mode('side')", "Side", show=False),
        Binding("f3", "set_mode('stack')", "Stack", show=False),
        Binding("f4", "set_mode('write')", "Write", show=False),
        Binding("f5", "top_mode", "Top", show=False),
        Binding("[", "decrease_split", "Split -", show=False),
        Binding("]", "increase_split", "Split +", show=False),
        Binding("ctrl+n", "next_exercise", "Next", show=False),
        Binding("ctrl+p", "previous_exercise", "Previous", show=False),
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("ctrl+j", "new_session_now", "New Session", show=False),
        Binding("ctrl+e", "show_exercise_list", "Exercises", show=False),
        Binding("ctrl+h", "show_history", "History", show=False),
        Binding("ctrl+t", "show_sprint", "Sprint", show=False),
        Binding("tab", "toggle_focus", "Focus", show=False),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
    ]

    def __init__(
        self,
        *,
        database: Database | None = None,
        content_index: ContentIndex | None = None,
    ) -> None:
        super().__init__()
        self.database: Database | None = database or Database()
        self.content_index = content_index or load_content_index()
        self.current_exercise: Exercise | None = None
        self.current_entry_id: int | None = None
        self.current_layout_mode = "side"
        self.side_ratio_index = 1
        self._loading_editor = False
        self._ignored_loaded_text: str | None = None
        self._suppress_autosave = False
        self._autosave_generation = 0
        self._save_generation = 0
        self._sprint_seconds_remaining = 0
        self._sprint_timer: Timer | None = None
        self._using_markdown_fallback = False
        self._last_save_message = "Saved ✓"

    def compose(self) -> ComposeResult:
        with Container(id="workspace"):
            with Container(id="exercise-pane"):
                yield MarkdownViewer("", id="exercise-markdown", show_table_of_contents=False)
                with VerticalScroll(id="exercise-fallback"):
                    yield Static("", id="exercise-fallback-view")
            with Container(id="writing-pane"):
                yield TextArea("", id="editor")
        with Horizontal(id="bottom-bar"):
            with Horizontal(id="left-buttons", classes="button-row"):
                yield Button("Read", id="mode-read")
                yield Button("Side", id="mode-side")
                yield Button("Stack", id="mode-stack")
                yield Button("Write", id="mode-write")
                yield Button("Top", id="mode-top")
                yield Static("|")
                yield Button("←", id="previous-exercise")
                yield Button("→", id="next-exercise")
            with Horizontal(id="right-status", classes="button-row"):
                yield Label("Wc: 0", id="word-count")
                yield Label("Saved ✓", id="save-indicator", classes="saved")
                yield Button("?", id="help-button")

    async def on_mount(self) -> None:
        self.title = "gbtw"
        self.sub_title = "Getting Back to Writing"
        self._restore_preferences()
        await self._load_initial_exercise()
        self._apply_layout()
        self._update_bottom_bar()
        if self.content_index.warnings:
            self.sub_title = f"Skipped {len(self.content_index.warnings)} invalid content file(s)"

    async def action_set_mode(self, mode: str) -> None:
        if mode not in LAYOUTS:
            return
        assert self.database is not None
        self.current_layout_mode = mode
        self.database.set_preference("last_mode", self.current_layout_mode)
        self._apply_layout()
        if mode != "read":
            self._focus_editor()

    async def action_top_mode(self) -> None:
        await self.action_set_mode("stack")
        self._focus_exercise()

    async def action_decrease_split(self) -> None:
        assert self.database is not None
        self.side_ratio_index = max(0, self.side_ratio_index - 1)
        self.database.set_preference("last_split_ratio", self._current_ratio_string())
        self._apply_layout()

    async def action_increase_split(self) -> None:
        assert self.database is not None
        self.side_ratio_index = min(len(SIDE_RATIOS) - 1, self.side_ratio_index + 1)
        self.database.set_preference("last_split_ratio", self._current_ratio_string())
        self._apply_layout()

    async def action_next_exercise(self) -> None:
        await self._step_exercise(1)

    async def action_previous_exercise(self) -> None:
        await self._step_exercise(-1)

    async def action_save(self) -> None:
        await self._save_current_entry("manual")

    async def action_new_session_now(self) -> None:
        if self.current_exercise is None or not self.current_exercise.is_long_term:
            self.bell()
            return
        if self.current_exercise.save_mode != "session":
            self.bell()
            return
        await self._create_new_session_entry()

    async def action_show_exercise_list(self) -> None:
        selection = await self.push_screen_wait(
            ExerciseListScreen(
                self.content_index,
                self.current_exercise.exercise_id if self.current_exercise else None,
            )
        )
        if selection:
            await self._open_exercise_by_id(selection)

    async def action_show_history(self) -> None:
        if self.current_exercise is None or not self.current_exercise.is_long_term:
            self.bell()
            return
        assert self.database is not None
        entries = self.database.list_history(self.current_exercise.exercise_id)
        selected = await self.push_screen_wait(HistoryScreen(self.current_exercise, entries))
        if selected is not None:
            await self.push_screen_wait(EntryPreviewScreen(selected))

    async def action_show_sprint(self) -> None:
        seconds = await self.push_screen_wait(SprintScreen())
        if seconds:
            self._start_sprint(seconds)

    async def action_toggle_focus(self) -> None:
        focused = self.focused
        editor = self.query_one("#editor", TextArea)
        if focused is editor:
            self._focus_exercise()
        else:
            self._focus_editor()

    async def action_quit_app(self) -> None:
        if self._is_dirty():
            await self._save_current_entry("quit")
        if self.database is not None:
            self.database.close()
            self.database = None
        self.exit()

    async def on_unmount(self) -> None:
        if self.database is not None:
            self.database.close()
            self.database = None

    async def on_key(self, event: events.Key) -> None:
        if event.key in {"question_mark", "?"}:
            await self.push_screen_wait(HelpScreen())
            event.stop()

    @on(Button.Pressed)
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "mode-read":
            await self.action_set_mode("read")
        elif button_id == "mode-side":
            await self.action_set_mode("side")
        elif button_id == "mode-stack":
            await self.action_set_mode("stack")
        elif button_id == "mode-write":
            await self.action_set_mode("write")
        elif button_id == "mode-top":
            await self.action_top_mode()
        elif button_id == "previous-exercise":
            await self.action_previous_exercise()
        elif button_id == "next-exercise":
            await self.action_next_exercise()
        elif button_id == "help-button":
            await self.push_screen_wait(HelpScreen())

    @on(TextArea.Changed, "#editor")
    def on_editor_changed(self, _: TextArea.Changed) -> None:
        if self._loading_editor:
            return
        editor = self.query_one("#editor", TextArea)
        if self._ignored_loaded_text is not None and editor.text == self._ignored_loaded_text:
            self._ignored_loaded_text = None
            return
        self._mark_dirty()

    def _restore_preferences(self) -> None:
        assert self.database is not None
        last_mode = self.database.get_preference("last_mode")
        if last_mode in LAYOUTS:
            self.current_layout_mode = last_mode
        ratio = self.database.get_preference("last_split_ratio")
        if ratio:
            for index, pair in enumerate(SIDE_RATIOS):
                if ratio == f"{pair[0]}/{pair[1]}":
                    self.side_ratio_index = index
                    break

    async def _load_initial_exercise(self) -> None:
        assert self.database is not None
        preferred_id = self.database.get_preference("last_exercise_id")
        if preferred_id and self.content_index.get(preferred_id):
            await self._open_exercise_by_id(preferred_id, save_current=False)
            return
        first = self.content_index.first_available()
        if first is None:
            await self._show_empty_state()
            return
        await self._open_exercise_by_id(first.exercise_id, save_current=False)

    async def _show_empty_state(self) -> None:
        self.current_exercise = None
        self.current_entry_id = None
        await self._update_exercise_markdown("# No exercises found\n\nRun install.sh to create sample content.")
        editor = self.query_one("#editor", TextArea)
        self._loading_editor = True
        self._ignored_loaded_text = ""
        editor.load_text("")
        self._loading_editor = False
        editor.disabled = True
        self._set_save_indicator("Saved ✓", "saved")
        self._update_word_count()

    async def _step_exercise(self, direction: int) -> None:
        if not self.content_index.exercises or self.current_exercise is None:
            return
        current_index = next(
            (index for index, item in enumerate(self.content_index.exercises) if item.exercise_id == self.current_exercise.exercise_id),
            0,
        )
        next_index = max(0, min(len(self.content_index.exercises) - 1, current_index + direction))
        if next_index == current_index:
            self.bell()
            return
        await self._open_exercise_by_id(self.content_index.exercises[next_index].exercise_id)

    async def _open_exercise_by_id(self, exercise_id: str, *, save_current: bool = True) -> None:
        target = self.content_index.get(exercise_id)
        if target is None:
            return
        if save_current and self._is_dirty():
            await self._save_current_entry("switch")
        self.current_exercise = target
        assert self.database is not None
        self.database.set_preference("last_exercise_id", target.exercise_id)
        self.current_entry_id = None
        record = self.database.resolve_entry_for_exercise(target)
        self.current_entry_id = record.id
        self.query_one("#editor", TextArea).disabled = False
        await self._update_exercise_markdown(target.body)
        self._loading_editor = True
        self._ignored_loaded_text = record.content
        self.query_one("#editor", TextArea).load_text(record.content)
        self._loading_editor = False
        self._set_save_indicator("Saved ✓", "saved")
        self._update_word_count()
        self._update_bottom_bar()
        if self.current_layout_mode != "read":
            self._focus_editor()

    async def _update_exercise_markdown(self, markdown_text: str) -> None:
        viewer = self.query_one("#exercise-markdown", MarkdownViewer)
        fallback = self.query_one("#exercise-fallback", VerticalScroll)
        fallback_text = self.query_one("#exercise-fallback-view", Static)
        try:
            result = viewer.update(markdown_text)
            if inspect.isawaitable(result):
                await result
            viewer.display = True
            fallback.display = False
            self._using_markdown_fallback = False
        except Exception:
            fallback_text.update(render_markdown_fallback(markdown_text))
            viewer.display = False
            fallback.display = True
            self._using_markdown_fallback = True

    def _apply_layout(self) -> None:
        workspace = self.query_one("#workspace", Container)
        exercise_pane = self.query_one("#exercise-pane", Container)
        writing_pane = self.query_one("#writing-pane", Container)
        ratio_left, ratio_right = SIDE_RATIOS[self.side_ratio_index]
        if self.current_layout_mode == "read":
            workspace.styles.layout = "vertical"
            exercise_pane.display = True
            writing_pane.display = False
            exercise_pane.styles.width = "100%"
            exercise_pane.styles.height = "100%"
        elif self.current_layout_mode == "write":
            workspace.styles.layout = "vertical"
            exercise_pane.display = False
            writing_pane.display = True
            writing_pane.styles.width = "100%"
            writing_pane.styles.height = "100%"
        elif self.current_layout_mode == "side":
            workspace.styles.layout = "horizontal"
            exercise_pane.display = True
            writing_pane.display = True
            exercise_pane.styles.width = f"{ratio_left}%"
            writing_pane.styles.width = f"{ratio_right}%"
            exercise_pane.styles.height = "100%"
            writing_pane.styles.height = "100%"
        else:
            workspace.styles.layout = "vertical"
            exercise_pane.display = True
            writing_pane.display = True
            exercise_pane.styles.height = "50%"
            writing_pane.styles.height = "50%"
            exercise_pane.styles.width = "100%"
            writing_pane.styles.width = "100%"
        self._update_bottom_bar()

    def _update_bottom_bar(self) -> None:
        mapping = {
            "mode-read": "read",
            "mode-side": "side",
            "mode-stack": "stack",
            "mode-write": "write",
        }
        for button_id, mode in mapping.items():
            button = self.query_one(f"#{button_id}", Button)
            button.set_class(mode == self.current_layout_mode, "active-mode")
        self.query_one("#mode-top", Button).set_class(False, "active-mode")
        self._update_word_count()

    def _update_word_count(self) -> None:
        label = self.query_one("#word-count", Label)
        if self._sprint_seconds_remaining > 0:
            minutes, seconds = divmod(self._sprint_seconds_remaining, 60)
            label.update(f"Sprint: {minutes:02d}:{seconds:02d}")
            return
        editor = self.query_one("#editor", TextArea)
        label.update(f"Wc: {word_count(editor.text)}")

    def _mark_dirty(self) -> None:
        self._set_save_indicator("Unsaved •", "unsaved")
        self._update_word_count()
        if self._suppress_autosave:
            return
        self._autosave_generation += 1
        generation = self._autosave_generation
        self.set_timer(4.0, lambda: self.call_after_refresh(self._autosave_if_current, generation))

    def _autosave_if_current(self, generation: int) -> None:
        if generation != self._autosave_generation:
            return
        if self._suppress_autosave or not self._is_dirty():
            return
        self.run_worker(self._save_current_entry("autosave"), exclusive=False)

    async def _save_current_entry(self, reason: str) -> None:
        if self.current_entry_id is None:
            return
        assert self.database is not None
        editor = self.query_one("#editor", TextArea)
        self._save_generation += 1
        generation = self._save_generation
        self._set_save_indicator("Saving…", "saving")
        record = self.database.update_entry(self.current_entry_id, editor.text)
        if generation != self._save_generation:
            return
        self.current_entry_id = record.id
        self._set_save_indicator("Saved ✓", "saved")
        if reason == "manual":
            self.set_timer(1.5, lambda: self.call_after_refresh(self._restore_saved_indicator))

    async def _create_new_session_entry(self) -> None:
        if self._is_dirty():
            await self._save_current_entry("manual")
        if self.current_exercise is None:
            return
        assert self.database is not None
        record = self.database.create_entry(self.current_exercise.exercise_id, "")
        self.current_entry_id = record.id
        self._loading_editor = True
        self._ignored_loaded_text = ""
        self.query_one("#editor", TextArea).load_text("")
        self._loading_editor = False
        self._set_save_indicator("Saved ✓", "saved")
        self._update_word_count()
        self._focus_editor()

    def _restore_saved_indicator(self) -> None:
        if not self._is_dirty():
            self._set_save_indicator("Saved ✓", "saved")

    def _set_save_indicator(self, text: str, state: str) -> None:
        indicator = self.query_one("#save-indicator", Label)
        indicator.update(text)
        indicator.remove_class("saving")
        indicator.remove_class("saved")
        indicator.remove_class("unsaved")
        indicator.add_class(state)
        self._last_save_message = text

    def _is_dirty(self) -> bool:
        indicator = self.query_one("#save-indicator", Label)
        return "unsaved" in indicator.classes

    def _focus_editor(self) -> None:
        self.query_one("#editor", TextArea).focus()

    def _focus_exercise(self) -> None:
        if self._using_markdown_fallback:
            self.query_one("#exercise-fallback", VerticalScroll).focus()
        else:
            self.query_one("#exercise-markdown", MarkdownViewer).focus()

    def _start_sprint(self, seconds: int) -> None:
        self._suppress_autosave = True
        self._sprint_seconds_remaining = seconds
        if self._sprint_timer is not None:
            self._sprint_timer.stop()
        self._sprint_timer = self.set_interval(1.0, self._tick_sprint)
        self._focus_editor()
        self._update_word_count()

    def _tick_sprint(self) -> None:
        if self._sprint_seconds_remaining <= 0:
            return
        self._sprint_seconds_remaining -= 1
        self._update_word_count()
        if self._sprint_seconds_remaining == 0:
            if self._sprint_timer is not None:
                self._sprint_timer.stop()
                self._sprint_timer = None
            self._suppress_autosave = False
            self.bell()
            self._update_word_count()
            if self._is_dirty():
                self.run_worker(self._save_current_entry("sprint"), exclusive=False)

    def _current_ratio_string(self) -> str:
        left, right = SIDE_RATIOS[self.side_ratio_index]
        return f"{left}/{right}"


def run() -> None:
    app = GBTWApp()
    app.run()


if __name__ == "__main__":
    run()
