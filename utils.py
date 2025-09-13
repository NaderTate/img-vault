"""Utility functions."""
from pathlib import Path

from fastapi import HTTPException


def resolve_under_root(root: Path, candidate: Path) -> Path:
    """Resolve a path ensuring it's under the root directory."""
    real = candidate.resolve()
    if root not in real.parents and real != root:
        raise HTTPException(status_code=400, detail="Path is outside root")
    return real