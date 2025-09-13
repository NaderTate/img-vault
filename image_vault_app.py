# file: image_vault_app.py
"""
Image Vault – local image tagger (FastAPI + SQLite)

Quick start
-----------
1) python -m venv .venv && source .venv/bin/activate  # or .venv\\Scripts\\activate on Windows
2) pip install fastapi uvicorn[standard] sqlmodel jinja2 pillow python-multipart
3) python image_vault_app.py  # auto-writes templates/static and DB
4) Open http://localhost:8000 → set your Vault root folder → Scan

Notes
-----
• Everything runs locally and stores data in ./image_vault.db.
• Thumbnails are cached under <root>/.vault_thumbs/.
• Use tags to model workflow states: Needs inpainting, Ready for i2v, Ready for upscale, etc.
• Keep root pointing to your "Vault" folder; scanning is idempotent and fast after the first run.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, List, TYPE_CHECKING

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image as PILImage, ImageOps
from sqlmodel import Field, Relationship, SQLModel, Session, create_engine, select

if TYPE_CHECKING:
    pass

# -------------------------------
# Configuration
# -------------------------------
APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "image_vault.db"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"
DEFAULT_THUMB_DIRNAME = ".vault_thumbs"
ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PAGE_SIZE_DEFAULT = 100


# -------------------------------
# DB Models
# -------------------------------
class ImageTagLink(SQLModel, table=True):
    image_id: Optional[int] = Field(
        default=None, foreign_key="image.id", primary_key=True
    )
    tag_id: Optional[int] = Field(default=None, foreign_key="tag.id", primary_key=True)


class Tag(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    color: Optional[str] = None
    description: Optional[str] = None

    images: List[Image] = Relationship(back_populates="tags", link_model=ImageTagLink)


class Image(SQLModel, table=True):
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

    tags: List[Tag] = Relationship(back_populates="images", link_model=ImageTagLink)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


# -------------------------------
# App + DB
# -------------------------------
app = FastAPI(title="Image Vault")
engine = create_engine(
    f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}
)


@contextmanager
def get_session():
    with Session(engine) as session:
        yield session


# -------------------------------
# Templates & Static bootstrapping
# -------------------------------
BASE_HTML = """{% macro tag_chip(tag, removable=False, image_id=None) %}
<span class="chip" style="--c: {{ tag.color or '#444' }}">
  {{ tag.name }}
  {% if removable %}
  <form class="inline" method="post" action="/images/{{ image_id }}/tags/remove">
    <input type="hidden" name="tag_id" value="{{ tag.id }}">
    <button title="Remove">×</button>
  </form>
  {% endif %}
</span>
{% endmacro %}

<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{{ title or 'Image Vault' }}</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <header class="topbar">
    <nav>
      <a href="/images" class="brand">📁 Image Vault</a>
      <a href="/tags">Tags</a>
      <a href="/settings">Settings</a>
      <form class="search" method="get" action="/images">
        <input name="q" value="{{ q or '' }}" placeholder="Search filename…" />
        {% if active_tag %}<input type="hidden" name="tag" value="{{ active_tag }}" />{% endif %}
        <button>Search</button>
      </form>
    </nav>
  </header>
  <main class="container">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
"""

IMAGES_HTML = """{% extends 'base.html' %}
{% block content %}
<h1>Images</h1>
<form class="filters" method="get" action="/images">
  <input type="hidden" name="q" value="{{ q or '' }}" />
  <label>Filter by tag:</label>
  <select name="tag" onchange="this.form.submit()">
    <option value="">— Any —</option>
    {% for t in tags %}
    <option value="{{ t.name }}" {% if active_tag==t.name %}selected{% endif %}>{{ t.name }}</option>
    {% endfor %}
  </select>
  <span class="muted">{{ total }} results</span>
</form>
<div class="grid">
  {% for img in images %}
  <article class="card">
    <a href="/images/{{ img.id }}" title="Open">
      <img loading="lazy" src="/thumb/{{ img.id }}?w=360" alt="{{ img.filename }}" />
    </a>
    <div class="meta">
      <div class="fn">{{ img.filename }}</div>
      <div class="chips">
        {% for t in img.tags %}{{ tag_chip(t) }}{% endfor %}
      </div>
      <details>
        <summary>Tag it</summary>
        <form method="post" action="/images/{{ img.id }}/tags/assign">
          <input name="name" placeholder="Add tag (enter)" list="taglist" />
          <button>Add</button>
        </form>
        <div class="quick">
          {% for t in quick_tags %}
          <form method="post" action="/images/{{ img.id }}/tags/assign" class="inline">
            <input type="hidden" name="name" value="{{ t.name }}" />
            <button type="submit" class="pill">+ {{ t.name }}</button>
          </form>
          {% endfor %}
        </div>
      </details>
    </div>
  </article>
  {% endfor %}
</div>

<datalist id="taglist">
  {% for t in tags %}<option value="{{ t.name }}">{% endfor %}
</datalist>

<div class="pager">
  {% if page>1 %}<a href="{{ pager_url(page-1) }}">← Prev</a>{% endif %}
  <span>Page {{ page }}</span>
  {% if has_more %}<a href="{{ pager_url(page+1) }}">Next →</a>{% endif %}
</div>
{% endblock %}
"""

IMAGE_HTML = """{% extends 'base.html' %}
{% block content %}
<h1>{{ image.filename }}</h1>
<div class="detail">
  <img src="/thumb/{{ image.id }}?w=1200" alt="{{ image.filename }}" />
  <aside>
    <section>
      <h3>Info</h3>
      <div class="kv"><b>Path</b><code>{{ image.path }}</code></div>
      <div class="kv"><b>Size</b><span>{{ image.width }}×{{ image.height }}, {{ image.size }} bytes</span></div>
      <div class="kv"><b>Modified</b><span>{{ image.mtime | datetime }}</span></div>
      <div class="kv"><b>Hash</b><code>{{ image.file_hash or '—' }}</code></div>
      <div class="kv"><b>Open</b><a href="/media/{{ image.id }}" target="_blank">original</a></div>
    </section>
    <section>
      <h3>Tags</h3>
      <div class="chips">
        {% for t in image.tags %}{{ tag_chip(t, True, image.id) }}{% endfor %}
      </div>
      <form method="post" action="/images/{{ image.id }}/tags/assign">
        <input name="name" placeholder="Add tag" list="taglist" />
        <button>Add</button>
      </form>
      <div class="quick">
        {% for t in quick_tags %}
        <form method="post" action="/images/{{ image.id }}/tags/assign" class="inline">
          <input type="hidden" name="name" value="{{ t.name }}" />
          <button type="submit" class="pill">+ {{ t.name }}</button>
        </form>
        {% endfor %}
      </div>
    </section>
  </aside>
</div>

<datalist id="taglist">
  {% for t in tags %}<option value="{{ t.name }}">{% endfor %}
</datalist>
{% endblock %}
"""

TAGS_HTML = """{% extends 'base.html' %}
{% block content %}
<h1>Tags</h1>
<div class="twocol">
  <section>
    <h3>Create</h3>
    <form method="post" action="/tags">
      <label>Name</label>
      <input name="name" required />
      <label>Color</label>
      <input name="color" type="color" value="#4477ff" />
      <label>Description</label>
      <input name="description" />
      <button>Create</button>
    </form>
  </section>
  <section>
    <h3>All tags ({{ tags|length }})</h3>
    <table class="tbl">
      <thead><tr><th>Name</th><th>Color</th><th>Description</th><th>Count</th><th></th></tr></thead>
      <tbody>
      {% for t in tags %}
        <tr>
          <td><a href="/images?tag={{ t.name }}">{{ t.name }}</a></td>
          <td><span class="swatch" style="background: {{ t.color or '#444' }};"></span></td>
          <td>{{ t.description or '' }}</td>
          <td>{{ t.images | length }}</td>
          <td>
            <form method="post" action="/tags/{{ t.id }}/delete" onsubmit="return confirm('Delete tag?');">
              <button class="danger">Delete</button>
            </form>
          </td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </section>
</div>
{% endblock %}
"""

SETTINGS_HTML = """{% extends 'base.html' %}
{% block content %}
<h1>Settings</h1>
<form method="post" action="/scan" class="settings">
  <label>Root folder</label>
  <input name="root_dir" value="{{ root or '' }}" placeholder="/path/to/Vault" required />
  <label class="inline"><input type="checkbox" name="cleanup" value="1"> Remove DB rows for files that disappeared</label>
  <button>Scan Now</button>
</form>
{% if last_scan %}<p class="muted">Last scan: {{ last_scan | datetime }}</p>{% endif %}
{% if msg %}<p class="flash">{{ msg }}</p>{% endif %}
{% endblock %}
"""

APP_CSS = """:root{--bg:#0f1115;--fg:#e5e7eb;--muted:#a1a1aa;--card:#111318;--chip:#2a2e37;--brand:#7aa2ff;--danger:#ff5c5c}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Inter,Ubuntu,Helvetica,Arial}
a{color:var(--brand);text-decoration:none}.muted{color:var(--muted)}
.topbar{position:sticky;top:0;background:#0c0e13;border-bottom:1px solid #1c1f26;z-index:10}
.topbar nav{margin:auto;display:flex;gap:14px;align-items:center;padding:10px}
.topbar .brand{font-weight:700}
.topbar .search{margin-left:auto;display:flex;gap:6px}
.container{margin:20px auto;padding:0 14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{background:var(--card);border:1px solid #1f2430;border-radius:12px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;height:300px;object-fit:cover;display:block;background:#090a0d}
.card .meta{padding:10px;display:flex;flex-direction:column;gap:8px}
.fn{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{display:inline-flex;align-items:center;gap:6px;background:var(--chip);border-radius:999px;padding:2px 8px;border:1px solid #313644}
.chip button{all:unset;cursor:pointer;padding:0 4px}
.inline{display:inline}
.pill{border-radius:999px;border:1px solid #2d3341;background:#1a1d24;color:var(--fg);padding:2px 8px;cursor:pointer}
.filters{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.tbl{width:100%;border-collapse:collapse}.tbl th,.tbl td{border-bottom:1px solid #252a36;padding:8px}
.twocol{display:grid;grid-template-columns:320px 1fr;gap:20px}
.swatch{display:inline-block;width:18px;height:18px;border-radius:4px;border:1px solid #0003}
button{cursor:pointer;background:#1e2635;border:1px solid #2f3748;color:var(--fg);padding:6px 12px;border-radius:8px}
button.danger{background:#3a1313;border-color:#5b1a1a;color:#ffd5d5}
input,select{background:#0e1218;border:1px solid #232a39;color:var(--fg);padding:6px 10px;border-radius:8px;width:100%}
.settings{display:grid;gap:10px;max-width:720px}
.detail{display:grid;grid-template-columns:1fr 320px;gap:20px}
.kv{display:flex;justify-content:space-between;gap:12px}
.pager{display:flex;justify-content:center;align-items:center;gap:10px;margin:16px}
.flash{background:#13221d;border:1px solid #214d39;padding:10px;border-radius:10px}
"""


def ensure_assets() -> None:
    """Create templates/static on first run so this file is standalone."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        TEMPLATES_DIR / "base.html": BASE_HTML,
        TEMPLATES_DIR / "images.html": IMAGES_HTML,
        TEMPLATES_DIR / "image.html": IMAGE_HTML,
        TEMPLATES_DIR / "tags.html": TAGS_HTML,
        TEMPLATES_DIR / "settings.html": SETTINGS_HTML,
        STATIC_DIR / "app.css": APP_CSS,
    }
    for p, content in files.items():
        if not p.exists():
            p.write_text(content, encoding="utf-8")


ensure_assets()

# Jinja env
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def fmt_datetime(value):
    try:
        return (
            datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(value, (int, float))
            else value.strftime("%Y-%m-%d %H:%M:%S")
        )
    except Exception:
        return str(value)


jinja_env.filters["datetime"] = fmt_datetime

# Mount static
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# -------------------------------
# DB init + seed tags
# -------------------------------
def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    default_tags = [
        ("Needs inpainting", "#ffb703"),
        ("Ready For i2v", "#06d6a0"),
        ("Ready for upscale", "#8ecae6"),
        ("SFW", "#4ade80"),
        ("NSFW", "#f87171"),
        ("Home", "#93c5fd"),
        ("Pool", "#22d3ee"),
        ("Outside", "#a78bfa"),
        ("Gym", "#fb7185"),
        ("Beach", "#fde68a"),
        ("Selfies", "#f472b6"),
    ]
    with get_session() as s:
        for name, color in default_tags:
            if not s.exec(select(Tag).where(Tag.name == name)).first():
                s.add(Tag(name=name, color=color))
        s.commit()


init_db()

# -------------------------------
# Utilities
# -------------------------------


def get_setting(key: str) -> Optional[str]:
    with get_session() as s:
        row = s.get(Setting, key)
        return row.value if row else None


def set_setting(key: str, value: str) -> None:
    with get_session() as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
        s.commit()


def resolve_under_root(root: Path, candidate: Path) -> Path:
    real = candidate.resolve()
    if root not in real.parents and real != root:
        raise HTTPException(status_code=400, detail="Path is outside root")
    return real


# -------------------------------
# Scanning
# -------------------------------


def iter_image_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTS:
            yield p


def md5sum(path: Path, chunk: int = 256 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def read_image_meta(path: Path) -> tuple[int, int]:
    im = PILImage.open(path)
    im = ImageOps.exif_transpose(im)
    return im.width, im.height


def scan(root_dir: Path, cleanup: bool = False) -> dict:
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
            db_img: Optional[Image] = s.exec(
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


# -------------------------------
# Rendering helper
# -------------------------------


def render(name: str, **ctx) -> HTMLResponse:
    template = jinja_env.get_template(name)

    # helpers
    def pager_url(page: int) -> str:
        params = []
        if ctx.get("q"):
            params.append(f"q={ctx['q']}")
        if ctx.get("active_tag"):
            params.append(f"tag={ctx['active_tag']}")
        params.append(f"page={page}")
        return f"/images?{'&'.join(params)}"

    ctx.setdefault("title", "Image Vault")
    ctx.setdefault("pager_url", pager_url)
    return HTMLResponse(template.render(**ctx))


# -------------------------------
# Routes
# -------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return RedirectResponse("/images")


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, msg: Optional[str] = None):
    return render(
        "settings.html",
        title="Settings",
        root=get_setting("root_dir"),
        last_scan=get_setting("last_scan"),
        msg=msg,
    )


@app.post("/scan", response_class=HTMLResponse)
def scan_route(root_dir: str = Form(...), cleanup: Optional[str] = Form(None)):
    set_setting("root_dir", root_dir)
    stats = scan(Path(root_dir), cleanup=bool(cleanup))
    set_setting("last_scan", str(datetime.utcnow().timestamp()))
    return RedirectResponse(
        url=f"/settings?msg=Scanned:+added+{stats['added']}+updated+{stats['updated']}+unchanged+{stats['unchanged']}",
        status_code=303,
    )


@app.get("/tags", response_class=HTMLResponse)
def get_tags():
    with get_session() as s:
        tags = s.exec(select(Tag).order_by(Tag.name)).all()
    return render("tags.html", title="Tags", tags=tags)


@app.post("/tags", response_class=HTMLResponse)
def create_tag(
    name: str = Form(...),
    color: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
):
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


@app.post("/tags/{tag_id}/delete")
def delete_tag(tag_id: int):
    with get_session() as s:
        tag = s.get(Tag, tag_id)
        if not tag:
            raise HTTPException(404, "Tag not found")
        s.delete(tag)
        s.commit()
    return RedirectResponse("/tags", 303)


@app.get("/images", response_class=HTMLResponse)
@app.get("/images/", response_class=HTMLResponse)
def list_images(
    q: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(PAGE_SIZE_DEFAULT, ge=1, le=500),
):
    with get_session() as s:
        stmt = select(Image).order_by(Image.updated_at.desc())
        if q:
            stmt = stmt.where(Image.filename.contains(q))
        if tag:
            t = s.exec(select(Tag).where(Tag.name == tag)).first()
            if t:
                # join via link table
                stmt = stmt.join(ImageTagLink, Image.id == ImageTagLink.image_id).where(
                    ImageTagLink.tag_id == t.id
                )
        total = len(s.exec(stmt).all())
        offset = (page - 1) * page_size
        images = s.exec(stmt.offset(offset).limit(page_size)).all()
        # eager-load tags
        for img in images:
            img.tags  # access to populate relationship
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
        tags=tags,
        quick_tags=quick_tags,
        page=page,
        has_more=(offset + len(images)) < total,
        total=total,
        q=q,
        active_tag=tag,
    )


@app.get("/images/{image_id}", response_class=HTMLResponse)
def image_detail(image_id: int):
    with get_session() as s:
        img = s.get(Image, image_id)
        if not img:
            raise HTTPException(404, "Not found")
        tags = s.exec(select(Tag).order_by(Tag.name)).all()
        quick_tags = s.exec(
            select(Tag).where(
                Tag.name.in_(["Needs inpainting", "Ready for i2v", "Ready for upscale"])
            )
        ).all()
    return render(
        "image.html", title=img.filename, image=img, tags=tags, quick_tags=quick_tags
    )


@app.post("/images/{image_id}/tags/assign")
def assign_tag(
    image_id: int, name: Optional[str] = Form(None), tag_id: Optional[int] = Form(None)
):
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
    return RedirectResponse(f"/images/{image_id}", 303)


@app.post("/images/{image_id}/tags/remove")
def remove_tag(image_id: int, tag_id: int = Form(...)):
    with get_session() as s:
        link = s.get(ImageTagLink, (image_id, tag_id))
        if link:
            s.delete(link)
            s.commit()
    return RedirectResponse(f"/images/{image_id}", 303)


@app.get("/media/{image_id}")
def media(image_id: int):
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


@app.get("/thumb/{image_id}")
def thumbnail(image_id: int, w: int = Query(360, ge=32, le=4096)):
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


# -------------------------------
# Main
# -------------------------------
if __name__ == "__main__":
    # Allow `python image_vault_app.py 8000`
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"→ Open http://localhost:{port}")
    import uvicorn

    uvicorn.run("image_vault_app:app", host="127.0.0.1", port=port, reload=True)
