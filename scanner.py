"""Image scanning utilities."""
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Iterable

from fastapi import HTTPException
from PIL import Image as PILImage, ImageOps
from sqlmodel import select

from database import get_session
from models import Image, Tag, ImageTagLink

# Configuration
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_THUMB_DIRNAME = ".vault_thumbs"


def iter_image_files(root: Path) -> Iterable[Path]:
    """Iterate through all image files in the root directory, excluding thumbnails and system folders."""
    excluded_folders = {DEFAULT_THUMB_DIRNAME, '.vault_thumbs', 'thumbs', '.git', '.DS_Store', '__pycache__'}
    
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTS:
            # Check if any parent directory is in the excluded list
            if not any(part in excluded_folders for part in p.parts):
                yield p


def md5sum(path: Path, chunk: int = 256 * 1024) -> str:
    """Calculate MD5 hash of a file."""
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_image_meta(path: Path) -> tuple[int, int]:
    """Read image dimensions."""
    im = PILImage.open(path)
    im = ImageOps.exif_transpose(im)
    return im.width, im.height


def extract_tags_from_path(image_path: Path, root_dir: Path) -> list[str]:
    """Extract tag names from folder structure relative to root directory."""
    try:
        # Get relative path from root to image
        relative_path = image_path.relative_to(root_dir)
        
        # Get all parent folders (excluding the image filename)
        folders = relative_path.parent.parts
        
        # Filter out common non-tag folders and clean up names
        excluded_folders = {'.vault_thumbs', 'thumbs', 'cache', '.git', '.DS_Store'}
        
        tags = []
        for folder in folders:
            if folder and folder not in excluded_folders:
                # Clean folder name: replace underscores/dashes with spaces, title case
                clean_name = folder.replace('_', ' ').replace('-', ' ').strip()
                if clean_name and len(clean_name) > 1:
                    # Convert to title case but preserve certain common patterns
                    clean_name = clean_name.title()
                    # Handle special cases
                    clean_name = clean_name.replace('Nsfw', 'NSFW').replace('Sfw', 'SFW')
                    clean_name = clean_name.replace('I2V', 'i2v').replace('I2v', 'i2v')
                    tags.append(clean_name)
        
        return tags
    except (ValueError, AttributeError):
        # If path is not relative to root or other errors
        return []


def get_or_create_tag(session, tag_name: str, color: str = None) -> Tag:
    """Get existing tag or create new one."""
    tag = session.exec(select(Tag).where(Tag.name == tag_name)).first()
    if not tag:
        tag = Tag(name=tag_name, color=color)
        session.add(tag)
        session.commit()
        session.refresh(tag)
    return tag


def assign_tags_to_image(session, image: Image, tag_names: list[str]) -> None:
    """Assign tags to an image based on tag names."""
    for tag_name in tag_names:
        tag = get_or_create_tag(session, tag_name)
        
        # Check if association already exists
        existing_link = session.get(ImageTagLink, (image.id, tag.id))
        if not existing_link:
            session.add(ImageTagLink(image_id=image.id, tag_id=tag.id))


def scan(root_dir: Path, cleanup: bool = False, auto_tag: bool = True) -> dict:
    """Index all images under root_dir. Returns scan stats."""
    root_dir = root_dir.resolve()
    if not root_dir.exists() or not root_dir.is_dir():
        raise HTTPException(400, "Invalid root directory")

    thumb_dir = root_dir / DEFAULT_THUMB_DIRNAME
    thumb_dir.mkdir(exist_ok=True)

    added = updated = unchanged = 0
    seen_paths: set[str] = set()

    with get_session() as s:
        for file in iter_image_files(root_dir):
            apath = str(file.resolve())
            seen_paths.add(apath)
            stat = file.stat()
            db_img: Image | None = s.exec(
                select(Image).where(Image.path == apath)
            ).first()
            if not db_img:
                try:
                    w, h = read_image_meta(file)
                except Exception:
                    w = h = 0
                db_img = Image(
                    path=apath,
                    filename=file.name,
                    dirpath=str(file.parent),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                    width=w,
                    height=h,
                    file_hash=md5sum(file),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                s.add(db_img)
                s.commit()  # Commit to get the image ID
                s.refresh(db_img)
                
                # Auto-tag based on folder structure
                if auto_tag:
                    folder_tags = extract_tags_from_path(file, root_dir)
                    if folder_tags:
                        assign_tags_to_image(s, db_img, folder_tags)
                
                added += 1
            else:
                # only recompute metadata when size/mtime changed (why: speed)
                if db_img.size != stat.st_size or db_img.mtime != stat.st_mtime:
                    try:
                        w, h = read_image_meta(file)
                    except Exception:
                        w, h = db_img.width, db_img.height
                    db_img.size = stat.st_size
                    db_img.mtime = stat.st_mtime
                    db_img.width = w
                    db_img.height = h
                    db_img.file_hash = md5sum(file)
                    db_img.updated_at = datetime.utcnow()
                    updated += 1
                else:
                    unchanged += 1
                
                # Auto-tag existing images if requested (regardless of whether they were updated)
                if auto_tag:
                    folder_tags = extract_tags_from_path(file, root_dir)
                    if folder_tags:
                        assign_tags_to_image(s, db_img, folder_tags)
        if cleanup:
            rows = s.exec(select(Image)).all()
            for img in rows:
                if img.path not in seen_paths:
                    s.delete(img)
            # Optionally wipe stale thumbs
            for t in (root_dir / DEFAULT_THUMB_DIRNAME).glob("*.jpg"):
                try:
                    int(t.stem.split("_")[0])
                except Exception:
                    continue
        s.commit()

    return {"added": added, "updated": updated, "unchanged": unchanged}