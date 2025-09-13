"""FastAPI routes for Image Vault."""
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Form, HTTPException, Query, Request
from typing import List as ListType
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image as PILImage, ImageOps
from sqlmodel import select
from sqlalchemy import text, func

from database import get_session, get_setting, set_setting, engine
from models import Image, ImageTagLink, Tag, SQLModel
from scanner import scan, DEFAULT_THUMB_DIRNAME
from utils import resolve_under_root

# Configuration
APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
PAGE_SIZE_DEFAULT = 100

# Jinja environment
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def fmt_datetime(value):
    """Format datetime for templates."""
    try:
        return (
            datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(value, (int, float))
            else value.strftime("%Y-%m-%d %H:%M:%S")
        )
    except Exception:
        return str(value)


jinja_env.filters["datetime"] = fmt_datetime


def render(name: str, **ctx) -> HTMLResponse:
    """Render template with context."""
    template = jinja_env.get_template(name)

    # helpers
    def pager_url(page: int) -> str:
        params = []
        if ctx.get("q"):
            params.append(f"q={ctx['q']}")
        if ctx.get("active_tag"):
            params.append(f"tag={ctx['active_tag']}")
        if ctx.get("selected_tags"):
            params.append(f"tags={ctx['selected_tags']}")
        if ctx.get("exclude_tags"):
            params.append(f"exclude_tags={ctx['exclude_tags']}")
        if ctx.get("page_size") and ctx.get("page_size") != PAGE_SIZE_DEFAULT:
            params.append(f"page_size={ctx['page_size']}")
        params.append(f"page={page}")
        return f"/images?{'&'.join(params)}"

    ctx.setdefault("title", "Image Vault")
    ctx.setdefault("pager_url", pager_url)
    return HTMLResponse(template.render(**ctx))


def index():
    """Root route redirects to images."""
    return RedirectResponse("/images")


def settings(request: Request, msg: Optional[str] = None):
    """Settings page."""
    return render(
        "settings.html",
        title="Settings",
        root=get_setting("root_dir"),
        last_scan=get_setting("last_scan"),
        msg=msg,
    )


def scan_route(
    root_dir: str = Form(...), 
    cleanup: Optional[str] = Form(None),
    auto_tag: Optional[str] = Form(None),
    reset_db: Optional[str] = Form(None)
):
    """Scan images route."""
    
    # Reset database if requested
    if reset_db:
        # Drop and recreate all tables - this is the cleanest approach
        from database import init_db
        SQLModel.metadata.drop_all(engine)
        SQLModel.metadata.create_all(engine)
        # Reinitialize with default tags
        from app import seed_default_tags
        seed_default_tags()
    
    # Set root dir after potential database reset
    set_setting("root_dir", root_dir)
    
    stats = scan(Path(root_dir), cleanup=bool(cleanup), auto_tag=bool(auto_tag))
    set_setting("last_scan", str(datetime.utcnow().timestamp()))
    
    auto_tag_msg = "+auto-tagged" if auto_tag else ""
    reset_msg = "+database-reset" if reset_db else ""
    
    return RedirectResponse(
        url=f"/settings?msg=Scanned:+added+{stats['added']}+updated+{stats['updated']}+unchanged+{stats['unchanged']}{auto_tag_msg}{reset_msg}",
        status_code=303,
    )


def get_tags():
    """Get all tags."""
    with get_session() as s:
        tags = s.exec(select(Tag).order_by(Tag.name)).all()
        # Add image count to each tag manually
        tag_counts = {}
        for tag in tags:
            count = len(s.exec(
                select(ImageTagLink).where(ImageTagLink.tag_id == tag.id)
            ).all())
            tag_counts[tag.id] = count
    return render("tags.html", title="Tags", tags=tags, tag_counts=tag_counts)


def create_tag(
    name: str = Form(...),
    color: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
):
    """Create a new tag."""
    name = name.strip()
    if not name:
        raise HTTPException(400, "Tag name required")
    with get_session() as s:
        existing = s.exec(select(Tag).where(Tag.name == name)).first()
        if existing:
            return RedirectResponse("/tags", 303)
        s.add(Tag(name=name, color=color or None, description=description or None))
        s.commit()
    return RedirectResponse("/tags", 303)


def update_tag(
    tag_id: int,
    name: str = Form(...),
    color: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
):
    """Update a tag."""
    name = name.strip()
    if not name:
        raise HTTPException(400, "Tag name required")
    
    with get_session() as s:
        tag = s.get(Tag, tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")
        
        # Check if name is taken by another tag
        existing = s.exec(select(Tag).where(Tag.name == name, Tag.id != tag_id)).first()
        if existing:
            raise HTTPException(400, "Tag name already exists")
        
        tag.name = name
        tag.color = color or None
        tag.description = description or None
        s.commit()
    
    return RedirectResponse("/tags", 303)


def delete_tag(tag_id: int):
    """Delete a tag."""
    with get_session() as s:
        tag = s.get(Tag, tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")
        s.delete(tag)
        s.commit()
    return RedirectResponse("/tags", 303)


def list_images(
    request: Request,
    q: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    tags: Optional[str] = Query(None),  # This is the comma-separated tags parameter
    exclude_tags: Optional[str] = Query(None),  # Comma-separated excluded tags
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE_DEFAULT, ge=1, le=500),
):
    """List images with optional filtering."""
    tags_param = tags  # Store the query parameter to avoid variable name confusion
    with get_session() as s:
        stmt = select(Image).order_by(Image.updated_at.desc())
        if q:
            stmt = stmt.where(Image.filename.contains(q))
        
        # Handle single tag (for backward compatibility)
        if tag:
            t = s.exec(select(Tag).where(Tag.name == tag)).first()
            if t:
                # join via link table
                stmt = stmt.join(ImageTagLink, Image.id == ImageTagLink.image_id).where(
                    ImageTagLink.tag_id == t.id
                )
        
        # Handle multiple tags
        if tags_param:
            tag_names = [t.strip() for t in tags_param.split(',') if t.strip()]
            if tag_names:
                # Get tag IDs for all selected tags
                tag_ids = []
                for tag_name in tag_names:
                    t = s.exec(select(Tag).where(Tag.name == tag_name)).first()
                    if t:
                        tag_ids.append(t.id)
                
                if tag_ids:
                    # Find images that have ALL the specified tags
                    subquery = (
                        select(ImageTagLink.image_id)
                        .where(ImageTagLink.tag_id.in_(tag_ids))
                        .group_by(ImageTagLink.image_id)
                        .having(func.count(ImageTagLink.tag_id) == len(tag_ids))
                    )
                    stmt = stmt.where(Image.id.in_(subquery))
        
        # Handle tag exclusions
        if exclude_tags:
            exclude_tag_names = [t.strip() for t in exclude_tags.split(',') if t.strip()]
            for tag_name in exclude_tag_names:
                t = s.exec(select(Tag).where(Tag.name == tag_name)).first()
                if t:
                    # Use NOT EXISTS to exclude images with this tag
                    subquery = select(ImageTagLink.image_id).where(
                        ImageTagLink.tag_id == t.id
                    )
                    stmt = stmt.where(~Image.id.in_(subquery))
        total = len(s.exec(stmt).all())
        offset = (page - 1) * page_size
        images = s.exec(stmt.offset(offset).limit(page_size)).all()
        # Load tags for each image manually
        image_tags = {}
        for img in images:
            tag_ids = s.exec(
                select(ImageTagLink.tag_id).where(ImageTagLink.image_id == img.id)
            ).all()
            if tag_ids:
                image_tags[img.id] = s.exec(
                    select(Tag).where(Tag.id.in_(tag_ids))
                ).all()
            else:
                image_tags[img.id] = []
        tags = s.exec(select(Tag).order_by(Tag.name)).all()
        quick_tags = s.exec(
            select(Tag).where(
                Tag.name.in_(["Needs inpainting", "Ready for i2v", "Ready for upscale"])
            )
        ).all()
    return render(
        "images.html",
        title="Images",
        images=images,
        image_tags=image_tags,
        tags=tags,  # This is the list of Tag objects
        quick_tags=quick_tags,
        page=page,
        page_size=page_size,
        has_more=(offset + len(images)) < total,
        total=total,
        q=q,
        active_tag=tag,
        selected_tags=tags_param,  # This is the comma-separated string from the query parameter
        exclude_tags=exclude_tags,
        current_url=str(request.url),
    )


def image_detail(image_id: int):
    """Show individual image details."""
    with get_session() as s:
        img = s.get(Image, image_id)
        if not img:
            raise HTTPException(404, "Not found")
        
        # Load tags for this image manually
        tag_ids = s.exec(
            select(ImageTagLink.tag_id).where(ImageTagLink.image_id == img.id)
        ).all()
        if tag_ids:
            image_tags = s.exec(
                select(Tag).where(Tag.id.in_(tag_ids))
            ).all()
        else:
            image_tags = []
            
        tags = s.exec(select(Tag).order_by(Tag.name)).all()
        quick_tags = s.exec(
            select(Tag).where(
                Tag.name.in_(["Needs inpainting", "Ready for i2v", "Ready for upscale"])
            )
        ).all()
    return render(
        "image.html", title=img.filename, image=img, image_tags=image_tags, tags=tags, quick_tags=quick_tags
    )


def assign_tag(
    image_id: int, 
    name: Optional[str] = Form(None), 
    tag_id: Optional[int] = Form(None),
    return_to: Optional[str] = Form(None)
):
    """Assign a tag to an image."""
    with get_session() as s:
        img = s.get(Image, image_id)
        if not img:
            raise HTTPException(404, "Image not found")
        tag: Optional[Tag] = None
        if tag_id:
            tag = s.get(Tag, tag_id)
        elif name:
            name = name.strip()
            tag = s.exec(select(Tag).where(Tag.name == name)).first()
            if not tag:
                tag = Tag(name=name)
                s.add(tag)
                s.commit()
                s.refresh(tag)
        if not tag:
            raise HTTPException(400, "Tag required")
        link = s.get(ImageTagLink, (img.id, tag.id))
        if not link:
            s.add(ImageTagLink(image_id=img.id, tag_id=tag.id))
            s.commit()
    
    # Return to previous page with filters preserved or image detail
    redirect_url = return_to if return_to else f"/images/{image_id}"
    return RedirectResponse(redirect_url, 303)


def remove_tag(image_id: int, tag_id: int = Form(...), return_to: Optional[str] = Form(None)):
    """Remove a tag from an image."""
    with get_session() as s:
        link = s.get(ImageTagLink, (image_id, tag_id))
        if link:
            s.delete(link)
            s.commit()
    
    # Return to previous page with filters preserved or image detail
    redirect_url = return_to if return_to else f"/images/{image_id}"
    return RedirectResponse(redirect_url, 303)


def delete_image(image_id: int):
    """Delete a single image from detail page."""
    root = get_setting("root_dir")
    with get_session() as s:
        img = s.get(Image, image_id)
        if not img:
            raise HTTPException(404, "Image not found")
        
        # Delete actual file if it exists
        if root:
            actual_path = resolve_under_root(Path(root), Path(img.path))
            if actual_path.exists():
                actual_path.unlink()
        
        # Delete image tag links first
        links = s.exec(select(ImageTagLink).where(ImageTagLink.image_id == image_id)).all()
        for link in links:
            s.delete(link)
        
        # Delete the image from database
        s.delete(img)
        s.commit()
    
    return RedirectResponse("/images", 303)


def media(image_id: int):
    """Serve original media file."""
    root = get_setting("root_dir")
    if not root:
        raise HTTPException(400, "Set root folder in Settings")
    with get_session() as s:
        img = s.get(Image, image_id)
        if not img:
            raise HTTPException(404, "Not found")
    real = resolve_under_root(Path(root), Path(img.path))
    if not real.exists():
        raise HTTPException(404, "File missing on disk")
    return FileResponse(real)


def thumbnail(image_id: int, w: int = Query(360, ge=32, le=4096)):
    """Generate and serve thumbnail."""
    root = get_setting("root_dir")
    if not root:
        raise HTTPException(400, "Set root folder in Settings")
    with get_session() as s:
        img = s.get(Image, image_id)
        if not img:
            raise HTTPException(404, "Not found")
    real = resolve_under_root(Path(root), Path(img.path))
    if not real.exists():
        raise HTTPException(404, "File missing on disk")

    thumb_dir = Path(root) / DEFAULT_THUMB_DIRNAME
    thumb_dir.mkdir(exist_ok=True)
    thumb_path = thumb_dir / f"{image_id}_{w}.jpg"

    if (
        not thumb_path.exists()
        or thumb_path.stat().st_mtime < Path(img.path).stat().st_mtime
    ):
        im = PILImage.open(real)
        im = ImageOps.exif_transpose(im)
        im.thumbnail((w, w * 10_000))
        rgb = im.convert("RGB")
        rgb.save(thumb_path, format="JPEG", quality=88)

    return FileResponse(thumb_path)


def bulk_delete_images(
    image_ids: ListType[int] = Form(...), 
    return_to: Optional[str] = Form(None)
):
    """Bulk delete images."""
    root = get_setting("root_dir")
    with get_session() as s:
        for image_id in image_ids:
            img = s.get(Image, image_id)
            if img:
                # Delete actual file if it exists
                if root:
                    actual_path = resolve_under_root(Path(root), Path(img.path))
                    if actual_path.exists():
                        actual_path.unlink()
                
                # Delete image tag links first
                links = s.exec(select(ImageTagLink).where(ImageTagLink.image_id == image_id)).all()
                for link in links:
                    s.delete(link)
                
                # Delete the image from database
                s.delete(img)
        s.commit()
    
    # Return to previous page with filters preserved
    redirect_url = return_to if return_to else "/images"
    return RedirectResponse(redirect_url, 303)


def bulk_add_tag(
    image_ids: ListType[int] = Form(...), 
    tag_name: str = Form(...), 
    return_to: Optional[str] = Form(None)
):
    """Bulk add tag to images."""
    with get_session() as s:
        # Get or create tag
        tag = s.exec(select(Tag).where(Tag.name == tag_name.strip())).first()
        if not tag:
            tag = Tag(name=tag_name.strip())
            s.add(tag)
            s.commit()
            s.refresh(tag)
        
        # Add tag to all selected images
        for image_id in image_ids:
            # Check if link already exists
            existing_link = s.get(ImageTagLink, (image_id, tag.id))
            if not existing_link:
                s.add(ImageTagLink(image_id=image_id, tag_id=tag.id))
        s.commit()
    
    # Return to previous page with filters preserved
    redirect_url = return_to if return_to else "/images"
    return RedirectResponse(redirect_url, 303)


def bulk_remove_tag(
    image_ids: ListType[int] = Form(...), 
    tag_id: int = Form(...), 
    return_to: Optional[str] = Form(None)
):
    """Bulk remove tag from images."""
    with get_session() as s:
        for image_id in image_ids:
            link = s.get(ImageTagLink, (image_id, tag_id))
            if link:
                s.delete(link)
        s.commit()
    
    # Return to previous page with filters preserved
    redirect_url = return_to if return_to else "/images"
    return RedirectResponse(redirect_url, 303)


def bulk_export_images(
    image_ids: ListType[int] = Form(...), 
    return_to: Optional[str] = Form(None)
):
    """Redirect to export page with selected images."""
    # Convert image IDs to a comma-separated string for the export page
    image_ids_str = ','.join(map(str, image_ids))
    
    # Redirect to export preview with the selected image IDs
    return RedirectResponse(f"/export/preview?image_ids={image_ids_str}", 303)


def export_preview(
    request: Request,
    q: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    include_tags: Optional[str] = Query(None),
    exclude_tags: Optional[str] = Query(None),
    image_ids: Optional[str] = Query(None)
):
    """Show export preview page with current filters."""
    with get_session() as s:
        # If specific image IDs are provided, use those instead of filters
        if image_ids:
            id_list = [int(id.strip()) for id in image_ids.split(',') if id.strip().isdigit()]
            if id_list:
                stmt = select(Image).where(Image.id.in_(id_list)).order_by(Image.filename)
                matching_images = s.exec(stmt).all()
            else:
                matching_images = []
        else:
            # Build the same query as list_images but get all results
            stmt = select(Image).order_by(Image.filename)
            
            if q:
                stmt = stmt.where(Image.filename.contains(q))
                
            if tag:
                t = s.exec(select(Tag).where(Tag.name == tag)).first()
                if t:
                    stmt = stmt.join(ImageTagLink, Image.id == ImageTagLink.image_id).where(
                        ImageTagLink.tag_id == t.id
                    )
            
            # Handle additional include tags
            if include_tags:
                include_tag_names = [t.strip() for t in include_tags.split(',') if t.strip()]
                if include_tag_names:
                    # Get tag IDs for all include tags
                    include_tag_ids = []
                    for tag_name in include_tag_names:
                        t = s.exec(select(Tag).where(Tag.name == tag_name)).first()
                        if t:
                            include_tag_ids.append(t.id)
                    
                    if include_tag_ids:
                        # Find images that have ALL the specified tags
                        # Use a subquery to count matching tags per image
                        subquery = (
                            select(ImageTagLink.image_id)
                            .where(ImageTagLink.tag_id.in_(include_tag_ids))
                            .group_by(ImageTagLink.image_id)
                            .having(func.count(ImageTagLink.tag_id) == len(include_tag_ids))
                        )
                        stmt = stmt.where(Image.id.in_(subquery))
            
            if exclude_tags:
                exclude_tag_names = [t.strip() for t in exclude_tags.split(',') if t.strip()]
                for tag_name in exclude_tag_names:
                    t = s.exec(select(Tag).where(Tag.name == tag_name)).first()
                    if t:
                        # Use NOT EXISTS to exclude images with this tag
                        subquery = select(ImageTagLink.image_id).where(
                            ImageTagLink.tag_id == t.id
                        )
                        stmt = stmt.where(~Image.id.in_(subquery))
            
            matching_images = s.exec(stmt).all()
        all_tags = s.exec(select(Tag).order_by(Tag.name)).all()
        
        # Calculate total file size
        total_size = sum(img.size for img in matching_images)
        
    return render(
        "export.html",
        title="Export Images",
        request=request,
        images=matching_images,
        total_count=len(matching_images),
        total_size=total_size,
        all_tags=all_tags,
        current_q=q,
        current_tag=tag,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        image_ids=image_ids
    )


def export_execute(
    destination: str = Form(...),
    q: Optional[str] = Form(None),
    tag: Optional[str] = Form(None),
    include_tags: Optional[str] = Form(None),
    exclude_tags: Optional[str] = Form(None),
    image_ids: Optional[str] = Form(None),
    preserve_structure: Optional[str] = Form(None)
):
    """Execute the export operation."""
    try:
        dest_path = Path(destination)
        if not dest_path.exists():
            dest_path.mkdir(parents=True, exist_ok=True)
            
        root_dir = get_setting("root_dir")
        if not root_dir:
            raise HTTPException(400, "Root folder not configured")
            
        root_path = Path(root_dir)
        
        with get_session() as s:
            # If specific image IDs are provided, use those instead of filters
            if image_ids:
                id_list = [int(id.strip()) for id in image_ids.split(',') if id.strip().isdigit()]
                if id_list:
                    stmt = select(Image).where(Image.id.in_(id_list)).order_by(Image.filename)
                    matching_images = s.exec(stmt).all()
                else:
                    matching_images = []
            else:
                # Use the same query logic as export_preview
                stmt = select(Image).order_by(Image.filename)
                
                if q:
                    stmt = stmt.where(Image.filename.contains(q))
                    
                if tag:
                    t = s.exec(select(Tag).where(Tag.name == tag)).first()
                    if t:
                        stmt = stmt.join(ImageTagLink, Image.id == ImageTagLink.image_id).where(
                            ImageTagLink.tag_id == t.id
                        )
                
                if include_tags:
                    include_tag_names = [t.strip() for t in include_tags.split(',') if t.strip()]
                    for tag_name in include_tag_names:
                        t = s.exec(select(Tag).where(Tag.name == tag_name)).first()
                        if t:
                            stmt = stmt.join(ImageTagLink, Image.id == ImageTagLink.image_id).where(
                                ImageTagLink.tag_id == t.id
                            )
                
                if exclude_tags:
                    exclude_tag_names = [t.strip() for t in exclude_tags.split(',') if t.strip()]
                    for tag_name in exclude_tag_names:
                        t = s.exec(select(Tag).where(Tag.name == tag_name)).first()
                        if t:
                            subquery = select(ImageTagLink.image_id).where(
                                ImageTagLink.tag_id == t.id
                            )
                            stmt = stmt.where(~Image.id.in_(subquery))
                
                matching_images = s.exec(stmt).all()
            
            copied_count = 0
            for img in matching_images:
                source_path = Path(img.path)
                
                if preserve_structure:
                    # Preserve relative folder structure from root
                    try:
                        rel_path = source_path.relative_to(root_path)
                        dest_file = dest_path / rel_path
                        dest_file.parent.mkdir(parents=True, exist_ok=True)
                    except ValueError:
                        # Fallback to flat structure if path not relative to root
                        dest_file = dest_path / source_path.name
                else:
                    # Flat structure - just filename
                    dest_file = dest_path / source_path.name
                    
                    # Handle duplicate names
                    counter = 1
                    while dest_file.exists():
                        name_parts = source_path.stem, counter, source_path.suffix
                        dest_file = dest_path / f"{name_parts[0]}_{name_parts[1]}{name_parts[2]}"
                        counter += 1
                
                if source_path.exists():
                    shutil.copy2(source_path, dest_file)
                    copied_count += 1
        
        return RedirectResponse(
            url=f"/export/preview?msg=Successfully+exported+{copied_count}+images+to+{destination}",
            status_code=303
        )
        
    except Exception as e:
        return RedirectResponse(
            url=f"/export/preview?error=Export+failed:+{str(e)}",
            status_code=303
        )


def open_folder(image_id: int):
    """Open the folder containing the specified image in the system file manager."""
    import subprocess
    import platform
    
    with get_session() as s:
        img = s.get(Image, image_id)
        if not img:
            raise HTTPException(404, "Image not found")
        
        image_path = Path(img.path)
        folder_path = image_path.parent
        
        if not folder_path.exists():
            raise HTTPException(404, "Image folder not found")
        
        try:
            system = platform.system()
            if system == "Windows":
                # Windows: open folder and select the file
                subprocess.run(["explorer", "/select,", str(image_path)], check=True)
            elif system == "Darwin":  # macOS
                # macOS: open folder and select the file
                subprocess.run(["open", "-R", str(image_path)], check=True)
            else:  # Linux and others
                # Linux: open folder (most file managers don't support file selection)
                subprocess.run(["xdg-open", str(folder_path)], check=True)
                
        except subprocess.CalledProcessError as e:
            raise HTTPException(500, f"Failed to open folder: {str(e)}")
        except FileNotFoundError:
            raise HTTPException(500, "File manager not found")
    
    # Return a simple success response
    return {"success": True, "message": "Folder opened"}


def api_get_tags():
    """Get all tags as JSON for API use."""
    with get_session() as s:
        tags = s.exec(select(Tag).order_by(Tag.name)).all()
        return [{"id": tag.id, "name": tag.name, "color": tag.color} for tag in tags]