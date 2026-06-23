"""Attachment storage — files are COPIED into the location's managed storage so a
project/workspace stays self-contained and portable (never referenced in place).
Stdlib only.
"""
from __future__ import annotations

from pathlib import Path

from . import paths

# Extensions we treat as inline-able text (everything else is referenced by path).
_TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".bash", ".zsh",
    ".html", ".css", ".scss", ".c", ".h", ".cpp", ".hpp", ".cc", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".sql", ".csv", ".tsv", ".xml", ".env",
    ".log", ".tex", ".r", ".swift", ".lua", ".pl", ".gitignore", ".dockerfile",
}


def attach_dir(location: dict, conversation_id: str) -> Path:
    if location.get("type") in ("project", "workspace") and location.get("root_path"):
        d = Path(location["root_path"]) / ".scry" / "web" / "attachments" / conversation_id
    else:
        d = paths.web_dir() / "attachments" / conversation_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_name(filename: str) -> str:
    name = Path(filename or "file").name.strip() or "file"
    return name.replace("/", "_").replace("\\", "_")


def _looks_text(filename: str, data: bytes) -> bool:
    ext = Path(filename).suffix.lower()
    if ext in _TEXT_EXT:
        return True
    if not data:
        return True
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    # mostly-printable heuristic
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def save_upload(location: dict, conversation_id: str, filename: str,
                data: bytes) -> dict:
    """Copy uploaded bytes into managed storage; return an attachment record dict
    (filename, path, size, is_text). Names collide-proofed with a numeric suffix."""
    base = _safe_name(filename)
    dest_dir = attach_dir(location, conversation_id)
    dest = dest_dir / base
    n = 1
    stem, dot, ext = base.rpartition(".")
    while dest.exists():
        if dot:
            dest = dest_dir / f"{stem}-{n}.{ext}"
        else:
            dest = dest_dir / f"{base}-{n}"
        n += 1
    dest.write_bytes(data)
    return {"filename": base, "path": str(dest), "size": len(data),
            "is_text": _looks_text(base, data)}
