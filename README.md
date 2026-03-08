# gbtw

Getting Back to Writing is a Textual TUI for working through a structured writing course with persistent drafts and low-friction keyboard controls.

## Modes

- `Read` shows the current exercise prompt full-screen.
- `Side` and `Stack` show the prompt and editor together.
- `Freewrite` opens a full-screen draft for open writing on the current exercise.
- `Exercise` opens a separate full-screen answers draft seeded from question or prompt sections in the exercise content when available.
- `Project` opens a shared long-form manuscript for any document linked to a `project_key`.

`Freewrite` and `Exercise` save independently for the same exercise, so switching between them does not overwrite or mix drafts. `Project` is separate again: it is shared across all linked documents for the same project track.
Freewrite-backed layouts also show a compact draft bar inside the writing pane so you can create, cycle, delete, and undo deleted drafts for the current exercise.

## Run

```bash
./install.sh
.venv/bin/gbtw
```

## Test

```bash
python3 -m unittest discover -s tests
```

## Manual QA Checklist

Use this on the target Ubuntu ARM64 box after a clean install:

1. Launch with `.venv/bin/gbtw` and confirm the first screen is clean, responsive, and loads sample content.
2. Switch through `Read`, `Side`, `Stack`, `Freewrite`, `Exercise`, and `Project`; verify no pane flicker, correct focus behavior, and disabled `Exercise` / `Project` states when the current document does not support them.
3. Use `[` and `]` in side mode; confirm the split cycles through `40/60`, `50/50`, and `60/40`.
4. Type in the editor; confirm live word count updates and autosave changes `Unsaved •` to `Saved ✓` after idle time.
5. Switch between `Freewrite` and `Exercise`; confirm each mode restores its own draft and `Exercise` scaffolds blank answer drafts from guided questions.
6. In a freewrite-backed layout, use the draft controls to create two drafts; confirm previous/next switches between them, dirty text saves before switching, `Del` removes the current draft, `Undo` restores it, and returning to the exercise restores the selected draft.
7. Switch exercises with dirty text; confirm the current section draft is preserved and the selected exercise restores its latest or currently selected draft for that section.
8. Open `Project` on two linked documents with the same `project_key`; confirm both reopen the same shared manuscript and switching between them does not fork the project text.
9. Open `Project` on an unlinked document; confirm the control is disabled and the app falls back to `Freewrite` or `Read` when appropriate.
10. Open a long-term session exercise and use `Ctrl+J`; confirm a new dated entry is created for the current section only, and `Ctrl+J` does nothing in `Project` mode.
11. Open `Ctrl+E`; confirm optional exercises are marked and archived exercises only appear after `Ctrl+A`.
12. Open `Ctrl+H`; confirm history is scoped to the current section in `Freewrite` / `Exercise`, and to the shared manuscript title in `Project`.
13. Open `Ctrl+T`; confirm the countdown replaces word count, autosave is suppressed mid-sprint, and completion saves plus rings the bell.
14. During autosave, confirm cursor position and editor scroll remain stable.
15. Restart the app; confirm mode, split ratio, and last exercise are restored from `~/.local/share/gbtw/progress.db`.
