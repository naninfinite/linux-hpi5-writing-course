from __future__ import annotations

import inspect
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
from textual.screen import ModalScreen
from textual.timer import Timer
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
from .db import Database, EntryRecord, ProjectEntryRecord

SIDE_RATIOS: tuple[tuple[int, int], ...] = ((40, 60), (50, 50), (60, 40))
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

Tab     switch focus between panes
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
        super().__init__(label, id=control_id, classes="footer-control")
        self._is_disabled = False

    def set_disabled(self, disabled: bool) -> None:
        self._is_disabled = disabled
        self.set_class(disabled, "disabled")

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

    #project-indicator {
        color: #91a3ad;
        margin-left: 1;
    }

    .field-label {
        margin-top: 1;
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
        Binding("tab", "toggle_focus", "Focus", show=False),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
    ]

    def __init__(
        self,
        *,
        database: Database | None = None,
        content_index: ContentIndex | None = None,
        autosave_delay_seconds: float = 4.0,
        sprint_tick_seconds: float = 1.0,
    ) -> None:
        super().__init__()
        self.database: Database | None = database or Database()
        self.content_index = content_index or load_content_index()
        if self.database is not None:
            self.database.sync_projects(
                group.project_key for group in self.content_index.project_groups()
            )
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
                yield TextArea("", id="editor")
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
                yield FooterControl("<", control_id="previous-exercise")
                yield FooterControl(">", control_id="next-exercise")
                yield FooterControl("?", control_id="help-button")
            with Horizontal(id="status-strip"):
                yield Label("Word count: 0", id="word-count")
                yield Label("", id="project-indicator")
                yield Label("Saved ✓", id="save-indicator", classes="saved")

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
        if not self._can_use_mode(mode):
            self.bell()
            return
        previous_slot = self._current_editor_slot()
        assert self.database is not None
        self.current_layout_mode = mode
        self.database.set_preference("last_mode", self.current_layout_mode)
        if self.current_exercise is not None and previous_slot != self._current_editor_slot():
            if self._is_dirty():
                await self._save_current_entry("switch")
            self._load_editor_for_exercise(self.current_exercise)
        self._apply_layout()
        if self._effective_layout_mode() == "read":
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
        if not self._can_edit_current_target():
            self._focus_exercise()
            return
        if self._effective_layout_mode() in {"freewrite", "exercise", "project"}:
            self._focus_editor()
            return
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
        elif control_id == "previous-exercise":
            await self.action_previous_exercise()
        elif control_id == "next-exercise":
            await self.action_next_exercise()
        elif control_id == "help-button":
            self.push_screen(HelpScreen())

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
        self.current_exercise = None
        self.current_entry_id = None
        self.query_one("#exercise-title", Label).update("No exercises found")
        self.query_one("#exercise-meta", Label).update("Run install.sh to create sample content.")
        self._update_project_indicator(None)
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
        if save_current and self._is_dirty():
            await self._save_current_entry("switch")
        self.current_exercise = target
        assert self.database is not None
        self.database.set_preference("last_exercise_id", target.exercise_id)
        self._update_exercise_header(target)
        await self._update_exercise_markdown(target.body)
        self._load_editor_for_exercise(target)
        self._update_bottom_bar()
        if self._effective_layout_mode(target) == "read":
            self._focus_exercise()
        else:
            self._focus_editor()

    def _update_project_indicator(self, exercise: Exercise | None) -> None:
        try:
            indicator = self.query_one("#project-indicator", Label)
        except Exception:
            return
        indicator_text = format_project_indicator(exercise)
        indicator.update(indicator_text)
        indicator.display = bool(indicator_text)

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
        self._update_project_indicator(exercise)

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
        ratio_left, ratio_right = SIDE_RATIOS[self.side_ratio_index]
        layout_mode = self._effective_layout_mode()
        if layout_mode == "read":
            workspace.styles.layout = "vertical"
            exercise_pane.display = True
            writing_pane.display = False
            exercise_pane.styles.width = "100%"
            exercise_pane.styles.height = "100%"
        elif layout_mode in {"freewrite", "exercise", "project"}:
            workspace.styles.layout = "vertical"
            exercise_pane.display = False
            writing_pane.display = True
            writing_pane.styles.width = "100%"
            writing_pane.styles.height = "100%"
        elif layout_mode == "side":
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
            control.set_class(mode == effective_mode, "active-mode")
            if mode == "exercise":
                control.set_disabled(not can_use_exercise_mode)
            elif mode == "project":
                control.set_disabled(not can_use_project_mode)
            elif mode in WRITING_LAYOUTS:
                control.set_disabled(not can_write)
        self._update_word_count()

    def _exercise_supports_writing(self, exercise: Exercise | None) -> bool:
        return exercise is not None and exercise.type != "reading"

    def _exercise_mode_available(self, exercise: Exercise | None) -> bool:
        return self._exercise_supports_writing(exercise) and bool(exercise.guided_questions)

    def _project_mode_available(self, exercise: Exercise | None) -> bool:
        return exercise is not None and exercise.project_key is not None

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
            return self._exercise_supports_writing(target)
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
        project_key = exercise.project_key
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
        if effective_mode == "project" and self._project_mode_available(exercise):
            self._load_project_entry_into_editor(self._resolve_project_entry(exercise))
            return
        if self._exercise_supports_writing(exercise):
            self._load_entry_into_editor(exercise, self._resolve_entry_for_exercise(exercise, self._draft_kind_for_exercise(exercise)))
            return
        self._load_disabled_editor()

    def _load_disabled_editor(self) -> None:
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
        self.current_entry_id = record.id
        self._remember_active_entry(exercise, record)
        editor = self.query_one("#editor", TextArea)
        editor.disabled = not self._exercise_supports_writing(exercise)
        loaded_text = self._editor_text_for_record(exercise, record)
        self._loading_editor = True
        self._ignored_loaded_text = loaded_text
        editor.load_text(loaded_text)
        self._loading_editor = False
        self._set_save_indicator("Saved ✓", "saved")
        self._update_word_count()
        self._update_draft_controls()

    def _load_project_entry_into_editor(self, record: ProjectEntryRecord) -> None:
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

    def _restore_saved_indicator(self) -> None:
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
