"""Database models for Image Vault."""
from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class ImageTagLink(SQLModel, table=True):
    """Link table for many-to-many relationship between images and tags."""
    image_id: Optional[int] = Field(
        default=None, foreign_key="image.id", primary_key=True
    )
    tag_id: Optional[int] = Field(default=None, foreign_key="tag.id", primary_key=True)


class Tag(SQLModel, table=True):
    """Tag model for categorizing images."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    color: Optional[str] = None
    description: Optional[str] = None


class Image(SQLModel, table=True):
    """Image model representing files in the vault."""
    id: Optional[int] = Field(default=None, primary_key=True)
    path: str = Field(index=True, unique=True, description="Absolute path")
    filename: str = Field(index=True)
    dirpath: str = Field(index=True)
    size: int = 0
    width: int = 0
    height: int = 0
    mtime: float = 0.0
    file_hash: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Setting(SQLModel, table=True):
    """Application settings."""
    key: str = Field(primary_key=True)
    value: str