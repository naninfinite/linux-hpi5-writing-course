from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gbtw.content import load_content_index, render_markdown_fallback


class ContentTests(unittest.TestCase):
    def test_load_content_index_applies_defaults_and_orders_by_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part1").mkdir()
            (root / "part2").mkdir()
            (root / "part1" / "b.md").write_text(
                """---
title: Later
part: 1
module: Voice
type: exercise
---

Two
""",
                encoding="utf-8",
            )
            (root / "part1" / "a.md").write_text(
                """---
title: Earlier
part: 1
module: Voice
type: long-term
---

One
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual([exercise.exercise_id for exercise in index.exercises], ["part1/a.md", "part1/b.md"])
        self.assertEqual(index.exercises[0].save_mode, "session")
        self.assertEqual(index.exercises[0].status, "active")

    def test_invalid_frontmatter_is_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part1").mkdir()
            (root / "part1" / "broken.md").write_text(
                """---
title: Broken
part: one
module: Voice
type: exercise
---

Body
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual(index.exercises, ())
        self.assertEqual(len(index.warnings), 1)

    def test_render_markdown_fallback_extracts_text(self) -> None:
        output = render_markdown_fallback("# Heading\n\n- One\n- Two\n\n`code`")
        self.assertIn("Heading", output)
        self.assertIn("One", output)
        self.assertIn("code", output)


if __name__ == "__main__":
    unittest.main()
