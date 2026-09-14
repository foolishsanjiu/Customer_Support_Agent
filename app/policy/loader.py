import hashlib
from pathlib import Path

from app.policy.models import PolicyChunk

REQUIRED_METADATA = {"policy_id", "policy_type", "version", "updated_at"}


def load_policy_directory(directory: str | Path) -> list[PolicyChunk]:
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"policy directory not found: {root}")
    chunks: list[PolicyChunk] = []
    for path in sorted(root.rglob("*.md")):
        chunks.extend(load_policy_file(path))
    if not chunks:
        raise ValueError("policy directory contains no Markdown documents")
    return chunks


def load_policy_file(path: Path) -> list[PolicyChunk]:
    metadata, body = _split_front_matter(path.read_text(encoding="utf-8"))
    missing = REQUIRED_METADATA - metadata.keys()
    if missing:
        raise ValueError(f"{path.name} missing metadata: {', '.join(sorted(missing))}")

    sections: list[tuple[str, list[str]]] = []
    current_name = "overview"
    current_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if any(item.strip() for item in current_lines):
                sections.append((current_name, current_lines))
            current_name = line[3:].strip()
            current_lines = []
        elif not line.startswith("# "):
            current_lines.append(line)
    if any(item.strip() for item in current_lines):
        sections.append((current_name, current_lines))

    chunks: list[PolicyChunk] = []
    for section, lines in sections:
        content = "\n".join(lines).strip()
        stable = f"{metadata['policy_id']}:{metadata['version']}:{section}"
        chunk_id = hashlib.sha256(stable.encode()).hexdigest()
        chunks.append(
            PolicyChunk(
                id=chunk_id,
                section=section,
                content=content,
                **metadata,
            )
        )
    return chunks


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("policy document must start with front matter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("policy front matter is not closed") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(f"invalid policy metadata line: {line}")
        metadata[key.strip()] = value.strip()
    return metadata, "\n".join(lines[closing + 1 :])
