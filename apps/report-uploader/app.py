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


UPLOAD_FORM = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>Upload report</title>
<style>
  body{font-family:system-ui,sans-serif;max-width:560px;margin:5em auto;padding:0 1em;color:#222}
  form{display:flex;flex-direction:column;gap:1em;margin-top:1em}
  button{padding:.6em 1em;font-size:1em;cursor:pointer}
  .uploader{color:#666;font-size:.9em}
</style>
</head><body>
<h1>Upload report</h1>
{% if uploader %}<p class="uploader">Signed in as <code>{{ uploader }}</code></p>{% endif %}
<form method="post" enctype="multipart/form-data">
  <input type="file" name="file" accept=".html,.htm,text/html" required>
  <button type="submit">Upload</button>
</form>
<p class="uploader">Single self-contained HTML file. Max {{ max_mb }} MB.</p>
</body></html>"""

UPLOAD_DONE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>Uploaded</title>
<style>
  body{font-family:system-ui,sans-serif;max-width:560px;margin:5em auto;padding:0 1em;color:#222}
  code{background:#f4f4f4;padding:.2em .4em;border-radius:.2em;word-break:break-all}
</style>
</head><body>
<h1>Uploaded</h1>
<p>Share this URL: <a href="{{ url }}"><code>{{ url }}</code></a></p>
<p><a href="/reports/upload">Upload another</a></p>
</body></html>"""


@app.route("/")
def root():
    return redirect("/reports/upload", code=302)


@app.route("/healthz")
def healthz():
    return {"ok": True}


@app.route("/reports/upload", methods=["GET", "POST"])
def upload():
    uploader = request.headers.get("Cf-Access-Authenticated-User-Email")
    max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    if request.method == "GET":
        return render_template_string(UPLOAD_FORM, uploader=uploader, max_mb=max_mb)
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
    return render_template_string(UPLOAD_DONE, url=f"{PUBLIC_BASE_URL}/reports/{rid}")


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
