"""Database configuration and utilities."""
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from sqlmodel import Session, create_engine, select

from models import Setting, SQLModel

# Configuration
APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "image_vault.db"

# Database engine
engine = create_engine(
    f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}
)


@contextmanager
def get_session():
    """Get a database session context manager."""
    with Session(engine) as session:
        yield session


def init_db() -> None:
    """Initialize database tables."""
    SQLModel.metadata.create_all(engine)


def get_setting(key: str) -> Optional[str]:
    """Get a setting value by key."""
    with get_session() as s:
        row = s.get(Setting, key)
        return row.value if row else None


def set_setting(key: str, value: str) -> None:
    """Set a setting value."""
    with get_session() as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
        s.commit()