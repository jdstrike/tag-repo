import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, redirect, render_template_string, request, send_file

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "reports.db"
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://tag-repo.com").rstrip("/")
CONTEXT_BASE_URL = os.environ.get("CONTEXT_BASE_URL", "https://context.tag-repo.com").rstrip("/")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,32}$")

DATA_DIR.mkdir(parents=True, exist_ok=True)
FILES_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


with db() as _conn:
    _conn.execute(
        "CREATE TABLE IF NOT EXISTS reports ("
        "id TEXT PRIMARY KEY, "
        "filename TEXT NOT NULL, "
        "uploaded_at TEXT NOT NULL, "
        "uploader_email TEXT)"
    )


PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<meta name="description" content="Upload a TAG-styled HTML report and get a permanent URL you can share.">
<link rel="icon" type="image/png" sizes="32x32" href="{{ ctx }}/favicon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="{{ ctx }}/favicon-16.png">
<link rel="icon" type="image/x-icon" href="{{ ctx }}/favicon.ico">
<link rel="apple-touch-icon" href="{{ ctx }}/apple-touch-icon.png">
<link rel="stylesheet" href="{{ ctx }}/assets/css/site.css">
<style>
  body { min-height: 100vh; }
  .upload-shell { padding: 56px 24px 96px; }
  .upload-card { max-width: 640px; margin: 0 auto; }
  .upload-card form { display: flex; flex-direction: column; gap: 18px; margin-top: 24px; }
  .upload-card input[type=file] {
    padding: 14px 16px;
    background: rgba(255,255,255,.04);
    border: 1px solid var(--tag-hairline-strong, rgba(255,255,255,.18));
    border-radius: 10px;
    color: rgba(255,255,255,.92);
    font: inherit;
    cursor: pointer;
  }
  .upload-card input[type=file]:focus-visible {
    outline: 2px solid #2DBFB8;
    outline-offset: 2px;
  }
  .upload-card .meta { color: rgba(255,255,255,.62); font-size: .9rem; margin-top: 12px; }
  .upload-card .meta code {
    background: rgba(255,255,255,.06);
    padding: .15em .45em;
    border-radius: 4px;
    font-family: "SF Mono", Consolas, monospace;
    font-size: .85em;
  }
  .upload-card .share-url {
    display: block;
    background: rgba(255,255,255,.04);
    border: 1px solid var(--tag-hairline, rgba(255,255,255,.12));
    border-radius: 10px;
    padding: 18px 20px;
    margin-top: 8px;
    color: #fff;
    text-decoration: none;
    word-break: break-all;
    font-family: "SF Mono", Consolas, monospace;
    font-size: .95rem;
    transition: border-color .15s ease;
  }
  .upload-card .share-url:hover { border-color: #2DBFB8; }
  .upload-actions { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 32px; }
</style>
</head>
<body class="tag-page">

<nav class="tag-nav" aria-label="Primary">
  <div class="tag-nav-inner">
    <a href="{{ ctx }}/" class="tag-nav-logo" aria-label="The Adecco Group">
      <img src="{{ ctx }}/assets/logos/tag-family-lockup-colour-neg.svg" alt="The Adecco Group">
    </a>
    <ul class="tag-nav-links">
      <li><a href="{{ ctx }}/index.html#strategy">Brand</a></li>
      <li><a href="{{ ctx }}/index.html#voice">Voice</a></li>
      <li><a href="{{ ctx }}/business-context/index.html">Business</a></li>
      <li><a href="{{ ctx }}/design-system/components/index.html">Components</a></li>
      <li><a href="{{ ctx }}/design-system/documents/index.html">Documents</a></li>
    </ul>
    <a href="/reports/upload" class="tag-nav-cta">Upload report</a>
  </div>
</nav>
<div class="tag-stripe"></div>
"""

PAGE_FOOT = """</body></html>"""


UPLOAD_FORM = PAGE_HEAD + """
<section class="tag-zone upload-shell">
  <div class="upload-card">
    <span class="tag-eyebrow">Reports</span>
    <h1 class="tag-h2">Upload a <span class="tag-grad-text">TAG report.</span></h1>
    <p class="lede">Drop in a single self-contained HTML file. We'll host it at a permanent URL you can share.</p>
    {% if uploader %}<p class="meta">Signed in as <code>{{ uploader }}</code></p>{% endif %}
    <form method="post" enctype="multipart/form-data">
      <input type="file" name="file" accept=".html,.htm,text/html" required>
      <button class="tag-btn tag-btn-primary" type="submit">Upload</button>
    </form>
    <p class="meta">Single self-contained HTML file. Max {{ max_mb }} MB.</p>
  </div>
</section>
""" + PAGE_FOOT


UPLOAD_DONE = PAGE_HEAD + """
<section class="tag-zone upload-shell">
  <div class="upload-card">
    <span class="tag-eyebrow">Reports</span>
    <h1 class="tag-h2">Uploaded. <span class="tag-grad-text">Share the URL.</span></h1>
    <p class="lede">The report is live at the URL below. It'll stay there as long as you keep it.</p>
    <a class="share-url" href="{{ url }}">{{ url }}</a>
    <div class="upload-actions">
      <a class="tag-btn tag-btn-primary" href="{{ url }}">Open the report</a>
      <a class="tag-btn tag-btn-secondary" href="/reports/upload">Upload another</a>
    </div>
  </div>
</section>
""" + PAGE_FOOT


@app.route("/")
def root():
    return redirect("/reports/upload", code=302)


@app.route("/healthz")
def healthz():
    return {"ok": True}


@app.route("/favicon.ico")
def favicon():
    return redirect(f"{CONTEXT_BASE_URL}/favicon.ico", code=302)


@app.route("/reports/upload", methods=["GET", "POST"])
def upload():
    uploader = request.headers.get("Cf-Access-Authenticated-User-Email")
    max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    if request.method == "GET":
        return render_template_string(
            UPLOAD_FORM,
            title="Upload report - the Adecco Group",
            ctx=CONTEXT_BASE_URL,
            uploader=uploader,
            max_mb=max_mb,
        )
    f = request.files.get("file")
    if not f or not f.filename:
        abort(400, "no file")
    name = f.filename.lower()
    if not (name.endswith(".html") or name.endswith(".htm")):
        abort(400, "only .html files allowed")
    rid = secrets.token_urlsafe(16)
    out = FILES_DIR / f"{rid}.html"
    f.save(out)
    with db() as conn:
        conn.execute(
            "INSERT INTO reports (id, filename, uploaded_at, uploader_email) VALUES (?, ?, ?, ?)",
            (rid, f.filename, datetime.now(timezone.utc).isoformat(), uploader),
        )
    return render_template_string(
        UPLOAD_DONE,
        title="Uploaded - the Adecco Group",
        ctx=CONTEXT_BASE_URL,
        url=f"{PUBLIC_BASE_URL}/reports/{rid}",
    )


@app.route("/reports/<rid>")
def serve(rid):
    if not ID_RE.match(rid):
        abort(404)
    path = FILES_DIR / f"{rid}.html"
    if not path.is_file():
        abort(404)
    resp = send_file(path, mimetype="text/html; charset=utf-8")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp
