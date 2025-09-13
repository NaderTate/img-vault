"""Templates and static file generation."""

from pathlib import Path

# Template content
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
<div class="controls">
  <form class="filters" method="get" action="/images">
    <input type="hidden" name="q" value="{{ q or '' }}" />
    <label>Filter by tag:</label>
    <select name="tag" onchange="this.form.submit()">
      <option value="">— Any —</option>
      {% for t in tags %}
      <option value="{{ t.name }}" {% if active_tag==t.name %}selected{% endif %}>{{ t.name }}</option>
      {% endfor %}
    </select>
    <label>Per page:</label>
    <select name="page_size" onchange="this.form.submit()">
      <option value="20" {% if page_size==20 %}selected{% endif %}>20</option>
      <option value="40" {% if page_size==40 %}selected{% endif %}>40</option>
      <option value="60" {% if page_size==60 %}selected{% endif %}>60</option>
      <option value="100" {% if page_size==100 %}selected{% endif %}>100</option>
      <option value="200" {% if page_size==200 %}selected{% endif %}>200</option>
    </select>
    <span class="muted">{{ total }} results</span>
    <a href="/export/preview?{% if q %}q={{ q }}&{% endif %}{% if active_tag %}tag={{ active_tag }}&{% endif %}" class="export-link">📤 Export Images</a>
  </form>
  
  <div class="bulk-controls" id="bulkControls" style="display: none;">
    <span class="selected-count" id="selectedCount">0 selected</span>
    <button type="button" id="selectAll">Select All</button>
    <button type="button" id="clearSelection">Clear</button>
    <div class="bulk-actions">
      <form method="post" action="/images/bulk/add_tag" id="bulkAddTagForm" style="display: inline;">
        <input name="tag_name" placeholder="Add tag" list="taglist" />
        <button type="submit">+ Add Tag</button>
      </form>
      <form method="post" action="/images/bulk/remove_tag" id="bulkRemoveTagForm" style="display: inline;">
        <select name="tag_id">
          <option value="">Remove tag...</option>
          {% for t in tags %}
          <option value="{{ t.id }}">{{ t.name }}</option>
          {% endfor %}
        </select>
        <button type="submit">- Remove Tag</button>
      </form>
      <form method="post" action="/images/bulk/delete" id="bulkDeleteForm" style="display: inline;">
        <button type="submit" class="danger" onclick="return confirm('Delete selected images? This cannot be undone.')">🗑️ Delete</button>
      </form>
    </div>
  </div>
</div>
<div class="grid">
  {% for img in images %}
  <article class="card" data-image-id="{{ img.id }}">
    <div class="card-header">
      <input type="checkbox" class="image-select" data-image-id="{{ img.id }}" />
    </div>
    <a href="/images/{{ img.id }}" title="Open" class="image-link">
      <img loading="lazy" src="/thumb/{{ img.id }}?w=360" alt="{{ img.filename }}" />
    </a>
    <div class="meta">
      <div class="fn">{{ img.filename }}</div>
      <div class="chips">
        {% for t in image_tags[img.id] %}{{ tag_chip(t) }}{% endfor %}
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

<script>
document.addEventListener('DOMContentLoaded', function() {
    const checkboxes = document.querySelectorAll('.image-select');
    const bulkControls = document.getElementById('bulkControls');
    const selectedCount = document.getElementById('selectedCount');
    const selectAllBtn = document.getElementById('selectAll');
    const clearSelectionBtn = document.getElementById('clearSelection');
    const bulkForms = document.querySelectorAll('#bulkAddTagForm, #bulkRemoveTagForm, #bulkDeleteForm');
    
    let selectedImages = new Set();
    let lastClickedIndex = -1;
    
    function updateBulkControls() {
        const count = selectedImages.size;
        selectedCount.textContent = `\${count} selected`;
        bulkControls.style.display = count > 0 ? 'block' : 'none';
        selectAllBtn.textContent = count === checkboxes.length ? 'Deselect All' : 'Select All';
    }
    
    function updateFormInputs() {
        // Remove existing hidden inputs
        bulkForms.forEach(form => {
            const existingInputs = form.querySelectorAll('input[name="image_ids"]');
            existingInputs.forEach(input => input.remove());
        });
        
        // Add current selection to all forms
        selectedImages.forEach(imageId => {
            bulkForms.forEach(form => {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'image_ids';
                input.value = imageId;
                form.appendChild(input);
            });
        });
    }
    
    // Handle individual checkbox clicks
    checkboxes.forEach((checkbox, index) => {
        checkbox.addEventListener('click', function(e) {
            const imageId = this.dataset.imageId;
            
            // Handle shift+click range selection
            if (e.shiftKey && lastClickedIndex >= 0) {
                const start = Math.min(lastClickedIndex, index);
                const end = Math.max(lastClickedIndex, index);
                
                for (let i = start; i <= end; i++) {
                    const cb = checkboxes[i];
                    const id = cb.dataset.imageId;
                    
                    if (this.checked) {
                        selectedImages.add(id);
                        cb.checked = true;
                        cb.closest('.card').classList.add('selected');
                    } else {
                        selectedImages.delete(id);
                        cb.checked = false;
                        cb.closest('.card').classList.remove('selected');
                    }
                }
            } else {
                // Normal single selection
                if (this.checked) {
                    selectedImages.add(imageId);
                    this.closest('.card').classList.add('selected');
                } else {
                    selectedImages.delete(imageId);
                    this.closest('.card').classList.remove('selected');
                }
            }
            
            lastClickedIndex = index;
            updateBulkControls();
            updateFormInputs();
        });
    });
    
    // Select/Deselect all button
    selectAllBtn.addEventListener('click', function() {
        const selectAll = selectedImages.size !== checkboxes.length;
        
        checkboxes.forEach(checkbox => {
            const imageId = checkbox.dataset.imageId;
            checkbox.checked = selectAll;
            
            if (selectAll) {
                selectedImages.add(imageId);
                checkbox.closest('.card').classList.add('selected');
            } else {
                selectedImages.delete(imageId);
                checkbox.closest('.card').classList.remove('selected');
            }
        });
        
        updateBulkControls();
        updateFormInputs();
    });
    
    // Clear selection button
    clearSelectionBtn.addEventListener('click', function() {
        selectedImages.clear();
        checkboxes.forEach(checkbox => {
            checkbox.checked = false;
            checkbox.closest('.card').classList.remove('selected');
        });
        updateBulkControls();
        updateFormInputs();
    });
    
    // Prevent form submission if no images selected
    bulkForms.forEach(form => {
        form.addEventListener('submit', function(e) {
            if (selectedImages.size === 0) {
                e.preventDefault();
                alert('Please select at least one image.');
                return false;
            }
            
            // For remove tag, check if tag is selected
            if (form.id === 'bulkRemoveTagForm') {
                const tagSelect = form.querySelector('select[name="tag_id"]');
                if (!tagSelect.value) {
                    e.preventDefault();
                    alert('Please select a tag to remove.');
                    return false;
                }
            }
        });
    });
    
    // Prevent clicks on checkboxes from opening image detail
    checkboxes.forEach(checkbox => {
        checkbox.addEventListener('click', function(e) {
            e.stopPropagation();
        });
    });
});
</script>
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
  
  <div class="scan-options">
    <h3>Scan Options</h3>
    <label class="inline">
      <input type="checkbox" name="auto_tag" value="1" checked> 
      Auto-create tags from folder names
    </label>
    <p class="help-text">
      Automatically creates and assigns tags based on folder structure.<br>
      Example: "Ready for upscale/Home/image.jpg" → gets tags "Ready For Upscale" and "Home"
    </p>
    
    <label class="inline">
      <input type="checkbox" name="cleanup" value="1"> 
      Remove orphaned database entries
    </label>
    <p class="help-text">Remove database entries for files that no longer exist on disk.</p>
  </div>
  
  <div class="danger-zone">
    <h3>⚠️ Danger Zone</h3>
    <label class="inline">
      <input type="checkbox" name="reset_db" value="1" id="resetDb"> 
      Reset database before scanning
    </label>
    <p class="help-text danger-text">
      <strong>WARNING:</strong> This will delete ALL images and tags from the database and start fresh. 
      Use this if you want to completely re-organize your tags based on current folder structure.
    </p>
  </div>
  
  <button>Scan Now</button>
</form>
{% if last_scan %}<p class="muted">Last scan: {{ last_scan | datetime }}</p>{% endif %}
{% if msg %}<p class="flash">{{ msg }}</p>{% endif %}

<script>
document.addEventListener('DOMContentLoaded', function() {
    const resetCheckbox = document.getElementById('resetDb');
    const form = document.querySelector('.settings');
    
    form.addEventListener('submit', function(e) {
        if (resetCheckbox.checked) {
            if (!confirm('Are you sure you want to RESET THE DATABASE? This will delete all current tags and image records. This cannot be undone!')) {
                e.preventDefault();
                resetCheckbox.checked = false;
                return false;
            }
        }
    });
});
</script>
{% endblock %}
"""

EXPORT_HTML = """{% extends 'base.html' %}
{% block content %}
<h1>Export Images</h1>

<div class="export-summary">
  <div class="summary-stats">
    <div class="stat">
      <span class="count">{{ total_count }}</span>
      <span class="label">Images</span>
    </div>
    <div class="stat">
      <span class="count">{{ "%.1f"|format(total_size / 1024 / 1024) }} MB</span>
      <span class="label">Total Size</span>
    </div>
  </div>
  
  <div class="current-filters">
    <h3>Current Filters</h3>
    {% if current_q %}
      <span class="filter-chip">
        Search: "{{ current_q }}"
        <a href="/export/preview?{% if current_tag %}tag={{ current_tag }}&{% endif %}{% if include_tags %}include_tags={{ include_tags }}&{% endif %}{% if exclude_tags %}exclude_tags={{ exclude_tags }}{% endif %}" class="remove-filter">×</a>
      </span>
    {% endif %}
    {% if current_tag %}
      <span class="filter-chip">
        Tag: "{{ current_tag }}"
        <a href="/export/preview?{% if current_q %}q={{ current_q }}&{% endif %}{% if include_tags %}include_tags={{ include_tags }}&{% endif %}{% if exclude_tags %}exclude_tags={{ exclude_tags }}{% endif %}" class="remove-filter">×</a>
      </span>
    {% endif %}
    {% if not current_q and not current_tag and not include_tags and not exclude_tags %}
      <span class="muted">All images (no filters applied)</span>
    {% endif %}
  </div>
</div>

<div class="export-options">
  <form id="filterForm" method="get" action="/export/preview" class="filter-section">
    <h3>Adjust Filters</h3>
    
    <!-- Preserve current filters as hidden inputs -->
    {% if current_q %}<input type="hidden" name="q" value="{{ current_q }}" />{% endif %}
    {% if current_tag %}<input type="hidden" name="tag" value="{{ current_tag }}" />{% endif %}
    
    <div class="filter-row">
      <label>Include images with tags (comma-separated):</label>
      <input name="include_tags" value="{{ include_tags or '' }}" placeholder="e.g. SFW, Home, Ready for upscale" list="taglist" />
    </div>
    
    <div class="filter-row">
      <label>Exclude images with tags (comma-separated):</label>
      <input name="exclude_tags" value="{{ exclude_tags or '' }}" placeholder="e.g. NSFW, Needs inpainting" list="taglist" />
    </div>
    
    <button type="submit">Update Preview</button>
    <a href="/images?{{ request.query_string.decode() }}" class="button-link">Back to Images</a>
  </form>
</div>

<div class="export-form">
  <form method="post" action="/export/execute" class="export-section">
    <h3>Export Options</h3>
    
    <!-- Pass all current filter parameters -->
    {% if current_q %}<input type="hidden" name="q" value="{{ current_q }}" />{% endif %}
    {% if current_tag %}<input type="hidden" name="tag" value="{{ current_tag }}" />{% endif %}
    {% if include_tags %}<input type="hidden" name="include_tags" value="{{ include_tags }}" />{% endif %}
    {% if exclude_tags %}<input type="hidden" name="exclude_tags" value="{{ exclude_tags }}" />{% endif %}
    
    <div class="export-row">
      <label>Destination Folder:</label>
      <div class="path-input">
        <input type="text" name="destination" id="destinationPath" required placeholder="/path/to/export/folder" />
        <button type="button" id="browseFolder">Browse...</button>
      </div>
      <p class="help-text">Choose where to copy the filtered images</p>
    </div>
    
    <div class="export-row">
      <label class="inline">
        <input type="checkbox" name="preserve_structure" value="1" checked>
        Preserve folder structure
      </label>
      <p class="help-text">Keep the original folder organization, or copy all images to a flat structure</p>
    </div>
    
    <button type="submit" class="export-button" onclick="return confirm('Export {{ total_count }} images? This will copy them to the selected folder.')">
      Export {{ total_count }} Images
    </button>
  </form>
</div>

<div class="image-preview">
  <h3>Preview (first 20 images)</h3>
  <div class="preview-grid">
    {% for img in images[:20] %}
    <article class="preview-card">
      <div class="preview-image-container">
        <img loading="lazy" src="/thumb/{{ img.id }}?w=200" alt="{{ img.filename }}" />
      </div>
      <div class="preview-name">{{ img.filename }}</div>
    </article>
    {% endfor %}
  </div>
  {% if total_count > 20 %}
    <p class="muted">... and {{ total_count - 20 }} more images</p>
  {% endif %}
</div>

<datalist id="taglist">
  {% for t in all_tags %}<option value="{{ t.name }}">{% endfor %}
</datalist>

{% if msg %}
  <div class="flash success">{{ msg.replace('+', ' ') }}</div>
{% endif %}

{% if error %}
  <div class="flash error">{{ error.replace('+', ' ') }}</div>
{% endif %}

<script>
document.addEventListener('DOMContentLoaded', function() {
    const browseButton = document.getElementById('browseFolder');
    const destinationInput = document.getElementById('destinationPath');
    
    // Check if the modern File System Access API is supported
    const supportsFileSystemAPI = 'showDirectoryPicker' in window;
    
    browseButton.addEventListener('click', async function() {
        if (supportsFileSystemAPI) {
            try {
                // Use modern File System Access API
                const directoryHandle = await window.showDirectoryPicker({
                    mode: 'readwrite'
                });
                
                // Get the directory path (note: this may not work in all browsers)
                if (directoryHandle.name) {
                    destinationInput.value = directoryHandle.name;
                } else {
                    destinationInput.value = 'Selected folder: ' + (directoryHandle.name || 'Unknown');
                }
                
                // Store the directory handle for later use
                destinationInput.dataset.directoryHandle = JSON.stringify(directoryHandle);
                
            } catch (err) {
                if (err.name !== 'AbortError') {
                    console.error('Directory selection failed:', err);
                    fallbackPrompt();
                }
            }
        } else {
            fallbackPrompt();
        }
    });
    
    function fallbackPrompt() {
        // Fallback to simple prompt
        const path = prompt('Enter the full path to the destination folder:', destinationInput.value || '/home/nadertate/Desktop/Exported Images');
        if (path) {
            destinationInput.value = path;
        }
    }
    
    // Auto-suggest some common export paths
    const suggestions = [
        '/home/nadertate/Desktop/Exported Images',
        '/home/nadertate/Downloads/Exported Images',
        '/tmp/exported_images'
    ];
    
    destinationInput.addEventListener('focus', function() {
        if (!this.value) {
            this.placeholder = suggestions[0];
        }
    });
    
    // Improve button text based on API support
    if (supportsFileSystemAPI) {
        browseButton.textContent = '📁 Browse...';
        browseButton.title = 'Open folder picker';
    } else {
        browseButton.textContent = '📝 Enter Path...';
        browseButton.title = 'Enter folder path manually';
    }
});
</script>
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
.detail{display:grid;grid-template-columns:minmax(300px,2fr) minmax(280px,1fr);gap:20px;align-items:start}@media (max-width:768px){.detail{grid-template-columns:1fr;gap:16px}}.detail img{max-width:100%;height:auto;max-height:80vh;object-fit:contain;border-radius:8px;background:#0a0d12;box-shadow:0 4px 12px rgba(0,0,0,0.3)}
.kv{display:flex;justify-content:space-between;gap:12px}
.pager{display:flex;justify-content:center;align-items:center;gap:10px;margin:16px}
.flash{background:#13221d;border:1px solid #214d39;padding:10px;border-radius:10px}
.controls{display:flex;flex-direction:column;gap:16px;margin-bottom:20px}
.filters{display:flex;align-items:center;gap:10px;margin-bottom:0}
.bulk-controls{background:var(--card);border:1px solid #1f2430;border-radius:8px;padding:12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.selected-count{color:var(--brand);font-weight:600}
.bulk-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.card-header{position:absolute;top:8px;left:8px;z-index:5;background:rgba(0,0,0,0.7);border-radius:4px;padding:2px}
.image-select{margin:0}
.card{position:relative}
.card.selected{border-color:var(--brand);box-shadow:0 0 0 2px var(--brand)}
@media (max-width:768px){.filters{flex-direction:column;align-items:stretch}.bulk-actions{flex-direction:column;align-items:stretch}}
.scan-options,.danger-zone{margin:20px 0;padding:16px;border-radius:8px}.scan-options{background:#0e1218;border:1px solid #1c1f26}.danger-zone{background:#1a0f0f;border:1px solid #3d1a1a}.help-text{font-size:13px;color:var(--muted);margin:6px 0 16px 0;line-height:1.4}.danger-text{color:#ff9999}
.export-link{background:var(--brand);color:white;padding:6px 12px;border-radius:6px;text-decoration:none;font-size:14px}.export-link:hover{background:#5a8bd4}
.export-summary{background:var(--card);border:1px solid #1f2430;border-radius:8px;padding:16px;margin-bottom:20px}.summary-stats{display:flex;gap:24px;margin-bottom:16px}.stat{text-align:center}.stat .count{display:block;font-size:24px;font-weight:700;color:var(--brand)}.stat .label{font-size:12px;color:var(--muted)}
.current-filters h3{margin:0 0 8px 0}.filter-chip{background:var(--chip);padding:4px 8px;border-radius:12px;font-size:12px;margin-right:8px;position:relative;display:inline-flex;align-items:center;gap:6px}.remove-filter{color:var(--muted);text-decoration:none;font-weight:bold;padding:0 4px;border-radius:50%;transition:all 0.2s}.remove-filter:hover{background:rgba(255,255,255,0.1);color:var(--fg)}
.export-options,.export-form{background:var(--card);border:1px solid #1f2430;border-radius:8px;padding:16px;margin-bottom:20px}
.filter-section,.export-section{display:flex;flex-direction:column;gap:12px}.filter-row,.export-row{display:flex;flex-direction:column;gap:6px}
.path-input{display:flex;gap:8px}.path-input input{flex:1}.path-input button{flex-shrink:0;width:auto}
.export-button{background:#10b981;color:white;border:none;padding:12px 24px;border-radius:8px;font-weight:600;cursor:pointer}.export-button:hover{background:#059669}
.preview-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.preview-card{background:var(--card);border:1px solid #1f2430;border-radius:8px;overflow:hidden;text-align:center}.preview-image-container{width:100%;height:180px;display:flex;align-items:center;justify-content:center;background:#090a0d}.preview-image-container img{max-width:100%;max-height:100%;object-fit:contain}.preview-name{padding:8px;font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.button-link{display:inline-block;background:#374151;color:white;padding:6px 12px;border-radius:6px;text-decoration:none;font-size:14px}.button-link:hover{background:#4b5563}
.flash.success{background:#0f2e1e;border-color:#10b981;color:#34d399}.flash.error{background:#2d1b1b;border-color:#ef4444;color:#f87171}
"""


def ensure_assets() -> None:
    """Create templates/static on first run so this file is standalone."""
    APP_DIR = Path(__file__).resolve().parent
    TEMPLATES_DIR = APP_DIR / "templates"
    STATIC_DIR = APP_DIR / "static"

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    files = {
        TEMPLATES_DIR / "base.html": BASE_HTML,
        TEMPLATES_DIR / "images.html": IMAGES_HTML,
        TEMPLATES_DIR / "image.html": IMAGE_HTML,
        TEMPLATES_DIR / "tags.html": TAGS_HTML,
        TEMPLATES_DIR / "settings.html": SETTINGS_HTML,
        TEMPLATES_DIR / "export.html": EXPORT_HTML,
        STATIC_DIR / "app.css": APP_CSS,
    }
    for p, content in files.items():
        if not p.exists():
            p.write_text(content, encoding="utf-8")
