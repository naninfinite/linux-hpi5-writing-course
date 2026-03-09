from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from datetime import datetime

from rich.markdown import Markdown
from rich.text import Text
from textual import on
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.types import NoActiveAppError
from textual.widgets import (
    Button,
    Input,
    Label,
    MarkdownViewer,
    OptionList,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)
from textual.widgets.option_list import Option

from .content import ContentIndex, Exercise, ProjectGroup, load_content_index
from .db import Database, EntryRecord, ProjectDocRecord, ProjectEntryRecord
from .profiles import (
    ProfileRecord,
    ProfileRegistryError,
    ProfileStore,
    ProfileValidationError,
)

SIDE_RATIOS: tuple[tuple[int, int], ...] = ((40, 60), (50, 50), (60, 40))
BUILTIN_CATEGORIES: tuple[str, ...] = ("Characters", "Locations", "Worldbuilding", "Plot Notes", "Notes")
LAYOUTS = {"read", "side", "stack", "freewrite", "exercise", "project"}
WRITING_LAYOUTS = {"side", "stack", "freewrite", "exercise", "project"}
SHORTCUT_TEXT = """\
F1  Reading mode
F2  Side split
F3  Stack split
F4  Freewrite mode
F5  Exercise mode
F6  Project mode

[   decrease split ratio
]   increase split ratio

Ctrl+N  next exercise
Ctrl+P  previous exercise
Ctrl+S  save
Ctrl+J  start new long-term session now

Ctrl+E  exercise list
Ctrl+H  history modal (current section or project)
Ctrl+T  timed writing sprint modal
Ctrl+U  profile switcher

Tab     switch focus between panes (Write editor: indent)
Space Space  insert ". " in Write editor
?       keyboard help

Ctrl+Q  quit
"""

TYPE_TITLE_STYLES = {
    "reading": "bold #8fbcd4",
    "exercise": "bold #e6d3a2",
    "long-term": "bold #93c58f",
}
TYPE_COLORS = {
    "reading": "#8fbcd4",
    "exercise": "#e6d3a2",
    "long-term": "#93c58f",
}
STATUS_COLORS = {
    "optional": "#e9b96e",
    "archived": "#7b8794",
    "ongoing": "#6eb6a4",
}
PART_COLOR = "#d2c36b"
MODULE_COLOR = "#b7c0c7"
TEXT_COLOR = "#d9d5cf"
MUTED_TEXT_COLOR = "#9dacb5"
CURRENT_MARKER_COLOR = "#a9c3d3"
PROJECT_META_COLOR = "#91a3ad"
PROJECT_INDICATOR_COLOR = "#c2b268"


def word_count(text: str) -> int:
    return len([word for word in text.split() if word.strip()])


def format_entry_label(entry: EntryRecord | ProjectEntryRecord) -> str:
    timestamp = entry.updated_at.astimezone().strftime("%Y-%m-%d %H:%M")
    preview = entry.content.strip().splitlines()[0] if entry.content.strip() else "(empty)"
    return f"{timestamp}  {preview[:60]}"


def format_project_indicator(exercise: Exercise | None) -> str:
    if exercise is None or exercise.project_key is None:
        return ""
    role = exercise.effective_project_role or "contributor"
    if exercise.project_seed:
        if role == "seed":
            return f"{exercise.project_key} · seed"
        if exercise.project_role is None:
            return f"{exercise.project_key} · seed"
        return f"{exercise.project_key} · {role} [seed]"
    return f"{exercise.project_key} · {role}"


def format_footer_control_label(
    label: str,
    *,
    show_indicator: bool = False,
    accent_indicator: bool = True,
) -> Text:
    text = Text(label)
    if not show_indicator:
        return text
    text.append(" ")
    if accent_indicator:
        text.append("•", style=PROJECT_INDICATOR_COLOR)
    else:
        text.append("•")
    return text


def format_exercise_option_label(exercise: Exercise, current_exercise_id: str | None) -> Text:
    label = Text("    ")
    label.append(exercise.title, style=TYPE_COLORS.get(exercise.type, TEXT_COLOR))
    if exercise.status in {"optional", "archived", "ongoing"}:
        label.append(f" [{exercise.status}]", style=STATUS_COLORS.get(exercise.status, MUTED_TEXT_COLOR))
    if exercise.exercise_id == current_exercise_id:
        label.append(" *", style=f"bold {CURRENT_MARKER_COLOR}")
    return label


def format_project_summary_option(title: str, group: ProjectGroup) -> Text:
    parts_prefix = "Part" if len(group.parts) == 1 else "Parts"
    parts_text = ", ".join(str(part) for part in group.parts)
    seed_title = group.seed.title if group.seed is not None else "none"
    summary = Text(title, style=f"bold {PART_COLOR}")
    summary.append(f" · {parts_prefix} {parts_text}", style=MODULE_COLOR)
    summary.append(f" · {group.contributor_count} docs", style=MUTED_TEXT_COLOR)
    summary.append(" · seed: ", style=MUTED_TEXT_COLOR)
    summary.append(seed_title, style=TEXT_COLOR if group.seed is not None else MUTED_TEXT_COLOR)
    return summary


def format_project_contributor_option(exercise: Exercise, current_exercise_id: str | None) -> Text:
    label = Text(f"Part {exercise.part} · ", style=f"bold {PART_COLOR}")
    label.append(exercise.title, style=TYPE_COLORS.get(exercise.type, TEXT_COLOR))
    if exercise.project_seed:
        label.append(" [seed]", style=TYPE_COLORS["long-term"])
    if exercise.project_role == "consolidator":
        label.append(" [end]", style=TYPE_COLORS["exercise"])
    if exercise.status == "ongoing":
        label.append(" [ongoing]", style=STATUS_COLORS["ongoing"])
    if exercise.exercise_id == current_exercise_id:
        label.append(" *", style=f"bold {CURRENT_MARKER_COLOR}")
    return label


@dataclass(slots=True)
class TimedDraftState:
    started_at: datetime | None = None
    active_seconds: int = 0
    typed_chars: int = 0
    locked: bool = False


class WritingTextArea(TextArea):
    """TextArea tuned for prose drafting interactions."""

    def __init__(self, text: str = "", **kwargs) -> None:
        kwargs.setdefault("tab_behavior", "indent")
        super().__init__(text, **kwargs)

    async def _on_key(self, event: events.Key) -> None:
        if not self.read_only and event.key == "space" and self._replace_previous_space_with_period():
            self._restart_blink()
            event.stop()
            event.prevent_default()
            return
        if not self.read_only and self._auto_capitalize_character(event):
            self._restart_blink()
            event.stop()
            event.prevent_default()
            start, end = self.selection
            character = event.character
            assert character is not None
            self.replace(character.upper(), start, end)
            return
        await super()._on_key(event)

    def _replace_previous_space_with_period(self) -> bool:
        start, end = self.selection
        if start != end:
            return False
        row, column = self.cursor_location
        if column < 2:
            return False
        line = self.document.get_line(row)
        if column > len(line) or line[column - 1] != " ":
            return False
        preceding = line[column - 2]
        if preceding.isspace() or preceding in ".!?":
            return False
        self.replace(". ", (row, column - 1), (row, column))
        return True

    def _auto_capitalize_character(self, event: events.Key) -> bool:
        if not event.is_printable or event.character is None:
            return False
        character = event.character
        if len(character) != 1 or not character.isalpha() or not character.islower():
            return False
        start, end = self.selection
        if start != end:
            return False
        row, column = self.cursor_location
        if row == 0 and column == 0:
            return True
        line = self.document.get_line(row)
        if column > len(line):
            return False
        scan_row = row
        scan_column = column
        while True:
            if scan_column == 0:
                if scan_row == 0:
                    return True
                scan_row -= 1
                scan_column = len(self.document.get_line(scan_row))
                continue
            scan_column -= 1
            previous = self.document.get_line(scan_row)[scan_column]
            if previous.isspace():
                continue
            return previous == "."


class NewItemScreen(ModalScreen[str | None]):
    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt_text = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="modal"):
            yield Static(self._prompt_text, classes="modal-title")
            yield Input(placeholder="Enter name…", id="new-item-input")

    def on_mount(self) -> None:
        self.query_one("#new-item-input", Input).focus()

    @on(Input.Submitted, "#new-item-input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        self.dismiss(value if value else None)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(None)


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

    def __init__(self, entry: EntryRecord | ProjectEntryRecord) -> None:
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


class HistoryScreen(ModalScreen[EntryRecord | ProjectEntryRecord | None]):
    BINDINGS = [Binding("escape", "dismiss_screen", "Close", show=False)]

    def __init__(self, title: str, entries: list[EntryRecord | ProjectEntryRecord]) -> None:
        super().__init__()
        self.title = title
        self.entries = entries

    def compose(self) -> ComposeResult:
        with Container(id="modal", classes="wide-modal"):
            yield Static(f"History: {self.title}", classes="modal-title")
            if not self.entries:
                yield Static("No saved entries yet.", id="history-empty")
            else:
                options = [
                    Option(format_entry_label(entry), id=str(entry.id))
                    for entry in self.entries
                ]
                yield OptionList(*options, id="history-list")
            yield Button("Close", id="history-close")

    def on_mount(self) -> None:
        if self.entries:
            self.query_one("#history-list", OptionList).focus()
        else:
            self.query_one("#history-close", Button).focus()

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
        Binding("tab", "next_tab", "Next Tab", show=False),
        Binding("shift+tab", "previous_tab", "Previous Tab", show=False),
        Binding("left", "focus_project_list", "Projects", show=False),
        Binding("right", "focus_project_documents", "Documents", show=False),
    ]

    show_archived = reactive(False)
    TAB_EXERCISES = "exercise-tab"
    TAB_PROJECTS = "projects-tab"

    def __init__(
        self,
        content_index: ContentIndex,
        current_exercise_id: str | None,
        project_titles: dict[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.content_index = content_index
        self.current_exercise_id = current_exercise_id
        self.project_titles = project_titles or {}

    def compose(self) -> ComposeResult:
        with Container(id="modal", classes="wide-modal"):
            yield Static("Exercise List", classes="modal-title")
            yield Label("", id="exercise-list-hint")
            with TabbedContent(initial=self.TAB_EXERCISES, id="exercise-list-tabs"):
                with TabPane("Exercises", id=self.TAB_EXERCISES):
                    yield OptionList(id="exercise-list")
                with TabPane("Projects", id=self.TAB_PROJECTS):
                    with Horizontal(id="projects-browser"):
                        with Vertical(classes="projects-pane"):
                            yield Static("Projects", classes="project-pane-title")
                            yield OptionList(id="project-list")
                        with Vertical(classes="projects-pane"):
                            yield Static("Contributors", id="project-documents-title", classes="project-pane-title")
                            yield OptionList(id="project-document-list")
            yield Button("Close", id="exercise-list-close")

    def on_mount(self) -> None:
        self._refresh_options()
        self._refresh_project_options()
        self.query_one("#exercise-list", OptionList).focus()
        self._update_hint()

    def action_dismiss_screen(self) -> None:
        self.dismiss(None)

    def action_toggle_archived(self) -> None:
        self.show_archived = not self.show_archived
        self._refresh_options()
        self._update_hint()

    def action_next_tab(self) -> None:
        self._set_active_tab(self.TAB_PROJECTS if self._active_tab() == self.TAB_EXERCISES else self.TAB_EXERCISES)

    def action_previous_tab(self) -> None:
        self.action_next_tab()

    def action_focus_project_list(self) -> None:
        if self._active_tab() != self.TAB_PROJECTS:
            return
        project_list = self.query_one("#project-list", OptionList)
        if project_list.option_count == 0:
            self.app.bell()
            return
        project_list.focus()

    def action_focus_project_documents(self) -> None:
        if self._active_tab() != self.TAB_PROJECTS:
            return
        document_list = self.query_one("#project-document-list", OptionList)
        if document_list.option_count == 0:
            self.app.bell()
            return
        document_list.focus()

    @on(OptionList.OptionSelected, "#exercise-list")
    def on_exercise_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option_id
        if option_id and option_id.startswith("exercise:"):
            self.dismiss(option_id.split(":", 1)[1])

    @on(OptionList.OptionHighlighted, "#project-list")
    def on_project_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        option_id = event.option_id
        if option_id and option_id.startswith("project:"):
            self._refresh_project_documents(option_id.split(":", 1)[1])

    @on(OptionList.OptionSelected, "#project-list")
    def on_project_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option_id
        if option_id and option_id.startswith("project:"):
            self._refresh_project_documents(option_id.split(":", 1)[1])
            self.action_focus_project_documents()

    @on(OptionList.OptionSelected, "#project-document-list")
    def on_project_document_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option_id
        if option_id and option_id.startswith("exercise:"):
            self.dismiss(option_id.split(":", 1)[1])

    @on(TabbedContent.TabActivated, "#exercise-list-tabs")
    def on_tab_activated(self, _event: TabbedContent.TabActivated) -> None:
        self._update_hint()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "exercise-list-close":
            self.dismiss(None)

    def _refresh_options(self) -> None:
        hint = self.query_one("#exercise-list-hint", Label)
        hint.update(
            "Grouped by Part and Module. Enter opens an exercise. "
            "Ctrl+A toggles archived exercises "
            f"({'shown' if self.show_archived else 'hidden'})"
        )
        option_list = self.query_one("#exercise-list", OptionList)
        options: list[Option | None] = []
        for part, exercises in self.content_index.grouped_by_part(include_archived=self.show_archived).items():
            options.append(
                Option(
                    Text(f"Part {part}", style=f"bold {PART_COLOR}"),
                    id=f"header:{part}",
                    disabled=True,
                )
            )
            current_module: str | None = None
            module_index = 0
            for exercise in exercises:
                if exercise.module != current_module:
                    current_module = exercise.module
                    module_index += 1
                    options.append(
                        Option(
                            Text(f"  {current_module}", style=MODULE_COLOR),
                            id=f"module:{part}:{module_index}",
                            disabled=True,
                        )
                    )
                options.append(
                    Option(
                        format_exercise_option_label(exercise, self.current_exercise_id),
                        id=f"exercise:{exercise.exercise_id}",
                    )
                )
        option_list.clear_options()
        option_list.add_options(options)

    def _refresh_project_options(self) -> None:
        project_list = self.query_one("#project-list", OptionList)
        groups = self._project_groups_for_display()
        options: list[Option] = []
        for group in groups:
            options.append(
                Option(
                    format_project_summary_option(self._project_title_for_group(group), group),
                    id=f"project:{group.project_key}",
                )
            )
        project_list.clear_options()
        project_list.add_options(options)
        if not groups:
            self._refresh_project_documents(None)
            return
        current_project_key = None
        current_exercise = self.content_index.get(self.current_exercise_id) if self.current_exercise_id else None
        if current_exercise is not None:
            current_project_key = current_exercise.project_key
        selected_index = next(
            (
                index
                for index, group in enumerate(groups)
                if group.project_key == current_project_key
            ),
            0,
        )
        project_list.highlighted = selected_index
        self._refresh_project_documents(groups[selected_index].project_key)

    def _refresh_project_documents(self, project_key: str | None) -> None:
        title = self.query_one("#project-documents-title", Static)
        option_list = self.query_one("#project-document-list", OptionList)
        group = self._project_group_map().get(project_key or "")
        option_list.clear_options()
        if group is None:
            title.update("Contributors")
            return
        title.update(f"{self._project_title_for_group(group)} contributors")
        options = [
            Option(
                format_project_contributor_option(item.exercise, self.current_exercise_id),
                id=f"exercise:{item.exercise.exercise_id}",
            )
            for item in group.contributors
        ]
        option_list.add_options(options)
        current_index = next(
            (
                index
                for index, item in enumerate(group.contributors)
                if item.exercise.exercise_id == self.current_exercise_id
            ),
            0,
        )
        option_list.highlighted = current_index

    def _project_groups_for_display(self) -> list[ProjectGroup]:
        groups = list(self.content_index.project_groups())
        groups.sort(
            key=lambda group: (
                self._project_title_for_group(group).lower(),
                group.project_key.lower(),
            )
        )
        return groups

    def _project_group_map(self) -> dict[str, ProjectGroup]:
        return {group.project_key: group for group in self._project_groups_for_display()}

    def _project_title_for_group(self, group: ProjectGroup) -> str:
        return self.project_titles.get(group.project_key) or group.legacy_title or group.project_key

    def _active_tab(self) -> str:
        return self.query_one("#exercise-list-tabs", TabbedContent).active

    def _set_active_tab(self, tab_id: str) -> None:
        tabs = self.query_one("#exercise-list-tabs", TabbedContent)
        tabs.active = tab_id
        self._update_hint()
        if tab_id == self.TAB_PROJECTS:
            self.action_focus_project_list()
            return
        self.query_one("#exercise-list", OptionList).focus()

    def _update_hint(self) -> None:
        hint = self.query_one("#exercise-list-hint", Label)
        if self._active_tab() == self.TAB_PROJECTS:
            hint.update(
                "Projects across parts. Enter on a project shows contributors. "
                "Enter on a contributor opens it. Left/Right moves between lists."
            )
            return
        hint.update(
            "Grouped by Part and Module. Enter opens an exercise. "
            "Ctrl+A toggles archived exercises "
            f"({'shown' if self.show_archived else 'hidden'}). Tab switches to Projects."
        )


@dataclass(slots=True, frozen=True)
class ProfilePickerResult:
    action: str
    profile_id: str | None = None


class ProfilePickerScreen(ModalScreen[ProfilePickerResult | None]):
    BINDINGS = [
        Binding("enter", "open_selected", "Open", show=False),
        Binding("n", "new_profile", "New", show=False),
        Binding("r", "rename_selected", "Rename", show=False),
        Binding("escape", "quit_picker", "Close", show=False),
    ]

    def __init__(
        self,
        profiles: list[ProfileRecord],
        *,
        selected_profile_id: str | None = None,
        message: str = "",
        message_style: str = "profile-help",
        close_label: str = "Close",
    ) -> None:
        super().__init__()
        self.profiles = profiles
        self.selected_profile_id = selected_profile_id
        self.message = message
        self.message_style = message_style
        self.close_label = close_label

    def compose(self) -> ComposeResult:
        with Container(id="profile-picker"):
            yield Static("Choose Profile", id="profile-picker-title")
            yield Static(
                self.message or "Each profile keeps its own progress, preferences, and project history.",
                id="profile-picker-message",
                classes=self.message_style,
            )
            options = [
                Option(
                    Text.assemble(
                        (profile.display_name, "bold #e7dfcf"),
                        ("  "),
                        (profile.profile_id, MUTED_TEXT_COLOR),
                    ),
                    id=profile.profile_id,
                )
                for profile in self.profiles
            ]
            yield OptionList(*options, id="profile-picker-list")
            with Horizontal(id="profile-picker-actions", classes="button-row"):
                yield Button("Open", id="profile-open")
                yield Button("Rename", id="profile-rename")
                yield Button("New Profile", id="profile-new")
                yield Button(self.close_label, id="profile-quit")

    def on_mount(self) -> None:
        option_list = self.query_one("#profile-picker-list", OptionList)
        if self.profiles:
            selected_index = next(
                (
                    index
                    for index, profile in enumerate(self.profiles)
                    if profile.profile_id == self.selected_profile_id
                ),
                0,
            )
            option_list.highlighted = selected_index
            option_list.focus()
        else:
            self.query_one("#profile-new", Button).focus()

    def action_open_selected(self) -> None:
        option_list = self.query_one("#profile-picker-list", OptionList)
        if not self.profiles:
            self.app.bell()
            return
        highlighted = option_list.highlighted
        if highlighted is None:
            highlighted = 0
        selected_index = max(0, min(highlighted, len(self.profiles) - 1))
        selected = self.profiles[selected_index]
        if selected is None:
            self.app.bell()
            return
        self.dismiss(ProfilePickerResult("open", selected.profile_id))

    def action_new_profile(self) -> None:
        self.dismiss(ProfilePickerResult("new"))

    def action_rename_selected(self) -> None:
        option_list = self.query_one("#profile-picker-list", OptionList)
        if not self.profiles:
            self.app.bell()
            return
        highlighted = option_list.highlighted
        if highlighted is None:
            highlighted = 0
        selected_index = max(0, min(highlighted, len(self.profiles) - 1))
        selected = self.profiles[selected_index]
        self.dismiss(ProfilePickerResult("rename", selected.profile_id))

    def action_quit_picker(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#profile-picker-list")
    def on_profile_selected(self, _event: OptionList.OptionSelected) -> None:
        self.action_open_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "profile-open":
            self.action_open_selected()
        elif event.button.id == "profile-rename":
            self.action_rename_selected()
        elif event.button.id == "profile-new":
            self.action_new_profile()
        elif event.button.id == "profile-quit":
            self.action_quit_picker()


class BlockingMessageScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss_message", "Close", show=False)]

    def __init__(self, title: str, message: str, button_label: str = "Quit") -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._button_label = button_label

    def compose(self) -> ComposeResult:
        with Container(id="blocking-screen"):
            yield Static(self._title, id="blocking-title")
            yield Static(self._message, id="blocking-message")
            yield Button(self._button_label, id="blocking-close")

    def action_dismiss_message(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "blocking-close":
            self.dismiss(None)


class FooterControl(Static):
    can_focus = True
    BINDINGS = [
        Binding("enter", "activate", show=False),
        Binding("space", "activate", show=False),
    ]

    class Pressed(Message):
        def __init__(self, control_id: str) -> None:
            super().__init__()
            self.control_id = control_id

    def __init__(self, label: str, *, control_id: str) -> None:
        self._label = label
        self._show_indicator = False
        self._is_active = False
        super().__init__(
            format_footer_control_label(label),
            id=control_id,
            classes="footer-control",
        )
        self._is_disabled = False
        self._detail = ""

    def set_active(self, active: bool) -> None:
        self._is_active = active
        self.set_class(active, "active-mode")
        self._sync_label()

    def set_disabled(self, disabled: bool) -> None:
        self._is_disabled = disabled
        self.set_class(disabled, "disabled")
        self._sync_label()
        self._sync_tooltip()

    def set_detail(self, detail: str) -> None:
        self._detail = detail
        self._sync_tooltip()

    def set_indicator(self, show_indicator: bool) -> None:
        self._show_indicator = show_indicator
        self._sync_label()

    def _sync_label(self) -> None:
        try:
            self.update(
                format_footer_control_label(
                    self._label,
                    show_indicator=self._show_indicator,
                    accent_indicator=not self._is_active and not self._is_disabled,
                )
            )
        except NoActiveAppError:
            return

    def _sync_tooltip(self) -> None:
        self.tooltip = None if self._is_disabled or not self._detail else self._detail

    def action_activate(self) -> None:
        if self._is_disabled:
            return
        if self.id is not None:
            self.post_message(self.Pressed(self.id))

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.action_activate()


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
        layout: vertical;
    }

    #writing-pane {
        background: #0f1519;
        layout: vertical;
    }

    #exercise-header {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        background: #20282d;
        color: #d9d5cf;
        border-bottom: solid #31393f;
    }

    #exercise-title {
        text-style: bold;
        color: #e7dfcf;
    }

    #exercise-meta {
        color: #aeb6bb;
    }

    #exercise-markdown, #exercise-fallback {
        height: 1fr;
        width: 100%;
    }

    #exercise-fallback {
        padding: 0 1 1 1;
    }

    #exercise-fallback-view {
        width: 100%;
        height: auto;
        color: #e3ded7;
    }

    #editor {
        height: 1fr;
        background: #0f1519;
    }

    #draft-toolbar {
        height: auto;
        padding: 0 0 0 0;
        margin-bottom: 1;
        align-vertical: middle;
        background: #131a1f;
        border: solid #273139;
    }

    #draft-label {
        width: 1fr;
        color: #c4cdd2;
        padding: 0 1;
    }

    #write-session-stats {
        height: auto;
        color: #8fbcd4;
        padding: 0 1;
        margin-bottom: 1;
    }

    .draft-button {
        min-width: 0;
        height: auto;
        margin-right: 0;
        padding: 0 1;
        color: #afbbc2;
        background: transparent;
        border: none;
    }

    .draft-button:hover {
        background: #212b32;
    }

    #draft-delete {
        color: #e1aaa4;
    }

    #draft-undo {
        color: #9fc8a9;
    }

    #bottom-bar {
        height: auto;
        padding: 0 1;
        background: #1a1f22;
        color: #e8e6e3;
        border-top: solid #2f353b;
        layout: horizontal;
    }

    .button-row {
        height: auto;
    }

    #left-buttons {
        width: 1fr;
    }

    #status-strip {
        width: auto;
        height: auto;
        align-horizontal: right;
    }

    #status-strip Label {
        margin-left: 2;
    }

    #word-count {
        color: #a9c3d3;
    }

    Button {
        margin-right: 1;
    }

    .footer-control {
        width: auto;
        min-width: 2;
        height: auto;
        padding: 0 1;
        margin-right: 0;
        color: #dad7d2;
        background: transparent;
    }

    .footer-control:focus {
        background: #2a3339;
    }

    .footer-control.active-mode {
        background: #d2c36b;
        color: #171717;
        text-style: bold;
    }

    .footer-control.disabled {
        color: #6c757d;
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

    #show-exercises {
        color: #8fbcd4;
    }

    #previous-exercise, #next-exercise {
        color: #d2c36b;
    }

    #help-button {
        color: #b7c0c7;
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

    #profile-picker, #blocking-screen {
        width: 100%;
        height: 100%;
        padding: 2 3;
        background: #101316;
    }

    #profile-picker-title, #blocking-title {
        color: #d2c36b;
        text-style: bold;
        margin-bottom: 1;
    }

    #profile-picker-title {
        width: 100%;
        content-align: center middle;
    }

    #profile-picker-message, #blocking-message {
        width: 100%;
        margin-bottom: 1;
    }

    #profile-picker-message.profile-help {
        color: #b8c4cb;
    }

    #profile-picker-message.profile-error, #blocking-message {
        color: #ef6b73;
    }

    #profile-picker-list {
        height: 1fr;
        margin-bottom: 1;
    }

    #profile-picker-actions {
        height: auto;
    }

    .modal-title {
        color: #d2c36b;
        text-style: bold;
        margin-bottom: 1;
    }

    #help-text, #preview-scroll, #history-list, #exercise-list, #project-list, #project-document-list, #exercise-list-tabs, #projects-browser {
        height: 1fr;
    }

    #help-text {
        color: #ddd7cf;
    }

    #exercise-list-hint, #history-empty {
        color: #9dacb5;
    }

    #exercise-list-tabs {
        margin-bottom: 1;
    }

    #exercise-list-tabs TabPane {
        padding: 0;
    }

    .projects-pane {
        width: 1fr;
        height: 1fr;
    }

    .projects-pane:first-child {
        margin-right: 1;
    }

    .project-pane-title {
        color: #b8c4cb;
        margin-bottom: 1;
        text-style: bold;
    }

    #preview-body {
        color: #e3ded7;
    }

    OptionList {
        background: #171d21;
        border: tall #4d5a63;
        color: #d9d5cf;
    }

    OptionList:focus {
        border: tall #6c7d89;
        background-tint: #d9d5cf 2%;
    }

    OptionList > .option-list--option {
        color: #d9d5cf;
    }

    OptionList > .option-list--option-disabled {
        color: #93a3ad;
        text-style: bold;
    }

    OptionList > .option-list--option-highlighted {
        background: #d2c36b;
        color: #171717;
        text-style: bold;
    }

    OptionList > .option-list--option-hover {
        background: #283137;
    }

    OptionList > .option-list--separator {
        color: #51606b;
    }

    #history-empty {
        margin-bottom: 1;
    }

    .field-label {
        margin-top: 1;
    }

    #project-pane {
        width: 100%;
        height: 100%;
        layout: vertical;
        background: #101316;
    }

    #project-nav {
        height: auto;
        padding: 0 1;
        background: #1a2024;
        border-bottom: solid #2f353b;
        align-vertical: middle;
    }

    #project-name-label {
        width: 1fr;
        content-align: center middle;
        color: #e7dfcf;
        text-style: bold;
        padding: 0 1;
    }

    .proj-nav-btn {
        min-width: 3;
        height: auto;
        padding: 0 1;
        background: transparent;
        border: none;
        color: #aeb6bb;
    }

    .proj-nav-btn:hover {
        background: #212b32;
    }

    #project-body {
        height: 1fr;
    }

    #project-tree-pane {
        width: 26;
        height: 1fr;
        border-right: solid #2f353b;
        background: #131a1f;
    }

    #project-editor-pane {
        width: 1fr;
        height: 1fr;
        layout: vertical;
        background: #0f1519;
        padding: 1 1;
    }

    #project-doc-title {
        height: auto;
        margin-bottom: 1;
        background: transparent;
        border: none;
        border-bottom: solid #31393f;
        color: #e7dfcf;
        padding: 0 0;
    }

    #project-doc-title:focus {
        border: none;
        border-bottom: solid #6c7d89;
        background: transparent;
    }

    #project-doc-editor {
        height: 1fr;
        background: #0f1519;
    }

    #project-tree {
        height: 1fr;
        background: #131a1f;
        border: none;
        padding: 0;
    }

    #project-tree:focus {
        background: #131a1f;
        background-tint: #d9d5cf 1%;
    }

    #project-empty-hint {
        color: #6c7d89;
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("f1", "set_mode('read')", "Read", show=False),
        Binding("f2", "set_mode('side')", "Side", show=False),
        Binding("f3", "set_mode('stack')", "Stack", show=False),
        Binding("f4", "set_mode('freewrite')", "Freewrite", show=False),
        Binding("f5", "set_mode('exercise')", "Exercise", show=False),
        Binding("f6", "set_mode('project')", "Project", show=False),
        Binding("[", "decrease_split", "Split -", show=False),
        Binding("]", "increase_split", "Split +", show=False),
        Binding("ctrl+n", "next_exercise", "Next", show=False),
        Binding("ctrl+p", "previous_exercise", "Previous", show=False),
        Binding("ctrl+s", "save", "Save", show=False),
        Binding("ctrl+j", "new_session_now", "New Session", show=False),
        Binding("ctrl+e", "show_exercise_list", "Exercises", show=False),
        Binding("ctrl+h", "show_history", "History", show=False),
        Binding("ctrl+t", "show_sprint", "Sprint", show=False),
        Binding("ctrl+u", "show_profiles", "Profiles", show=False),
        Binding("tab", "toggle_focus", "Focus", show=False),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
    ]

    def __init__(
        self,
        *,
        database: Database | None = None,
        profile_store: ProfileStore | None = None,
        content_index: ContentIndex | None = None,
        autosave_delay_seconds: float = 4.0,
        sprint_tick_seconds: float = 1.0,
    ) -> None:
        super().__init__()
        self._database_was_injected = database is not None
        self.database: Database | None = database
        self.profile_store = profile_store or ProfileStore()
        self.content_index = content_index or load_content_index()
        if self.database is not None:
            self._sync_database_projects()
        self.autosave_delay_seconds = autosave_delay_seconds
        self.sprint_tick_seconds = sprint_tick_seconds
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
        self._save_indicator_state = "saved"
        self._last_deleted_freewrite: EntryRecord | None = None
        self.active_project_key: str | None = None
        self.current_project_doc_id: int | None = None
        self._project_tree_expanded: dict[str, bool] = {}
        self._project_doc_dirty: bool = False
        self._project_doc_autosave_gen: int = 0
        self._project_doc_save_gen: int = 0
        self._loading_project_doc: bool = False
        self.current_profile: ProfileRecord | None = None
        self._timed_limit_seconds = 10 * 60
        self._timed_first_exercise_id = self._first_writable_exercise_id()
        self._timed_state: TimedDraftState | None = None
        self._timed_entry_id: int | None = None
        self._timed_last_input_at: datetime | None = None
        self._timed_last_text_length = 0
        self._timed_state_dirty = False
        self._timed_tick_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Container(id="workspace"):
            with Container(id="exercise-pane"):
                yield Label("", id="exercise-title")
                yield Label("", id="exercise-meta")
                yield MarkdownViewer("", id="exercise-markdown", show_table_of_contents=False)
                with VerticalScroll(id="exercise-fallback"):
                    yield Static("", id="exercise-fallback-view")
            with Container(id="writing-pane"):
                with Horizontal(id="draft-toolbar"):
                    yield Button("<", id="draft-prev", classes="draft-button")
                    yield Label("", id="draft-label")
                    yield Button(">", id="draft-next", classes="draft-button")
                    yield Button("New", id="draft-new", classes="draft-button")
                    yield Button("Del", id="draft-delete", classes="draft-button")
                    yield Button("Undo", id="draft-undo", classes="draft-button")
                yield Label("", id="write-session-stats")
                yield WritingTextArea("", id="editor")
            with Container(id="project-pane"):
                with Horizontal(id="project-nav"):
                    yield Button("<", id="proj-prev", classes="proj-nav-btn")
                    yield Label("", id="project-name-label")
                    yield Button(">", id="proj-next", classes="proj-nav-btn")
                with Horizontal(id="project-body"):
                    with Vertical(id="project-tree-pane"):
                        yield OptionList(id="project-tree")
                    with Vertical(id="project-editor-pane"):
                        yield Input(placeholder="Document title", id="project-doc-title")
                        yield TextArea("", id="project-doc-editor")
        with Horizontal(id="bottom-bar"):
            with Horizontal(id="left-buttons", classes="button-row"):
                yield FooterControl("Read", control_id="mode-read")
                yield FooterControl("Side", control_id="mode-side")
                yield FooterControl("Stack", control_id="mode-stack")
                yield FooterControl("Write", control_id="mode-freewrite")
                yield FooterControl("Exercise", control_id="mode-exercise")
                yield FooterControl("Project", control_id="mode-project")
                yield Static("|")
                yield FooterControl("Exercises", control_id="show-exercises")
                yield FooterControl("Profiles", control_id="show-profiles")
                yield FooterControl("<", control_id="previous-exercise")
                yield FooterControl(">", control_id="next-exercise")
                yield FooterControl("?", control_id="help-button")
            with Horizontal(id="status-strip"):
                yield Label("Word count: 0", id="word-count")
                yield Label("Saved ✓", id="save-indicator", classes="saved")

    async def on_mount(self) -> None:
        self.title = "gbtw"
        self._refresh_sub_title()
        if self.database is None:
            self.run_worker(self._select_profile_and_start(), exclusive=True)
            return
        await self._initialize_after_database_ready()

    async def _select_profile_and_start(self) -> None:
        while self.database is None:
            selected = await self._run_profile_picker(close_label="Quit")
            if selected is None:
                self.exit()
                return
            self._open_selected_profile(selected)
            break
        if self.database is None:
            return
        await self._initialize_after_database_ready()

    async def _initialize_after_database_ready(self) -> None:
        self._restore_preferences()
        await self._load_initial_exercise()
        self._apply_layout()
        self._update_bottom_bar()
        self._refresh_sub_title()

    def _open_selected_profile(self, profile: ProfileRecord) -> None:
        refreshed_profile = self.profile_store.mark_used(profile.profile_id)
        self.current_profile = refreshed_profile
        self.database = Database(refreshed_profile.db_path)
        self._sync_database_projects()
        self._refresh_sub_title()

    async def _run_profile_picker(self, *, close_label: str) -> ProfileRecord | None:
        error_message = ""
        selected_profile_id = self.current_profile.profile_id if self.current_profile is not None else None
        while True:
            try:
                profiles = self.profile_store.list_profiles()
            except ProfileRegistryError as exc:
                await self.push_screen_wait(
                    BlockingMessageScreen(
                        "Profile Registry Error",
                        str(exc),
                    )
                )
                return None
            choice = await self.push_screen_wait(
                ProfilePickerScreen(
                    profiles,
                    selected_profile_id=selected_profile_id,
                    message=error_message,
                    message_style="profile-error" if error_message else "profile-help",
                    close_label=close_label,
                )
            )
            error_message = ""
            if choice is None:
                return None
            if choice.action == "new":
                new_name = await self.push_screen_wait(NewItemScreen("New profile name"))
                if new_name is None:
                    continue
                try:
                    created = self.profile_store.create_profile(new_name)
                except ProfileValidationError as exc:
                    self.bell()
                    error_message = str(exc)
                    continue
                selected_profile_id = created.profile_id
                continue
            if choice.action == "rename":
                if choice.profile_id is None:
                    self.bell()
                    continue
                current = self.profile_store.get_profile(choice.profile_id)
                if current is None:
                    self.bell()
                    error_message = "That profile no longer exists."
                    continue
                new_name = await self.push_screen_wait(NewItemScreen(f"Rename profile: {current.display_name}"))
                if new_name is None:
                    continue
                try:
                    renamed = self.profile_store.rename_profile(choice.profile_id, new_name)
                except ProfileValidationError as exc:
                    self.bell()
                    error_message = str(exc)
                    continue
                selected_profile_id = renamed.profile_id
                if self.current_profile is not None and renamed.profile_id == self.current_profile.profile_id:
                    self.current_profile = renamed
                    self._refresh_sub_title()
                continue
            if choice.action == "open" and choice.profile_id is not None:
                selected = self.profile_store.get_profile(choice.profile_id)
                if selected is None:
                    self.bell()
                    error_message = "That profile no longer exists."
                    continue
                return selected

    def _sync_database_projects(self) -> None:
        if self.database is None:
            return
        self.database.sync_projects(
            group.project_key for group in self.content_index.project_groups()
        )

    def _refresh_sub_title(self) -> None:
        parts = ["Getting Back to Writing"]
        if self.current_profile is not None:
            parts.append(self.current_profile.display_name)
        if self.content_index.warnings:
            parts.append(f"Skipped {len(self.content_index.warnings)} invalid content file(s)")
        self.sub_title = " · ".join(parts)

    async def action_set_mode(self, mode: str) -> None:
        if mode not in LAYOUTS:
            return
        if not self._can_use_mode(mode):
            self.bell()
            return
        previous_slot = self._current_editor_slot()
        previous_effective = self._effective_layout_mode()
        assert self.database is not None
        if previous_effective == "project" and self._project_doc_dirty:
            await self._save_project_doc("switch")
        self.current_layout_mode = mode
        self.database.set_preference("last_mode", self.current_layout_mode)
        if self.current_exercise is not None and previous_slot != self._current_editor_slot():
            if self._is_dirty():
                await self._save_current_entry("switch")
            self._load_editor_for_exercise(self.current_exercise)
        self._apply_layout()
        if self._effective_layout_mode() == "project":
            self.query_one("#project-tree", OptionList).focus()
        elif self._effective_layout_mode() == "read":
            self._focus_exercise()
        else:
            self._focus_editor()

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
        if self._effective_layout_mode() == "project":
            await self._save_project_doc("manual")
        else:
            await self._save_current_entry("manual")

    async def action_previous_draft(self) -> None:
        await self._cycle_freewrite_draft(-1)

    async def action_next_draft(self) -> None:
        await self._cycle_freewrite_draft(1)

    async def action_new_draft(self) -> None:
        await self._create_new_freewrite_draft()

    async def action_delete_draft(self) -> None:
        await self._delete_current_freewrite_draft()

    async def action_undo_delete_draft(self) -> None:
        await self._undo_deleted_freewrite_draft()

    async def action_new_session_now(self) -> None:
        if self._effective_layout_mode() == "project":
            self.bell()
            return
        if self.current_exercise is None or not self.current_exercise.is_long_term:
            self.bell()
            return
        if self.current_exercise.save_mode != "session":
            self.bell()
            return
        await self._create_new_session_entry()

    async def action_show_exercise_list(self) -> None:
        self.push_screen(
            ExerciseListScreen(
                self.content_index,
                self.current_exercise.exercise_id if self.current_exercise else None,
                self._content_project_titles(),
            ),
            callback=self._on_exercise_list_closed,
        )

    async def action_show_profiles(self) -> None:
        selected = await self._run_profile_picker(close_label="Close")
        if selected is None:
            return
        if self.current_profile is not None and selected.profile_id == self.current_profile.profile_id:
            refreshed = self.profile_store.get_profile(selected.profile_id)
            if refreshed is not None:
                self.current_profile = refreshed
                self._refresh_sub_title()
            return
        await self._switch_to_profile(selected)

    async def action_show_history(self) -> None:
        if self.current_exercise is None:
            self.bell()
            return
        assert self.database is not None
        if self._effective_layout_mode() == "project" and self._project_mode_available(self.current_exercise):
            entries = self.database.list_project_history(self.current_exercise.project_key or "")
            self.push_screen(
                HistoryScreen(self._project_title_for(self.current_exercise), entries),
                callback=self._on_history_closed,
            )
            return
        if not self.current_exercise.is_long_term:
            self.bell()
            return
        entries = self.database.list_history(
            self.current_exercise.exercise_id,
            self._current_draft_kind(),
        )
        self.push_screen(
            HistoryScreen(self.current_exercise.title, entries),
            callback=self._on_history_closed,
        )

    async def action_show_sprint(self) -> None:
        if not self._can_edit_current_target():
            self.bell()
            return
        self.push_screen(SprintScreen(), callback=self._on_sprint_closed)

    async def action_toggle_focus(self) -> None:
        if self._effective_layout_mode() == "project":
            focused = self.focused
            tree = self.query_one("#project-tree", OptionList)
            editor = self.query_one("#project-doc-editor", TextArea)
            if focused is tree:
                editor.focus()
            else:
                tree.focus()
            return
        if not self._can_edit_current_target():
            self._focus_exercise()
            return
        if self._effective_layout_mode() in {"freewrite", "exercise"}:
            self._focus_editor()
            return
        focused = self.focused
        editor = self.query_one("#editor", TextArea)
        if focused is editor:
            self._focus_exercise()
        else:
            self._focus_editor()

    async def action_quit_app(self) -> None:
        if self._effective_layout_mode() == "project" and self._project_doc_dirty:
            await self._save_project_doc("quit")
        if self._is_dirty():
            await self._save_current_entry("quit")
        self._persist_timed_state()
        self._stop_timed_tick_timer()
        if self.database is not None:
            self.database.close()
            self.database = None
        self.exit()

    async def on_unmount(self) -> None:
        self._persist_timed_state()
        self._stop_timed_tick_timer()
        if self.database is not None:
            self.database.close()
            self.database = None

    async def _switch_to_profile(self, profile: ProfileRecord) -> None:
        if self._effective_layout_mode() == "project" and self._project_doc_dirty:
            await self._save_project_doc("switch")
        if self._is_dirty():
            await self._save_current_entry("switch")
        self._persist_timed_state()
        self._stop_timed_tick_timer()
        if self.database is not None:
            self.database.close()
            self.database = None
        self._reset_runtime_state_for_profile_switch()
        self._open_selected_profile(profile)
        await self._initialize_after_database_ready()

    def _reset_runtime_state_for_profile_switch(self) -> None:
        self.current_exercise = None
        self.current_entry_id = None
        self.current_layout_mode = "side"
        self.side_ratio_index = 1
        self.active_project_key = None
        self.current_project_doc_id = None
        self._project_tree_expanded = {}
        self._project_doc_dirty = False
        self._project_doc_autosave_gen = 0
        self._project_doc_save_gen = 0
        self._loading_project_doc = False
        self._last_deleted_freewrite = None
        self._loading_editor = False
        self._ignored_loaded_text = None
        self._save_generation = 0
        self._autosave_generation = 0
        self._suppress_autosave = False
        self._sprint_seconds_remaining = 0
        self._using_markdown_fallback = False
        if self._sprint_timer is not None:
            self._sprint_timer.stop()
            self._sprint_timer = None
        self._clear_timed_state_runtime(persist=False)
        self._save_indicator_state = "saved"
        self._last_save_message = "Saved ✓"

    def _content_project_titles(self) -> dict[str, str]:
        titles: dict[str, str] = {}
        for group in self.content_index.project_groups():
            if self.database is not None:
                project = self.database.get_project(group.project_key)
                if project is not None:
                    titles[group.project_key] = project.title
                    continue
            titles[group.project_key] = group.legacy_title or group.project_key
        return titles

    def _first_writable_exercise_id(self) -> str | None:
        for exercise in self.content_index.visible_exercises():
            if exercise.type != "reading":
                return exercise.exercise_id
        return None

    def _timed_pref_key(self, entry_id: int) -> str:
        return f"timed_draft:{entry_id}"

    def _load_timed_state_for_entry(self, entry_id: int) -> TimedDraftState:
        if self.database is None:
            return TimedDraftState()
        raw = self.database.get_preference(self._timed_pref_key(entry_id))
        if not raw:
            return TimedDraftState()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return TimedDraftState()
        started_at: datetime | None = None
        raw_started_at = payload.get("started_at")
        if isinstance(raw_started_at, str):
            try:
                started_at = datetime.fromisoformat(raw_started_at)
            except ValueError:
                started_at = None
            if started_at is not None and started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=datetime.now().astimezone().tzinfo)
        try:
            active_seconds = max(0, int(payload.get("active_seconds", 0)))
        except (TypeError, ValueError):
            active_seconds = 0
        try:
            typed_chars = max(0, int(payload.get("typed_chars", 0)))
        except (TypeError, ValueError):
            typed_chars = 0
        locked = bool(payload.get("locked", False))
        return TimedDraftState(
            started_at=started_at,
            active_seconds=active_seconds,
            typed_chars=typed_chars,
            locked=locked,
        )

    def _persist_timed_state(self) -> None:
        if (
            self.database is None
            or self._timed_state is None
            or self._timed_entry_id is None
            or not self._timed_state_dirty
        ):
            return
        payload = {
            "started_at": self._timed_state.started_at.isoformat() if self._timed_state.started_at is not None else None,
            "active_seconds": self._timed_state.active_seconds,
            "typed_chars": self._timed_state.typed_chars,
            "locked": self._timed_state.locked,
        }
        self.database.set_preference(self._timed_pref_key(self._timed_entry_id), json.dumps(payload, separators=(",", ":")))
        self._timed_state_dirty = False

    def _stop_timed_tick_timer(self) -> None:
        if self._timed_tick_timer is not None:
            self._timed_tick_timer.stop()
            self._timed_tick_timer = None

    def _clear_timed_state_runtime(self, *, persist: bool = True) -> None:
        if persist:
            self._persist_timed_state()
        self._stop_timed_tick_timer()
        self._timed_state = None
        self._timed_entry_id = None
        self._timed_last_input_at = None
        self._timed_last_text_length = 0
        self._timed_state_dirty = False
        self._update_write_session_stats()

    def _timed_state_applies_to_entry(self, exercise: Exercise, entry_id: int) -> bool:
        return self._timed_first_exercise_id is not None and exercise.exercise_id == self._timed_first_exercise_id and entry_id > 0

    def _activate_timed_state_for_entry(self, exercise: Exercise, entry_id: int, text: str) -> None:
        if not self._timed_state_applies_to_entry(exercise, entry_id):
            self._clear_timed_state_runtime()
            return
        self._persist_timed_state()
        self._stop_timed_tick_timer()
        self._timed_entry_id = entry_id
        self._timed_state = self._load_timed_state_for_entry(entry_id)
        self._timed_last_input_at = None
        self._timed_last_text_length = len(text)
        self._refresh_timed_state(datetime.now().astimezone())
        self._ensure_timed_tick_timer()
        self._update_write_session_stats()

    def _timed_elapsed_seconds(self, now: datetime) -> int:
        if self._timed_state is None or self._timed_state.started_at is None:
            return 0
        return max(0, int((now - self._timed_state.started_at).total_seconds()))

    def _current_entry_locked_by_timer(self) -> bool:
        return (
            self._timed_state is not None
            and self._timed_entry_id is not None
            and self._timed_entry_id == self.current_entry_id
            and self._timed_state.locked
        )

    def _apply_current_editor_disabled_state(self) -> None:
        if self.current_exercise is None:
            return
        try:
            editor = self.query_one("#editor", TextArea)
        except Exception:
            return
        editor.disabled = not self._exercise_supports_writing(self.current_exercise) or self._current_entry_locked_by_timer()

    def _ensure_timed_tick_timer(self) -> None:
        if self._timed_state is None or self._timed_state.started_at is None or self._timed_state.locked:
            self._stop_timed_tick_timer()
            return
        if self._timed_tick_timer is None:
            self._timed_tick_timer = self.set_interval(1.0, self._tick_timed_draft)

    def _refresh_timed_state(self, now: datetime) -> None:
        if self._timed_state is None or self._timed_state.started_at is None or self._timed_state.locked:
            return
        if self._timed_elapsed_seconds(now) < self._timed_limit_seconds:
            return
        self._timed_state.locked = True
        self._timed_state_dirty = True
        self._stop_timed_tick_timer()
        self._apply_current_editor_disabled_state()
        if not self._is_dirty():
            self._set_save_indicator("10m limit reached", "saved")
        self.bell()
        self._update_write_session_stats()

    def _tick_timed_draft(self) -> None:
        if self._timed_state is None:
            self._stop_timed_tick_timer()
            return
        now = datetime.now().astimezone()
        if (
            not self._timed_state.locked
            and self._timed_last_input_at is not None
            and (now - self._timed_last_input_at).total_seconds() <= 1.2
        ):
            self._timed_state.active_seconds += 1
            self._timed_state_dirty = True
        self._refresh_timed_state(now)
        self._update_write_session_stats()

    def _handle_timed_editor_change(self, text: str) -> None:
        if self._timed_state is None or self._timed_state.locked:
            return
        now = datetime.now().astimezone()
        if self._timed_state.started_at is None and any(character.isalpha() for character in text):
            self._timed_state.started_at = now
            self._timed_state_dirty = True
            self._ensure_timed_tick_timer()
        delta = len(text) - self._timed_last_text_length
        if delta > 0:
            self._timed_state.typed_chars += delta
            self._timed_state_dirty = True
        self._timed_last_text_length = len(text)
        if self._timed_state.started_at is not None:
            self._timed_last_input_at = now
            self._refresh_timed_state(now)
        self._update_write_session_stats()

    def _update_write_session_stats(self) -> None:
        try:
            label = self.query_one("#write-session-stats", Label)
        except Exception:
            return
        visible = (
            self._timed_state is not None
            and self.current_exercise is not None
            and self._timed_entry_id == self.current_entry_id
            and self.current_exercise.exercise_id == self._timed_first_exercise_id
            and self._effective_layout_mode() != "project"
        )
        label.display = visible
        if not visible:
            label.update("")
            return
        now = datetime.now().astimezone()
        elapsed = min(self._timed_elapsed_seconds(now), self._timed_limit_seconds)
        elapsed_minutes, elapsed_seconds = divmod(elapsed, 60)
        limit_minutes, limit_seconds = divmod(self._timed_limit_seconds, 60)
        active_minutes = self._timed_state.active_seconds / 60.0
        status = "locked" if self._timed_state.locked else ("running" if self._timed_state.started_at else "ready")
        label.update(
            f"10m session {elapsed_minutes:02d}:{elapsed_seconds:02d}/{limit_minutes:02d}:{limit_seconds:02d}"
            f" · typed {self._timed_state.typed_chars} chars · active {active_minutes:.1f}m · {status}"
        )

    async def on_key(self, event: events.Key) -> None:
        if event.key in {"question_mark", "?"}:
            self.push_screen(HelpScreen())
            event.stop()

    @on(Button.Pressed)
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "draft-prev":
            await self.action_previous_draft()
        elif button_id == "draft-next":
            await self.action_next_draft()
        elif button_id == "draft-new":
            await self.action_new_draft()
        elif button_id == "draft-delete":
            await self.action_delete_draft()
        elif button_id == "draft-undo":
            await self.action_undo_delete_draft()
        elif button_id == "proj-prev":
            self._cycle_active_project(-1)
        elif button_id == "proj-next":
            self._cycle_active_project(1)

    @on(FooterControl.Pressed)
    async def on_footer_control_pressed(self, event: FooterControl.Pressed) -> None:
        control_id = event.control_id
        if control_id == "mode-read":
            await self.action_set_mode("read")
        elif control_id == "mode-side":
            await self.action_set_mode("side")
        elif control_id == "mode-stack":
            await self.action_set_mode("stack")
        elif control_id == "mode-freewrite":
            await self.action_set_mode("freewrite")
        elif control_id == "mode-exercise":
            await self.action_set_mode("exercise")
        elif control_id == "mode-project":
            await self.action_set_mode("project")
        elif control_id == "show-exercises":
            await self.action_show_exercise_list()
        elif control_id == "show-profiles":
            await self.action_show_profiles()
        elif control_id == "previous-exercise":
            await self.action_previous_exercise()
        elif control_id == "next-exercise":
            await self.action_next_exercise()
        elif control_id == "help-button":
            self.push_screen(HelpScreen())

    @on(OptionList.OptionSelected, "#project-tree")
    async def on_project_tree_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option_id
        if not option_id:
            return
        if option_id.startswith("cat:"):
            category = option_id[4:]
            self._project_tree_expanded[category] = not self._project_tree_expanded.get(category, True)
            self._rebuild_project_tree()
        elif option_id.startswith("doc:"):
            doc_id = int(option_id[4:])
            if doc_id != self.current_project_doc_id:
                if self._project_doc_dirty:
                    await self._save_project_doc("switch")
                self._load_project_doc(doc_id)
        elif option_id.startswith("add:"):
            category = option_id[4:]
            await self._add_project_doc(category)
        elif option_id == "newcat":
            await self._add_project_category()

    async def _add_project_doc(self, category: str) -> None:
        title = await self.push_screen_wait(NewItemScreen(f"New document in {category}"))
        if not title or self.database is None or self.active_project_key is None:
            return
        doc = self.database.create_project_doc(self.active_project_key, category, title)
        self._project_tree_expanded[category] = True
        self._load_project_doc(doc.id)
        self.query_one("#project-doc-editor", TextArea).focus()

    async def _add_project_category(self) -> None:
        category = await self.push_screen_wait(NewItemScreen("New category name"))
        if not category or self.database is None or self.active_project_key is None:
            return
        doc = self.database.create_project_doc(self.active_project_key, category, "Untitled")
        self._project_tree_expanded[category] = True
        self._load_project_doc(doc.id)
        self.query_one("#project-doc-title", Input).focus()

    @on(TextArea.Changed, "#project-doc-editor")
    def on_project_doc_editor_changed(self, _: TextArea.Changed) -> None:
        if self._loading_project_doc or self.current_project_doc_id is None:
            return
        self._mark_project_doc_dirty()

    @on(Input.Submitted, "#project-doc-title")
    def on_project_doc_title_submitted(self, event: Input.Submitted) -> None:
        if self.current_project_doc_id is None or self.database is None:
            return
        new_title = event.value.strip()
        if not new_title:
            return
        self.database.update_project_doc_title(self.current_project_doc_id, new_title)
        self._rebuild_project_tree()
        self.query_one("#project-doc-editor", TextArea).focus()

    @on(TextArea.Changed, "#editor")
    def on_editor_changed(self, _: TextArea.Changed) -> None:
        if self._loading_editor:
            return
        editor = self.query_one("#editor", TextArea)
        if self._ignored_loaded_text is not None and editor.text == self._ignored_loaded_text:
            self._ignored_loaded_text = None
            return
        self._mark_dirty()
        self._handle_timed_editor_change(editor.text)

    def _restore_preferences(self) -> None:
        assert self.database is not None
        last_mode = self.database.get_preference("last_mode")
        if last_mode == "write":
            last_mode = "freewrite"
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
        visible_ids = {exercise.exercise_id for exercise in self.content_index.visible_exercises()}
        if preferred_id and preferred_id in visible_ids:
            await self._open_exercise_by_id(preferred_id, save_current=False)
            return
        first = self.content_index.first_available()
        if first is None:
            await self._show_empty_state()
            return
        await self._open_exercise_by_id(first.exercise_id, save_current=False)

    async def _show_empty_state(self) -> None:
        self._clear_timed_state_runtime()
        self.current_exercise = None
        self.current_entry_id = None
        self.query_one("#exercise-title", Label).update("No exercises found")
        self.query_one("#exercise-meta", Label).update("Run install.sh to create sample content.")
        await self._update_exercise_markdown("# No exercises found\n\nRun install.sh to create sample content.")
        self._load_disabled_editor()

    def _on_exercise_list_closed(self, selection: str | None) -> None:
        if selection:
            self.run_worker(self._open_exercise_by_id(selection), exclusive=False)

    def _on_history_closed(self, selected: EntryRecord | ProjectEntryRecord | None) -> None:
        if selected is not None:
            self.push_screen(EntryPreviewScreen(selected))

    def _on_sprint_closed(self, seconds: int | None) -> None:
        if seconds:
            self._start_sprint(seconds)

    async def _step_exercise(self, direction: int) -> None:
        visible_exercises = self.content_index.visible_exercises()
        if not visible_exercises or self.current_exercise is None:
            return
        current_index = next(
            (index for index, item in enumerate(visible_exercises) if item.exercise_id == self.current_exercise.exercise_id),
            0,
        )
        next_index = max(0, min(len(visible_exercises) - 1, current_index + direction))
        if next_index == current_index:
            self.bell()
            return
        await self._open_exercise_by_id(visible_exercises[next_index].exercise_id)

    async def _open_exercise_by_id(self, exercise_id: str, *, save_current: bool = True) -> None:
        target = self.content_index.get(exercise_id)
        if target is None:
            return
        self._persist_timed_state()
        if save_current and self._is_dirty():
            await self._save_current_entry("switch")
        self.current_exercise = target
        assert self.database is not None
        self.database.set_preference("last_exercise_id", target.exercise_id)
        if target.project_key is not None:
            self._unlock_project_mode()
        self._update_exercise_header(target)
        await self._update_exercise_markdown(target.body)
        self._load_editor_for_exercise(target)
        self._update_bottom_bar()
        if self._effective_layout_mode(target) == "read":
            self._focus_exercise()
        else:
            self._focus_editor()

    def _update_exercise_header(self, exercise: Exercise) -> None:
        try:
            title = self.query_one("#exercise-title", Label)
            meta = self.query_one("#exercise-meta", Label)
        except Exception:
            return
        title_style = TYPE_TITLE_STYLES.get(exercise.type, "bold #e7dfcf")
        title.update(Text(exercise.title, style=title_style))

        type_label = "Long-term" if exercise.type == "long-term" else exercise.type.title()
        type_style = TYPE_COLORS.get(exercise.type, "#c7d0d6")
        status_style = STATUS_COLORS.get(exercise.status, "#7b8794")
        meta_text = Text()
        meta_text.append(f"Part {exercise.part}", style=f"bold {PART_COLOR}")
        meta_text.append("  ")
        meta_text.append(exercise.module, style=MODULE_COLOR)
        meta_text.append("  ")
        meta_text.append(type_label, style=type_style)
        if exercise.status in {"optional", "archived"}:
            meta_text.append("  ")
            meta_text.append(f"[{exercise.status}]", style=status_style)
        meta.update(meta_text)

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
            fallback_text.update(
                Markdown(
                    markdown_text,
                    code_theme="monokai",
                    hyperlinks=True,
                    justify="left",
                )
            )
            viewer.display = False
            fallback.display = True
            self._using_markdown_fallback = True

    def _apply_layout(self) -> None:
        workspace = self.query_one("#workspace", Container)
        exercise_pane = self.query_one("#exercise-pane", Container)
        writing_pane = self.query_one("#writing-pane", Container)
        project_pane = self.query_one("#project-pane", Container)
        ratio_left, ratio_right = SIDE_RATIOS[self.side_ratio_index]
        layout_mode = self._effective_layout_mode()
        if layout_mode == "project":
            workspace.styles.layout = "vertical"
            exercise_pane.display = False
            writing_pane.display = False
            project_pane.display = True
            project_pane.styles.width = "100%"
            project_pane.styles.height = "100%"
        elif layout_mode == "read":
            workspace.styles.layout = "vertical"
            exercise_pane.display = True
            writing_pane.display = False
            project_pane.display = False
            exercise_pane.styles.width = "100%"
            exercise_pane.styles.height = "100%"
        elif layout_mode in {"freewrite", "exercise"}:
            workspace.styles.layout = "vertical"
            exercise_pane.display = False
            writing_pane.display = True
            project_pane.display = False
            writing_pane.styles.width = "100%"
            writing_pane.styles.height = "100%"
        elif layout_mode == "side":
            workspace.styles.layout = "horizontal"
            exercise_pane.display = True
            writing_pane.display = True
            project_pane.display = False
            exercise_pane.styles.width = f"{ratio_left}%"
            writing_pane.styles.width = f"{ratio_right}%"
            exercise_pane.styles.height = "100%"
            writing_pane.styles.height = "100%"
        else:
            workspace.styles.layout = "vertical"
            exercise_pane.display = True
            writing_pane.display = True
            project_pane.display = False
            exercise_pane.styles.height = "50%"
            writing_pane.styles.height = "50%"
            exercise_pane.styles.width = "100%"
            writing_pane.styles.width = "100%"
        self._update_bottom_bar()
        self._update_write_session_stats()

    def _update_bottom_bar(self) -> None:
        mapping = {
            "mode-read": "read",
            "mode-side": "side",
            "mode-stack": "stack",
            "mode-freewrite": "freewrite",
            "mode-exercise": "exercise",
            "mode-project": "project",
        }
        effective_mode = self._effective_layout_mode()
        can_write = self._exercise_supports_writing(self.current_exercise)
        can_use_exercise_mode = self._exercise_mode_available(self.current_exercise)
        can_use_project_mode = self._project_mode_available(self.current_exercise)
        for control_id, mode in mapping.items():
            control = self.query_one(f"#{control_id}", FooterControl)
            control.set_active(mode == effective_mode)
            control.set_indicator(
                mode == "project"
                and self.current_exercise is not None
                and self.current_exercise.project_key is not None
            )
            if mode == "exercise":
                control.set_disabled(not can_use_exercise_mode)
            elif mode == "project":
                control.set_disabled(not can_use_project_mode)
                control.set_detail(format_project_indicator(self.current_exercise))
            elif mode in WRITING_LAYOUTS:
                control.set_disabled(not can_write)
        self._update_word_count()

    def _exercise_supports_writing(self, exercise: Exercise | None) -> bool:
        return exercise is not None and exercise.type != "reading"

    def _exercise_mode_available(self, exercise: Exercise | None) -> bool:
        return self._exercise_supports_writing(exercise) and bool(exercise.guided_questions)

    def _project_mode_available(self, exercise: Exercise | None = None) -> bool:
        if self.database is not None and self.database.get_preference("project_unlocked") == "1":
            return True
        target = self.current_exercise if exercise is None else exercise
        return target is not None and target.project_key is not None

    def _can_write_current_exercise(self) -> bool:
        return self._exercise_supports_writing(self.current_exercise)

    def _can_edit_current_target(self) -> bool:
        return self._effective_layout_mode() != "read"

    def _can_use_mode(self, mode: str, exercise: Exercise | None = None) -> bool:
        target = self.current_exercise if exercise is None else exercise
        if mode == "exercise":
            return self._exercise_mode_available(target)
        if mode == "project":
            return self._project_mode_available(target)
        if mode in WRITING_LAYOUTS:
            return target is None or self._exercise_supports_writing(target)
        return mode in LAYOUTS

    def _effective_layout_mode(self, exercise: Exercise | None = None) -> str:
        target = self.current_exercise if exercise is None else exercise
        if self.current_layout_mode == "project":
            if self._project_mode_available(target):
                return "project"
            if self._exercise_supports_writing(target):
                return "freewrite"
            return "read"
        if self.current_layout_mode == "exercise":
            if self._exercise_mode_available(target):
                return "exercise"
            if self._exercise_supports_writing(target):
                return "freewrite"
            return "read"
        if self.current_layout_mode in WRITING_LAYOUTS and not self._exercise_supports_writing(target):
            return "read"
        return self.current_layout_mode

    def _current_editor_slot(self, exercise: Exercise | None = None) -> tuple[str, str] | None:
        target = self.current_exercise if exercise is None else exercise
        if target is None:
            return None
        effective_mode = self._effective_layout_mode(target)
        if effective_mode == "project" and target.project_key is not None:
            return ("project", target.project_key)
        if effective_mode == "exercise":
            return ("exercise", "exercise")
        if effective_mode in {"freewrite", "side", "stack", "read"} and self._exercise_supports_writing(target):
            return ("exercise", "freewrite")
        return None

    def _current_draft_kind(self) -> str:
        return self._draft_kind_for_exercise(self.current_exercise)

    def _draft_kind_for_exercise(self, exercise: Exercise | None) -> str:
        return "exercise" if self._effective_layout_mode(exercise) == "exercise" else "freewrite"

    def _draft_preference_key(self, exercise: Exercise, draft_kind: str) -> str:
        return f"active_entry:{draft_kind}:{exercise.exercise_id}"

    def _resolve_entry_for_exercise(self, exercise: Exercise, draft_kind: str) -> EntryRecord:
        assert self.database is not None
        preferred_entry_id = self.database.get_preference(self._draft_preference_key(exercise, draft_kind))
        if preferred_entry_id is not None:
            try:
                preferred = self.database.get_entry_by_id(int(preferred_entry_id))
            except (KeyError, ValueError):
                preferred = None
            if preferred is not None and self._entry_matches_current_slot(exercise, draft_kind, preferred):
                return preferred
        return self.database.resolve_entry_for_exercise(exercise, draft_kind)

    def _entry_matches_current_slot(self, exercise: Exercise, draft_kind: str, record: EntryRecord) -> bool:
        if record.exercise_id != exercise.exercise_id or record.draft_kind != draft_kind:
            return False
        if exercise.is_long_term and exercise.save_mode == "session":
            return record.created_at.astimezone().date() == datetime.now().astimezone().date()
        return True

    def _remember_active_entry(self, exercise: Exercise, record: EntryRecord) -> None:
        assert self.database is not None
        self.database.set_preference(
            self._draft_preference_key(exercise, record.draft_kind),
            str(record.id),
        )

    def _can_manage_freewrite_drafts(self, exercise: Exercise | None = None) -> bool:
        target = self.current_exercise if exercise is None else exercise
        return self._exercise_supports_writing(target) and self._effective_layout_mode(target) == "freewrite"

    def _current_freewrite_entries(self) -> list[EntryRecord]:
        if self.current_exercise is None or not self._can_manage_freewrite_drafts(self.current_exercise):
            return []
        assert self.database is not None
        return self.database.list_history(self.current_exercise.exercise_id, "freewrite")

    def _can_undo_deleted_freewrite(self) -> bool:
        return (
            self._last_deleted_freewrite is not None
            and self.current_exercise is not None
            and self._can_manage_freewrite_drafts()
            and self._last_deleted_freewrite.exercise_id == self.current_exercise.exercise_id
        )

    def _project_title_for(self, exercise: Exercise) -> str:
        if exercise.project_key is not None and self.database is not None:
            project = self.database.get_project(exercise.project_key)
            if project is not None:
                return project.title
        return exercise.project_title or exercise.title

    def _resolve_project_entry(self, exercise: Exercise) -> ProjectEntryRecord:
        assert self.database is not None
        assert exercise.project_key is not None
        return self._resolve_project_entry_for_key(exercise.project_key)

    def _resolve_project_entry_for_key(self, project_key: str) -> ProjectEntryRecord:
        assert self.database is not None
        latest = self.database.get_latest_project_entry(project_key)
        if latest is not None:
            return latest
        seed = self.content_index.project_seed(project_key)
        if seed is not None:
            seed_entries = self.database.list_history(seed.exercise_id, "freewrite")
            if seed_entries:
                self.database.seed_project_entries(project_key, seed_entries)
        return self.database.resolve_project_entry(project_key)

    def _load_editor_for_exercise(self, exercise: Exercise) -> None:
        effective_mode = self._effective_layout_mode(exercise)
        if effective_mode == "project":
            self._load_project_workspace(exercise.project_key)
            return
        if self._exercise_supports_writing(exercise):
            self._load_entry_into_editor(exercise, self._resolve_entry_for_exercise(exercise, self._draft_kind_for_exercise(exercise)))
            return
        self._load_disabled_editor()

    def _load_disabled_editor(self) -> None:
        self._clear_timed_state_runtime()
        self.current_entry_id = None
        editor = self.query_one("#editor", TextArea)
        self._loading_editor = True
        self._ignored_loaded_text = ""
        editor.load_text("")
        self._loading_editor = False
        editor.disabled = True
        self._set_save_indicator("Saved ✓", "saved")
        self._update_word_count()
        self._update_draft_controls()

    def _load_entry_into_editor(self, exercise: Exercise, record: EntryRecord) -> None:
        self._persist_timed_state()
        self.current_entry_id = record.id
        self._remember_active_entry(exercise, record)
        editor = self.query_one("#editor", TextArea)
        loaded_text = self._editor_text_for_record(exercise, record)
        self._loading_editor = True
        self._ignored_loaded_text = loaded_text
        editor.load_text(loaded_text)
        self._loading_editor = False
        self._activate_timed_state_for_entry(exercise, record.id, loaded_text)
        self._apply_current_editor_disabled_state()
        self._set_save_indicator("Saved ✓", "saved")
        self._update_word_count()
        self._update_draft_controls()

    def _load_project_entry_into_editor(self, record: ProjectEntryRecord) -> None:
        self._clear_timed_state_runtime()
        self.current_entry_id = record.id
        editor = self.query_one("#editor", TextArea)
        editor.disabled = False
        self._loading_editor = True
        self._ignored_loaded_text = record.content
        editor.load_text(record.content)
        self._loading_editor = False
        self._set_save_indicator("Saved ✓", "saved")
        self._update_word_count()
        self._update_draft_controls()

    def _unlock_project_mode(self) -> None:
        if self.database is not None and self.database.get_preference("project_unlocked") != "1":
            self.database.set_preference("project_unlocked", "1")

    def _load_project_workspace(self, target_project_key: str | None = None) -> None:
        if self.database is None:
            return
        groups = list(self.content_index.project_groups())
        if not groups:
            return
        if target_project_key is not None:
            self.active_project_key = target_project_key
        elif self.active_project_key is None:
            last = self.database.get_preference("last_project")
            if last and any(g.project_key == last for g in groups):
                self.active_project_key = last
            else:
                self.active_project_key = groups[0].project_key
        if self.active_project_key is not None:
            self.database.set_preference("last_project", self.active_project_key)
            self._load_project_entry_into_editor(self._resolve_project_entry_for_key(self.active_project_key))
        name_label = self.query_one("#project-name-label", Label)
        name_label.update(self._project_name_for_key(self.active_project_key or ""))
        prev_btn = self.query_one("#proj-prev", Button)
        next_btn = self.query_one("#proj-next", Button)
        prev_btn.display = len(groups) > 1
        next_btn.display = len(groups) > 1
        self._rebuild_project_tree()
        if self.current_project_doc_id is not None:
            try:
                doc = self.database.get_project_doc(self.current_project_doc_id)
                if doc.project_key == self.active_project_key:
                    self._load_project_doc(self.current_project_doc_id)
                    return
            except KeyError:
                pass
        docs = self.database.list_project_docs(self.active_project_key or "")
        if docs:
            self._load_project_doc(docs[0].id)
        else:
            self._clear_project_doc_editor()

    def _project_name_for_key(self, project_key: str) -> str:
        if self.database is None:
            return project_key
        project = self.database.get_project(project_key)
        return project.title if project else project_key

    def _cycle_active_project(self, direction: int) -> None:
        groups = list(self.content_index.project_groups())
        if not groups:
            return
        keys = [g.project_key for g in groups]
        if self.active_project_key not in keys:
            self.active_project_key = keys[0]
        else:
            idx = keys.index(self.active_project_key)
            self.active_project_key = keys[(idx + direction) % len(keys)]
        self._load_project_workspace(self.active_project_key)

    def _rebuild_project_tree(self) -> None:
        if self.database is None or self.active_project_key is None:
            return
        project_tree = self.query_one("#project-tree", OptionList)
        docs = self.database.list_project_docs(self.active_project_key)
        docs_by_category: dict[str, list[ProjectDocRecord]] = {}
        for doc in docs:
            docs_by_category.setdefault(doc.category, []).append(doc)
        all_categories = list(BUILTIN_CATEGORIES)
        for cat in docs_by_category:
            if cat not in all_categories:
                all_categories.append(cat)
        options: list = []
        for category in all_categories:
            expanded = self._project_tree_expanded.get(category, True)
            arrow = "▼" if expanded else "▶"
            cat_text = Text(f"{arrow} {category}", style="bold #c4cdd2")
            options.append(Option(cat_text, id=f"cat:{category}"))
            if expanded:
                cat_docs = docs_by_category.get(category, [])
                for doc in cat_docs:
                    marker = "●" if doc.id == self.current_project_doc_id else "·"
                    doc_text = Text(f"  {marker} {doc.title}", style="#d9d5cf")
                    options.append(Option(doc_text, id=f"doc:{doc.id}"))
                options.append(Option(Text("  + Add", style="#5a8a6a"), id=f"add:{category}"))
        if options:
            options.append(None)
        options.append(Option(Text("+ New category", style="#5a8a6a"), id="newcat"))
        project_tree.clear_options()
        project_tree.add_options(options)

    def _load_project_doc(self, doc_id: int) -> None:
        if self.database is None:
            return
        try:
            doc = self.database.get_project_doc(doc_id)
        except KeyError:
            return
        self.current_project_doc_id = doc_id
        self._project_doc_dirty = False
        title_input = self.query_one("#project-doc-title", Input)
        title_input.value = doc.title
        editor = self.query_one("#project-doc-editor", TextArea)
        self._loading_project_doc = True
        editor.load_text(doc.content)
        self._loading_project_doc = False
        self._rebuild_project_tree()
        self._update_word_count()
        self._set_save_indicator("Saved ✓", "saved")

    def _clear_project_doc_editor(self) -> None:
        self.current_project_doc_id = None
        self._project_doc_dirty = False
        title_input = self.query_one("#project-doc-title", Input)
        title_input.value = ""
        editor = self.query_one("#project-doc-editor", TextArea)
        self._loading_project_doc = True
        editor.load_text("")
        self._loading_project_doc = False
        self._update_word_count()

    def _mark_project_doc_dirty(self) -> None:
        self._project_doc_dirty = True
        self._set_save_indicator("Unsaved •", "unsaved")
        self._update_word_count()
        self._project_doc_autosave_gen += 1
        gen = self._project_doc_autosave_gen
        self.set_timer(
            self.autosave_delay_seconds,
            lambda: self.call_after_refresh(self._autosave_project_doc_if_current, gen),
        )

    def _autosave_project_doc_if_current(self, gen: int) -> None:
        if gen != self._project_doc_autosave_gen:
            return
        if not self._project_doc_dirty:
            return
        self.run_worker(self._save_project_doc("autosave"), exclusive=False)

    async def _save_project_doc(self, reason: str) -> None:
        if self.current_project_doc_id is None or self.database is None:
            return
        editor = self.query_one("#project-doc-editor", TextArea)
        self._project_doc_save_gen += 1
        gen = self._project_doc_save_gen
        self._set_save_indicator("Saving…", "saving")
        self.database.update_project_doc_content(self.current_project_doc_id, editor.text)
        if gen != self._project_doc_save_gen:
            return
        self._project_doc_dirty = False
        self._set_save_indicator("Saved ✓", "saved")
        if reason == "manual":
            self.set_timer(1.5, lambda: self.call_after_refresh(self._restore_saved_indicator))

    def _editor_text_for_record(self, exercise: Exercise, record: EntryRecord) -> str:
        if record.content:
            return record.content
        if record.draft_kind == "exercise" and exercise.guided_questions:
            return self._build_guided_scaffold(exercise.guided_questions)
        return ""

    def _build_guided_scaffold(self, questions: tuple[str, ...]) -> str:
        lines: list[str] = []
        for index, question in enumerate(questions, start=1):
            if index > 1:
                lines.append("")
            lines.append(f"{index}. {question}")
            lines.append("Answer:")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _update_word_count(self) -> None:
        label = self.query_one("#word-count", Label)
        if self._sprint_seconds_remaining > 0:
            minutes, seconds = divmod(self._sprint_seconds_remaining, 60)
            label.update(f"Sprint: {minutes:02d}:{seconds:02d}")
            return
        if self._effective_layout_mode() == "project":
            editor = self.query_one("#project-doc-editor", TextArea)
            label.update(f"Word count: {word_count(editor.text)}")
            return
        editor = self.query_one("#editor", TextArea)
        label.update(f"Word count: {word_count(editor.text)}")

    def _mark_dirty(self) -> None:
        self._set_save_indicator("Unsaved •", "unsaved")
        self._update_word_count()
        if self._suppress_autosave:
            return
        self._autosave_generation += 1
        generation = self._autosave_generation
        self.set_timer(
            self.autosave_delay_seconds,
            lambda: self.call_after_refresh(self._autosave_if_current, generation),
        )

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
        if self._effective_layout_mode() == "project":
            record = self.database.update_project_entry(self.current_entry_id, editor.text)
            if generation != self._save_generation:
                return
            self.current_entry_id = record.id
        else:
            record = self.database.update_entry(self.current_entry_id, editor.text)
            if generation != self._save_generation:
                return
            self.current_entry_id = record.id
            if self.current_exercise is not None:
                self._remember_active_entry(self.current_exercise, record)
        self._set_save_indicator("Saved ✓", "saved")
        self._persist_timed_state()
        if self._current_entry_locked_by_timer():
            self._set_save_indicator("10m limit reached", "saved")
        self._update_draft_controls()
        if reason == "manual":
            self.set_timer(1.5, lambda: self.call_after_refresh(self._restore_saved_indicator))

    async def _create_new_session_entry(self) -> None:
        if self._effective_layout_mode() == "project":
            self.bell()
            return
        if self._is_dirty():
            await self._save_current_entry("manual")
        if self.current_exercise is None:
            return
        assert self.database is not None
        record = self.database.create_entry(
            self.current_exercise.exercise_id,
            self._current_draft_kind(),
            "",
        )
        self._load_entry_into_editor(self.current_exercise, record)
        self._focus_editor()

    async def _create_new_freewrite_draft(self) -> None:
        if self.current_exercise is None or not self._can_manage_freewrite_drafts():
            self.bell()
            return
        if self._is_dirty():
            await self._save_current_entry("manual")
        assert self.database is not None
        record = self.database.create_entry(
            self.current_exercise.exercise_id,
            "freewrite",
            "",
        )
        self._last_deleted_freewrite = None
        self._load_entry_into_editor(self.current_exercise, record)
        self._focus_editor()

    async def _cycle_freewrite_draft(self, direction: int) -> None:
        if self.current_exercise is None or not self._can_manage_freewrite_drafts():
            self.bell()
            return
        entries = self._current_freewrite_entries()
        if len(entries) <= 1:
            self.bell()
            return
        current_index = next((index for index, entry in enumerate(entries) if entry.id == self.current_entry_id), 0)
        target = entries[(current_index + direction) % len(entries)]
        if target.id == self.current_entry_id:
            return
        if self._is_dirty():
            await self._save_current_entry("switch")
        self._load_entry_into_editor(self.current_exercise, target)
        self._focus_editor()

    async def _delete_current_freewrite_draft(self) -> None:
        if self.current_exercise is None or not self._can_manage_freewrite_drafts():
            self.bell()
            return
        if self.current_entry_id is None:
            self.bell()
            return
        if self._is_dirty():
            await self._save_current_entry("manual")
        assert self.database is not None
        deleted = self.database.get_entry_by_id(self.current_entry_id)
        entries = self._current_freewrite_entries()
        current_index = next((index for index, entry in enumerate(entries) if entry.id == deleted.id), -1)
        if current_index < 0:
            self.bell()
            return
        if len(entries) == 1:
            replacement = self.database.create_entry(
                self.current_exercise.exercise_id,
                "freewrite",
                "",
            )
        else:
            replacement = entries[(current_index + 1) % len(entries)]
            if replacement.id == deleted.id:
                replacement = entries[current_index - 1]
        self.database.delete_entry(deleted.id)
        self._last_deleted_freewrite = deleted
        self._load_entry_into_editor(self.current_exercise, replacement)
        self._focus_editor()

    async def _undo_deleted_freewrite_draft(self) -> None:
        if not self._can_undo_deleted_freewrite():
            self.bell()
            return
        if self._is_dirty():
            await self._save_current_entry("manual")
        assert self.database is not None
        assert self.current_exercise is not None
        assert self._last_deleted_freewrite is not None
        restored = self.database.create_entry(
            self._last_deleted_freewrite.exercise_id,
            self._last_deleted_freewrite.draft_kind,
            self._last_deleted_freewrite.content,
        )
        self._last_deleted_freewrite = None
        self._load_entry_into_editor(self.current_exercise, restored)
        self._focus_editor()

    def _update_draft_controls(self) -> None:
        try:
            toolbar = self.query_one("#draft-toolbar", Horizontal)
            label = self.query_one("#draft-label", Label)
            previous_button = self.query_one("#draft-prev", Button)
            next_button = self.query_one("#draft-next", Button)
            new_button = self.query_one("#draft-new", Button)
            delete_button = self.query_one("#draft-delete", Button)
            undo_button = self.query_one("#draft-undo", Button)
        except Exception:
            return
        visible = self.current_exercise is not None and self._can_manage_freewrite_drafts()
        toolbar.display = visible
        if not visible:
            self._update_write_session_stats()
            return
        entries = self._current_freewrite_entries()
        count = len(entries)
        current_index = next((index for index, entry in enumerate(entries) if entry.id == self.current_entry_id), 0)
        label.update("Draft" if count == 0 else f"Draft {current_index + 1}/{count}")
        previous_button.disabled = count <= 1
        next_button.disabled = count <= 1
        new_button.disabled = False
        delete_button.disabled = count == 0
        undo_button.disabled = not self._can_undo_deleted_freewrite()
        self._update_write_session_stats()

    def _restore_saved_indicator(self) -> None:
        if self._current_entry_locked_by_timer():
            self._set_save_indicator("10m limit reached", "saved")
            return
        if not self._is_dirty():
            self._set_save_indicator("Saved ✓", "saved")

    def _set_save_indicator(self, text: str, state: str) -> None:
        self._last_save_message = text
        self._save_indicator_state = state
        indicator = self.query_one("#save-indicator", Label)
        indicator.update(text)
        indicator.remove_class("saving")
        indicator.remove_class("saved")
        indicator.remove_class("unsaved")
        indicator.add_class(state)

    def _is_dirty(self) -> bool:
        return self._save_indicator_state == "unsaved"

    def _focus_editor(self) -> None:
        self.query_one("#editor", TextArea).focus()

    def _focus_exercise(self) -> None:
        if self._using_markdown_fallback:
            self.query_one("#exercise-fallback", VerticalScroll).focus()
        else:
            self.query_one("#exercise-markdown", MarkdownViewer).focus()

    def _start_sprint(self, seconds: int) -> None:
        if not self._can_edit_current_target():
            self.bell()
            return
        self._suppress_autosave = True
        self._sprint_seconds_remaining = seconds
        if self._sprint_timer is not None:
            self._sprint_timer.stop()
        self._sprint_timer = self.set_interval(self.sprint_tick_seconds, self._tick_sprint)
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
