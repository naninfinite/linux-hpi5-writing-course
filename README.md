# gbtw

Getting Back to Writing is a Textual TUI for working through a structured writing course with persistent drafts and low-friction keyboard controls.

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
2. Switch through `Read`, `Side`, `Stack`, `Write`, and `Top`; verify no pane flicker and correct focus behavior.
3. Use `[` and `]` in side mode; confirm the split cycles through `40/60`, `50/50`, and `60/40`.
4. Type in the editor; confirm live word count updates and autosave changes `Unsaved •` to `Saved ✓` after idle time.
5. Switch exercises with dirty text; confirm the current draft is preserved and the selected exercise restores its latest draft.
6. Open a long-term session exercise and use `Ctrl+J`; confirm a new dated entry is created.
7. Open a long-term project exercise across a restart; confirm it reopens the same ongoing draft instead of creating a new row.
8. Open `Ctrl+E`; confirm optional exercises are marked and archived exercises only appear after `Ctrl+A`.
9. Open `Ctrl+H`; confirm long-term history entries open in read-only preview mode.
10. Open `Ctrl+T`; confirm the countdown replaces word count, autosave is suppressed mid-sprint, and completion saves plus rings the bell.
11. During autosave, confirm cursor position and editor scroll remain stable.
12. Restart the app; confirm mode, split ratio, and last exercise are restored from `~/.local/share/gbtw/progress.db`.
