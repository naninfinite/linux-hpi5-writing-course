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

    def test_first_available_skips_archived_exercises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part1").mkdir()
            (root / "part1" / "archived.md").write_text(
                """---
title: Archived
part: 1
module: Start
type: reading
status: archived
---

Old content
""",
                encoding="utf-8",
            )
            (root / "part1" / "active.md").write_text(
                """---
title: Active
part: 1
module: Start
type: exercise
status: active
---

Current content
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        first = index.first_available()
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.exercise_id, "part1/active.md")

    def test_load_content_index_extracts_guided_questions_from_question_sections_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part1").mkdir()
            (root / "part1" / "questions.md").write_text(
                """---
title: Questions
part: 1
module: Start
type: exercise
---

## World notes

- Who owns this district?

## Reflection questions

1\\. What kind of cyberpunk world am I drawn to write?
2\\. What human cost am I most interested in exploring?

## Closing

What else might matter here?
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual(
            index.exercises[0].guided_questions,
            (
                "What kind of cyberpunk world am I drawn to write?",
                "What human cost am I most interested in exploring?",
            ),
        )

    def test_load_content_index_keeps_valid_project_group_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part2").mkdir()
            (root / "part2" / "a.md").write_text(
                """---
title: Planning
part: 2
module: Novel
type: exercise
status: ongoing
project_key: part2-novel
project_title: Part 2 Novel
project_seed: true
project_role: seed
---

Body
""",
                encoding="utf-8",
            )
            (root / "part2" / "b.md").write_text(
                """---
title: Reading
part: 2
module: Novel
type: reading
project_key: part2-novel
project_title: Part 2 Novel
project_role: consolidator
---

Body
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual(index.warnings, ())
        seed = index.project_seed("part2-novel")
        assert seed is not None
        self.assertEqual(seed.exercise_id, "part2/a.md")
        self.assertEqual(seed.status, "ongoing")
        self.assertEqual(seed.effective_project_role, "seed")
        self.assertEqual(index.get("part2/b.md").project_title, "Part 2 Novel")
        self.assertEqual(index.get("part2/b.md").effective_project_role, "consolidator")

    def test_load_content_index_keeps_project_group_without_seed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part2").mkdir()
            (root / "part2" / "a.md").write_text(
                """---
title: First
part: 2
module: Novel
type: exercise
project_key: part2-novel
project_title: Novel A
---

Body
""",
                encoding="utf-8",
            )
            (root / "part2" / "b.md").write_text(
                """---
title: Second
part: 2
module: Novel
type: long-term
save_mode: project
project_key: part2-novel
project_title: Novel B
---

Body
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual(index.warnings, ())
        self.assertEqual(index.get("part2/a.md").project_key, "part2-novel")
        self.assertEqual(index.get("part2/b.md").project_key, "part2-novel")
        self.assertIsNone(index.project_seed("part2-novel"))

    def test_load_content_index_clears_seed_without_project_key(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part1").mkdir()
            (root / "part1" / "a.md").write_text(
                """---
title: Seedless Flag
part: 1
module: Start
type: exercise
project_seed: true
---

Body
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual(len(index.warnings), 1)
        self.assertFalse(index.get("part1/a.md").project_seed)

    def test_load_content_index_clears_multiple_seed_flags_but_keeps_project_membership(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part4").mkdir()
            (root / "part4" / "a.md").write_text(
                """---
title: Studio
part: 4
module: Portfolio
type: long-term
save_mode: project
project_key: part4-portfolio
project_seed: true
---

Body
""",
                encoding="utf-8",
            )
            (root / "part4" / "b.md").write_text(
                """---
title: Reading
part: 4
module: Portfolio
type: reading
project_key: part4-portfolio
project_seed: true
---

Body
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual(len(index.warnings), 1)
        self.assertEqual(index.get("part4/a.md").project_key, "part4-portfolio")
        self.assertFalse(index.get("part4/a.md").project_seed)
        self.assertFalse(index.get("part4/b.md").project_seed)
        self.assertIsNone(index.project_seed("part4-portfolio"))

    def test_project_groups_sort_cross_part_contributors_by_part_then_filename(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part1").mkdir()
            (root / "part2").mkdir()
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
            (root / "part1" / "15-world.md").write_text(
                """---
title: World
part: 1
module: Novel
type: exercise
project_key: dark-fantasy-novel
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
            (root / "part2" / "p2-04-pov.md").write_text(
                """---
title: POV
part: 2
module: Novel
type: exercise
project_key: dark-fantasy-novel
---

Body
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        groups = {group.project_key: group for group in index.project_groups()}
        group = groups["dark-fantasy-novel"]
        self.assertEqual(group.parts, (1, 2))
        self.assertEqual(group.contributor_count, 4)
        self.assertEqual(
            [item.exercise.exercise_id for item in group.contributors],
            [
                "part1/15-world.md",
                "part1/20-seed.md",
                "part2/p2-04-pov.md",
                "part2/p2-10-end.md",
            ],
        )
        assert group.seed is not None
        self.assertEqual(group.seed.exercise_id, "part1/20-seed.md")

    def test_load_content_index_normalizes_pseudo_table_intro_blocks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part1").mkdir()
            (root / "part1" / "intro.md").write_text(
                """---
title: Intro
part: 1
module: Start
type: reading
---

---

---

  **This document is
  for**              Helping you start.
  ------------------ -----------------
  **It builds**      Confidence
                     and momentum.

---

# Next section
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual(len(index.exercises), 1)
        self.assertEqual(
            index.exercises[0].body,
            "- **This document is for**: Helping you start.\n"
            "- **It builds**: Confidence and momentum.\n"
            "---\n\n"
            "# Next section",
        )

    def test_load_content_index_reflows_wrapped_paragraphs_and_list_items(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part1").mkdir()
            (root / "part1" / "wrapped.md").write_text(
                """---
title: Wrapped
part: 1
module: Start
type: reading
---

This is a paragraph that was exported with hard line breaks
even though it should read as one normal paragraph in the app.

- This list item was also wrapped by the source export
  and should read as a single bullet line.

## Heading

Another wrapped paragraph
that should reflow cleanly.
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual(
            index.exercises[0].body,
            "This is a paragraph that was exported with hard line breaks even though it should read as one normal paragraph in the app.\n\n"
            "- This list item was also wrapped by the source export and should read as a single bullet line.\n\n"
            "## Heading\n\n"
            "Another wrapped paragraph that should reflow cleanly.",
        )

    def test_load_content_index_rewrites_ascii_box_reading_list_blocks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "part1").mkdir()
            (root / "part1" / "reading.md").write_text(
                """---
title: Reading
part: 1
module: List
type: reading
---

**Novels**

+----------------------------------+
| **Book Title** *by Author Name*  |
|                                  |
| **Weeks 1--2** *Library copy*    |
|                                  |
| This description was exported    |
| with hard line breaks.           |
|                                  |
| **What to watch for:** *Notice*  |
+----------------------------------+
""",
                encoding="utf-8",
            )
            index = load_content_index(root)

        self.assertEqual(
            index.exercises[0].body,
            "**Novels**\n\n"
            "**Book Title** *by Author Name*\n\n"
            "**Weeks 1--2** *Library copy*\n\n"
            "This description was exported with hard line breaks.\n\n"
            "**What to watch for:** *Notice*",
        )

    def test_render_markdown_fallback_extracts_text(self) -> None:
        output = render_markdown_fallback("# Heading\n\n- **One**\n- Two\n\n`code`")
        self.assertIn("Heading", output)
        self.assertIn("- One", output)
        self.assertIn("code", output)
        self.assertNotIn("**", output)


if __name__ == "__main__":
    unittest.main()
