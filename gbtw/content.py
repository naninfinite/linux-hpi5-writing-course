from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from markdown_it import MarkdownIt

try:
    import frontmatter  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised indirectly in this environment
    frontmatter = None

CONTENT_ROOT = Path.home() / "gbtw" / "content"
PART_DIRECTORIES = ("part1", "part2", "part3", "part4")
VALID_TYPES = {"exercise", "reading", "long-term"}
VALID_STATUSES = {"active", "archived", "optional"}
DEFAULT_SAVE_MODE = "session"
_MARKDOWN_PARSER = MarkdownIt("commonmark")
_PSEUDO_TABLE_SEPARATOR_RE = re.compile(r"^(\s*-{3,})\s+(-{3,}\s*)$")
_HORIZONTAL_RULE_RE = re.compile(r"^\s*---+\s*$")


@dataclass(slots=True, frozen=True)
class Exercise:
    exercise_id: str
    source_path: Path
    title: str
    part: int
    module: str
    type: str
    status: str
    save_mode: str | None
    body: str

    @property
    def is_long_term(self) -> bool:
        return self.type == "long-term"


@dataclass(slots=True, frozen=True)
class ContentIndex:
    exercises: tuple[Exercise, ...]
    warnings: tuple[str, ...]

    def grouped_by_part(self, include_archived: bool = False) -> dict[int, list[Exercise]]:
        grouped: dict[int, list[Exercise]] = {}
        for exercise in self.exercises:
            if exercise.status == "archived" and not include_archived:
                continue
            grouped.setdefault(exercise.part, []).append(exercise)
        return grouped

    def get(self, exercise_id: str) -> Exercise | None:
        for exercise in self.exercises:
            if exercise.exercise_id == exercise_id:
                return exercise
        return None

    def first_available(self) -> Exercise | None:
        return self.exercises[0] if self.exercises else None


def ensure_content_directories(content_root: Path = CONTENT_ROOT) -> None:
    for part_directory in PART_DIRECTORIES:
        (content_root / part_directory).mkdir(parents=True, exist_ok=True)


def load_content_index(content_root: Path = CONTENT_ROOT) -> ContentIndex:
    ensure_content_directories(content_root)
    exercises: list[Exercise] = []
    warnings: list[str] = []
    for markdown_file in _iter_markdown_files(content_root):
        try:
            exercises.append(_load_exercise(markdown_file, content_root))
        except ValueError as exc:
            warnings.append(f"{markdown_file}: {exc}")
        except Exception as exc:  # pragma: no cover - defensive skip for malformed files
            warnings.append(f"{markdown_file}: unexpected error: {exc}")
    exercises.sort(key=lambda item: (item.part, item.exercise_id))
    return ContentIndex(tuple(exercises), tuple(warnings))


def render_markdown_fallback(markdown_text: str) -> str:
    if not markdown_text.strip():
        return ""
    tokens = _MARKDOWN_PARSER.parse(markdown_text)
    lines: list[str] = []
    current_line = ""
    list_stack: list[dict[str, int | str]] = []

    def flush_line(*, blank_after: bool = False) -> None:
        nonlocal current_line
        stripped = current_line.rstrip()
        if stripped:
            lines.extend(stripped.splitlines())
        current_line = ""
        if blank_after and (not lines or lines[-1] != ""):
            lines.append("")

    for token in tokens:
        if token.type == "bullet_list_open":
            list_stack.append({"type": "bullet"})
            continue
        if token.type == "bullet_list_close":
            flush_line(blank_after=True)
            list_stack.pop()
            continue
        if token.type == "ordered_list_open":
            start = token.attrGet("start")
            list_stack.append({"type": "ordered", "next": int(start) if start else 1})
            continue
        if token.type == "ordered_list_close":
            flush_line(blank_after=True)
            list_stack.pop()
            continue
        if token.type == "list_item_open":
            flush_line()
            if list_stack and list_stack[-1]["type"] == "ordered":
                number = int(list_stack[-1]["next"])
                current_line = f"{number}. "
                list_stack[-1]["next"] = number + 1
            else:
                current_line = "- "
            continue
        if token.type == "inline":
            current_line += _render_inline_fallback_text(token)
            continue
        if token.type in {"heading_close", "paragraph_close"}:
            flush_line(blank_after=(token.type == "heading_close" or not list_stack))
            continue
        elif token.type in {"fence", "code_block"} and token.content:
            flush_line(blank_after=bool(lines and lines[-1] != ""))
            lines.extend(token.content.rstrip().splitlines())
            lines.append("")
    flush_line()
    compacted: list[str] = []
    blank_run = 0
    for line in lines:
        if line:
            blank_run = 0
            compacted.append(line)
            continue
        blank_run += 1
        if blank_run <= 1:
            compacted.append("")
    return "\n".join(compacted).strip()


def _render_inline_fallback_text(token: object) -> str:
    children = getattr(token, "children", None)
    if not children:
        return getattr(token, "content", "")
    parts: list[str] = []
    for child in children:
        if child.type == "text" and child.content:
            parts.append(child.content)
        elif child.type == "code_inline" and child.content:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append("\n")
    return "".join(parts)


def _iter_markdown_files(content_root: Path) -> Iterable[Path]:
    for part_directory in PART_DIRECTORIES:
        root = content_root / part_directory
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            if path.is_file():
                yield path


def _load_exercise(markdown_file: Path, content_root: Path) -> Exercise:
    metadata, content = _load_markdown_file(markdown_file)
    title = _require_string(metadata, "title")
    module = _require_string(metadata, "module")
    part = _require_int(metadata, "part")
    exercise_type = _require_string(metadata, "type")
    if exercise_type not in VALID_TYPES:
        raise ValueError(f"invalid type {exercise_type!r}")
    status = metadata.get("status", "active")
    if not isinstance(status, str) or status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    save_mode: str | None = None
    if exercise_type == "long-term":
        save_mode = metadata.get("save_mode", DEFAULT_SAVE_MODE)
        if save_mode not in {"session", "project"}:
            raise ValueError(f"invalid save_mode {save_mode!r}")
    exercise_id = markdown_file.relative_to(content_root).as_posix()
    return Exercise(
        exercise_id=exercise_id,
        source_path=markdown_file,
        title=title,
        part=part,
        module=module,
        type=exercise_type,
        status=status,
        save_mode=save_mode,
        body=_normalize_markdown_content(content),
    )


def _require_string(metadata: dict[str, object], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or invalid {key!r}")
    return value.strip()


def _require_int(metadata: dict[str, object], key: str) -> int:
    value = metadata.get(key)
    if not isinstance(value, int):
        raise ValueError(f"missing or invalid {key!r}")
    return value


def _load_markdown_file(markdown_file: Path) -> tuple[dict[str, object], str]:
    if frontmatter is not None:
        post = frontmatter.load(markdown_file)
        return dict(post.metadata), str(post.content)
    raw_text = markdown_file.read_text(encoding="utf-8")
    return _parse_frontmatter_fallback(raw_text)


def _normalize_markdown_content(markdown_text: str) -> str:
    if not markdown_text.strip():
        return ""
    lines = markdown_text.strip().splitlines()
    lines = _collapse_repeated_horizontal_rules(lines)
    lines = _strip_leading_horizontal_rules(lines)
    lines = _rewrite_pseudo_table_blocks(lines)
    lines = _collapse_repeated_horizontal_rules(lines)
    lines = _collapse_blank_runs(lines)
    return "\n".join(lines).strip()


def _collapse_repeated_horizontal_rules(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    previous_significant_rule = False
    for line in lines:
        if _HORIZONTAL_RULE_RE.match(line):
            if previous_significant_rule:
                continue
            collapsed.append("---")
            previous_significant_rule = True
            continue
        collapsed.append(line)
        if line.strip():
            previous_significant_rule = False
    return collapsed


def _strip_leading_horizontal_rules(lines: list[str]) -> list[str]:
    index = 0
    while index < len(lines) and (_HORIZONTAL_RULE_RE.match(lines[index]) or not lines[index].strip()):
        index += 1
    return lines[index:] if index else lines


def _rewrite_pseudo_table_blocks(lines: list[str]) -> list[str]:
    rewritten: list[str] = []
    index = 0
    while index < len(lines):
        match = _PSEUDO_TABLE_SEPARATOR_RE.match(lines[index])
        if not match:
            rewritten.append(lines[index])
            index += 1
            continue
        start = index - 1
        while start >= 0 and _is_pseudo_table_line(lines[start]):
            start -= 1
        start += 1
        if start < index:
            del rewritten[-(index - start):]
        end = index + 1
        while end < len(lines) and _is_pseudo_table_line(lines[end]):
            end += 1
        rewritten.extend(_format_pseudo_table_block(lines[start:end], len(match.group(1))))
        index = end
    return rewritten


def _is_pseudo_table_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _PSEUDO_TABLE_SEPARATOR_RE.match(line):
        return True
    return line.startswith("  ")


def _format_pseudo_table_block(lines: list[str], split_at: int) -> list[str]:
    bullets: list[str] = []
    left_parts: list[str] = []
    right_parts: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or _PSEUDO_TABLE_SEPARATOR_RE.match(line):
            continue
        row_starts = stripped.startswith("**")
        if row_starts and (left_parts or right_parts):
            bullets.append(_format_pseudo_table_row(left_parts, right_parts))
            left_parts = []
            right_parts = []
        left = line[:split_at].strip()
        right = line[split_at:].strip()
        if left:
            left_parts.append(left)
        if right:
            right_parts.append(right)
    if left_parts or right_parts:
        bullets.append(_format_pseudo_table_row(left_parts, right_parts))
    return bullets or [line for line in lines if line.strip()]


def _format_pseudo_table_row(left_parts: list[str], right_parts: list[str]) -> str:
    left = _join_table_fragments(left_parts)
    right = _join_table_fragments(right_parts)
    if left and right:
        return f"- {left}: {right}"
    if left:
        return f"- {left}"
    return f"- {right}"


def _join_table_fragments(parts: list[str]) -> str:
    joined = " ".join(part.strip() for part in parts if part.strip())
    return re.sub(r"\s+", " ", joined).strip()


def _collapse_blank_runs(lines: list[str]) -> list[str]:
    compacted: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip():
            blank_run = 0
            compacted.append(line.rstrip())
            continue
        blank_run += 1
        if blank_run <= 1:
            compacted.append("")
    return compacted


def _parse_frontmatter_fallback(raw_text: str) -> tuple[dict[str, object], str]:
    if not raw_text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    _, _, remainder = raw_text.partition("---\n")
    frontmatter_text, separator, content = remainder.partition("\n---\n")
    if not separator:
        raise ValueError("unterminated YAML frontmatter")
    metadata: dict[str, object] = {}
    for line in frontmatter_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            raise ValueError(f"invalid frontmatter line {line!r}")
        key = key.strip()
        value = value.strip().strip("'\"")
        if value.isdigit():
            metadata[key] = int(value)
        else:
            metadata[key] = value
    return metadata, content
