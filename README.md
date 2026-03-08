# gbtw

Getting Back to Writing is a Textual TUI for working through a structured writing course with persistent drafts and low-friction keyboard controls.

## Modes

- `Read` shows the current exercise prompt full-screen.
- `Side` and `Stack` show the prompt and editor together.
- `Freewrite` opens a full-screen draft for open writing on the current exercise.
- `Exercise` opens a separate full-screen answers draft seeded from question or prompt sections in the exercise content when available.

`Freewrite` and `Exercise` save independently for the same exercise, so switching between them does not overwrite or mix drafts.

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
2. Switch through `Read`, `Side`, `Stack`, `Freewrite`, and `Exercise`; verify no pane flicker, correct focus behavior, and disabled `Exercise` states for content without guided questions.
3. Use `[` and `]` in side mode; confirm the split cycles through `40/60`, `50/50`, and `60/40`.
4. Type in the editor; confirm live word count updates and autosave changes `Unsaved •` to `Saved ✓` after idle time.
5. Switch between `Freewrite` and `Exercise`; confirm each mode restores its own draft and `Exercise` scaffolds blank answer drafts from guided questions.
6. Switch exercises with dirty text; confirm the current section draft is preserved and the selected exercise restores its latest draft for that section.
7. Open a long-term session exercise and use `Ctrl+J`; confirm a new dated entry is created for the current section only.
8. Open a long-term project exercise across a restart; confirm it reopens the same ongoing draft row for the active section instead of creating a new one.
9. Open `Ctrl+E`; confirm optional exercises are marked and archived exercises only appear after `Ctrl+A`.
10. Open `Ctrl+H`; confirm long-term history is scoped to the current section (`Freewrite` or `Exercise`) and entries open in read-only preview mode.
11. Open `Ctrl+T`; confirm the countdown replaces word count, autosave is suppressed mid-sprint, and completion saves plus rings the bell.
12. During autosave, confirm cursor position and editor scroll remain stable.
13. Restart the app; confirm mode, split ratio, and last exercise are restored from `~/.local/share/gbtw/progress.db`.
