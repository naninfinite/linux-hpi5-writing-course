from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
    chunks: list[str] = []
    for token in tokens:
        if token.type == "inline" and token.content:
            chunks.append(token.content)
        elif token.type in {"fence", "code_block"} and token.content:
            chunks.append(token.content.rstrip())
        elif token.type == "softbreak":
            chunks.append("\n")
        elif token.type == "hardbreak":
            chunks.append("\n")
    lines = [line.rstrip() for line in "\n".join(chunks).splitlines()]
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
        body=content.strip(),
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
