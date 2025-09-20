"""
Image Vault – local image tagger (FastAPI + SQLite)

Quick start
-----------
1) python -m venv .venv && source .venv/bin/activate  # or .venv\\Scripts\\activate on Windows
2) pip install fastapi uvicorn[standard] sqlmodel jinja2 pillow python-multipart
3) python app.py  # auto-writes templates/static and DB
4) Open http://localhost:8000 → set your Vault root folder → Scan

Notes
-----
• Everything runs locally and stores data in ./image_vault.db.
• Thumbnails are cached under <root>/.vault_thumbs/.
• Use tags to model workflow states: Needs inpainting, Ready for i2v, Ready for upscale, etc.
• Keep root pointing to your "Vault" folder; scanning is idempotent and fast after the first run.
"""

import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from database import get_session, init_db
from models import Image, Tag
from routes import (
    api_get_tags,
    assign_tag,
    bulk_add_tag,
    bulk_delete_images,
    bulk_export_images,
    bulk_remove_tag,
    create_tag,
    dashboard,
    delete_image,
    delete_tag,
    export_execute,
    export_preview,
    get_tags,
    image_detail,
    index,
    list_images,
    media,
    open_folder,
    remove_tag,
    scan_route,
    settings,
    thumbnail,
    update_tag,
)
from templates_static import ensure_assets

# Configuration
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

# Create FastAPI app
app = FastAPI(title="Image Vault")

# Ensure templates and static files exist
ensure_assets()

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def seed_default_tags() -> None:
    """Seed database with default tags based on scanned folder structure."""
    # Only add workflow-related default tags, let folder scanning create location tags
    workflow_tags = [
        ("Needs Inpainting", "#ffb703"),
        ("Ready For i2v", "#06d6a0"),
        ("Ready for Upscale", "#8ecae6"),
        ("SFW", "#4ade80"),
        ("NSFW", "#f87171"),
        ("Posted", "#4ade80"),  # Green color for posted tag
        ("i2v done", "#06d6a0"),  # Teal color for i2v done tag
    ]

    with get_session() as s:
        # Add workflow tags
        for name, color in workflow_tags:
            if not s.exec(select(Tag).where(Tag.name == name)).first():
                s.add(Tag(name=name, color=color))

        # Get folder names from existing images to create location/category tags
        from pathlib import Path
        from database import get_setting
        from scanner import extract_tags_from_path

        root_dir_setting = get_setting("root_dir")
        if root_dir_setting and Path(root_dir_setting).exists():
            root_path = Path(root_dir_setting)
            folder_tags = set()

            # Collect all unique folder tags from existing images
            images = s.exec(select(Image)).all()
            for img in images:
                try:
                    img_path = Path(img.path)
                    if img_path.exists():
                        tags = extract_tags_from_path(img_path, root_path)
                        folder_tags.update(tags)
                except Exception:
                    continue

            # Create tags for folders with some default colors
            color_palette = [
                "#93c5fd",
                "#22d3ee",
                "#a78bfa",
                "#fb7185",
                "#fde68a",
                "#f472b6",
                "#34d399",
                "#fbbf24",
                "#fb923c",
                "#c084fc",
            ]

            for i, tag_name in enumerate(sorted(folder_tags)):
                if not s.exec(select(Tag).where(Tag.name == tag_name)).first():
                    color = color_palette[i % len(color_palette)]
                    s.add(Tag(name=tag_name, color=color))

        s.commit()


# Initialize database and seed tags
init_db()
seed_default_tags()

# Routes
app.get("/", response_class="HTMLResponse")(index)
app.get("/dashboard", response_class="HTMLResponse")(dashboard)
app.get("/settings", response_class="HTMLResponse")(settings)
app.post("/scan", response_class="HTMLResponse")(scan_route)
app.get("/tags", response_class="HTMLResponse")(get_tags)
app.post("/tags", response_class="HTMLResponse")(create_tag)
app.post("/tags/{tag_id}/update")(update_tag)
app.post("/tags/{tag_id}/delete")(delete_tag)
app.get("/images", response_class="HTMLResponse")(list_images)
app.get("/images/", response_class="HTMLResponse")(list_images)

# Bulk operations - MUST come before {image_id} routes to avoid route conflicts
app.post("/images/bulk/delete")(bulk_delete_images)
app.post("/images/bulk/add_tag")(bulk_add_tag)
app.post("/images/bulk/remove_tag")(bulk_remove_tag)
app.post("/images/bulk/export")(bulk_export_images)

# Individual image routes
app.get("/images/{image_id}", response_class="HTMLResponse")(image_detail)
app.post("/images/{image_id}/tags/assign")(assign_tag)
app.post("/images/{image_id}/tags/remove")(remove_tag)
app.post("/images/{image_id}/delete")(delete_image)
app.post("/images/{image_id}/open_folder")(open_folder)
app.get("/media/{image_id}")(media)
app.get("/thumb/{image_id}")(thumbnail)

# Export functionality
app.get("/export/preview", response_class="HTMLResponse")(export_preview)
app.post("/export/execute")(export_execute)

# API endpoints
app.get("/api/tags")(api_get_tags)


if __name__ == "__main__":
    # Allow `python app.py 8000`
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    print(f"→ Open http://localhost:{port}")
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=port, reload=True)
