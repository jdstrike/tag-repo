"""
TAG UTM Builder + Short-Link Manager - v2
Campaign-level approval, source/medium dependencies, copy logging.
"""
import os, re, sqlite3, re, secrets, string, smtplib, json
from datetime import datetime
from email.mime.text import MIMEText
from urllib.parse import urlencode
from functools import wraps

from flask import (Flask, request, jsonify, render_template, redirect,
                   session, url_for, abort, Response, g)
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("UTM_DB_PATH", os.path.join(APP_DIR, "data", "utm.db"))
SECRET_KEY = os.environ.get("UTM_SECRET_KEY", secrets.token_hex(32))
ADMIN_EMAIL = os.environ.get("UTM_ADMIN_EMAIL", "johannes.schatt@googlemail.com")
ADMIN_EMAILS = [e.strip() for e in os.environ.get("UTM_ADMIN_EMAILS","").split(",") if e.strip()] or [ADMIN_EMAIL]
ADMIN_USER = os.environ.get("UTM_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("UTM_ADMIN_PASSWORD", "TagUtm2026!")
PUBLIC_BASE_URL = os.environ.get("UTM_PUBLIC_BASE_URL", "")
SHORT_CODE_LEN = 6

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", ADMIN_EMAIL)

CAMPAIGN_RE = re.compile(r"^[a-z0-9_-]{2,80}$")

def normalize_email(raw):
    """Lowercase and trim. Returns empty string if None."""
    return (str(raw).strip().lower() if raw else "")

def normalize_destination(raw):
    """Lowercase scheme + hostname only; leave path/query case-sensitive. Adds https:// if missing."""
    if not raw: return ""
    s = str(raw).strip()
    if not s.lower().startswith(("http://","https://")):
        s = "https://" + s
    try:
        from urllib.parse import urlsplit, urlunsplit
        p = urlsplit(s)
        netloc = p.netloc.lower()
        return urlunsplit((p.scheme.lower(), netloc, p.path, p.query, p.fragment))
    except Exception:
        return s

def normalize_campaign_name(raw):
    """Lowercase, strip whitespace, allow only a-z 0-9 _ -. Returns (name, error_msg_or_None)."""
    if raw is None: return None, "Campaign name required."
    n = str(raw).strip().lower()
    if not n: return None, "Campaign name required."
    if not CAMPAIGN_RE.match(n):
        return None, ("Campaign name must be lowercase letters, numbers, underscore or hyphen only. "
                      "2 to 80 characters. No spaces, no special characters. Example: pride_2026 or q4-launch.")
    return n, None

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

app = Flask(__name__)
app.secret_key = SECRET_KEY
from flask import after_this_request
@app.after_request
def add_cors_headers(resp):
    """Allow Chrome extension origin to call /api/* endpoints with cookies."""
    origin = request.headers.get("Origin","")
    if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp



# -------------------- DB --------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db is not None: db.close()

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        short_code TEXT UNIQUE NOT NULL,
        long_url TEXT NOT NULL,
        destination TEXT NOT NULL,
        utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
        utm_term TEXT, utm_content TEXT, utm_id TEXT,
        gbu TEXT, brand_tag TEXT, country TEXT, country_code TEXT,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'approved',
        requested_by_email TEXT,
        created_at TEXT NOT NULL,
        approved_at TEXT,
        clicks INTEGER NOT NULL DEFAULT 0,
        campaign_id INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_links_status ON links(status);
    CREATE INDEX IF NOT EXISTS idx_links_campaign ON links(utm_campaign);

    CREATE TABLE IF NOT EXISTS clicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        link_id INTEGER NOT NULL,
        ts TEXT NOT NULL, ip TEXT, ua TEXT, referer TEXT,
        FOREIGN KEY(link_id) REFERENCES links(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_clicks_link ON clicks(link_id);

    CREATE TABLE IF NOT EXISTS taxonomy (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL, value TEXT NOT NULL, code TEXT,
        sort_order INTEGER DEFAULT 0,
        UNIQUE(kind, value)
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    );

    CREATE TABLE IF NOT EXISTS campaigns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        requested_by_email TEXT,
        notes TEXT,
        created_at TEXT NOT NULL,
        approved_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);

    CREATE TABLE IF NOT EXISTS source_medium_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL, medium TEXT NOT NULL,
        UNIQUE(source, medium)
    );
    CREATE INDEX IF NOT EXISTS idx_smr_source ON source_medium_rules(source);

    CREATE TABLE IF NOT EXISTS link_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        link_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        email TEXT,
        ts TEXT NOT NULL,
        ip TEXT, ua TEXT,
        FOREIGN KEY(link_id) REFERENCES links(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_link_events_link ON link_events(link_id);
    CREATE INDEX IF NOT EXISTS idx_link_events_type ON link_events(event_type);

    CREATE TABLE IF NOT EXISTS suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        related_value TEXT,
        email TEXT,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        resolved_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status);

    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        message TEXT NOT NULL,
        email TEXT,
        page TEXT,
        user_agent TEXT,
        ip TEXT,
        username TEXT,
        status TEXT NOT NULL DEFAULT 'new',
        created_at TEXT NOT NULL,
        resolved_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status);

    CREATE TABLE IF NOT EXISTS combination_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        medium TEXT NOT NULL,
        dimension_kind TEXT NOT NULL,
        allowed_values_json TEXT,
        required INTEGER NOT NULL DEFAULT 0,
        UNIQUE(source, medium, dimension_kind)
    );
    CREATE INDEX IF NOT EXISTS idx_combrules_sm ON combination_rules(source, medium);

    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        preset_json TEXT NOT NULL,
        country_scope TEXT DEFAULT 'global',
        created_by TEXT,
        created_at TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS ga4_properties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gbu_name TEXT,
        domain TEXT NOT NULL,
        property_id TEXT UNIQUE NOT NULL,
        measurement_id TEXT,
        notes TEXT,
        is_master INTEGER NOT NULL DEFAULT 1,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT 'all',  -- 'all', 'global', 'de', 'ch', 'it' etc
        email TEXT,
        full_name TEXT,
        created_at TEXT NOT NULL,
        created_by TEXT,
        last_login_at TEXT
    );

    CREATE TABLE IF NOT EXISTS country_config (
        country_code TEXT PRIMARY KEY,
        country_name TEXT NOT NULL,
        config_json TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT
    );
    """)
    con.commit()

    # Seed taxonomy
    cur = con.execute("SELECT COUNT(*) FROM taxonomy")
    if cur.fetchone()[0] == 0:
        sources = ["bing","blog","facebook","google","instagram","linkedin","mailchimp",
                   "pinterest","website","x","yelp","youtube","tiktok","newsletter"]
        mediums = ["advocacy","automation","banner","boosted","button","cpc","cpm","email",
                   "instream","organic","post","pr","profile","promoted","review","slider",
                   "sales","social","display","paid"]
        stages = ["awareness","consideration","conversion","retention","register","download"]
        gbus = [("The Adecco Group","tag"),("Adecco","ade"),("Akkodis","akk"),
                ("LHH","lhh"),("Pontoon","pon")]
        countries = [
            ("Global","hq"),("Australia","au"),("Austria","at"),("Belgium","be"),
            ("Bulgaria","bg"),("Canada","ca"),("Finland","fi"),("France","fr"),
            ("Germany","de"),("Greece","gr"),("Hungary","hu"),("India","in"),
            ("Israel","il"),("Italy","it"),("Japan","jp"),("Liechtenstein","li"),
            ("Luxembourg","lu"),("Mexico","mx"),("Netherlands","nl"),("Norway","no"),
            ("Saudi Arabia","sa"),("Singapore","sg"),("Slovakia","sk"),("Slovenia","si"),
            ("Spain","es"),("Sweden","se"),("Switzerland","ch"),("Thailand","th"),
            ("Turkey","tr"),("Ukraine","ua"),("United Arab Emirates","ae"),
            ("United Kingdom","gb"),("United States","us"),("Vietnam","vn"),
        ]
        for i,v in enumerate(sources): con.execute("INSERT INTO taxonomy(kind,value,sort_order) VALUES('source',?,?)",(v,i))
        for i,v in enumerate(mediums): con.execute("INSERT INTO taxonomy(kind,value,sort_order) VALUES('medium',?,?)",(v,i))
        for i,v in enumerate(stages): con.execute("INSERT INTO taxonomy(kind,value,sort_order) VALUES('stage',?,?)",(v,i))
        for i,(n,c) in enumerate(gbus): con.execute("INSERT INTO taxonomy(kind,value,code,sort_order) VALUES('gbu',?,?,?)",(n,c,i))
        for i,(n,c) in enumerate(countries): con.execute("INSERT INTO taxonomy(kind,value,code,sort_order) VALUES('country',?,?,?)",(n,c,i))
        # Add-on dimensions for utm_id composition (Agency, Targeting Type, Segmentation, Asset Type)
        agencies = ["internal","wpp","publicis","omd","mindshare","groupm","havas","dentsu","localagency"]
        targeting_types = ["prospecting","retargeting","lookalike","interest","contextual","keyword","placement","custom"]
        segmentations = ["c-suite","hr-leaders","talent-acquisition","candidates","alumni","clients-existing","clients-prospect","employees","students","general"]
        asset_types = ["image-static","image-carousel","video-short","video-long","native","text-ad","story","whitepaper","webinar","case-study","infographic","podcast"]
        for i,v in enumerate(agencies): con.execute("INSERT INTO taxonomy(kind,value,sort_order) VALUES('agency',?,?)",(v,i))
        for i,v in enumerate(targeting_types): con.execute("INSERT INTO taxonomy(kind,value,sort_order) VALUES('targeting_type',?,?)",(v,i))
        for i,v in enumerate(segmentations): con.execute("INSERT INTO taxonomy(kind,value,sort_order) VALUES('segmentation',?,?)",(v,i))
        for i,v in enumerate(asset_types): con.execute("INSERT INTO taxonomy(kind,value,sort_order) VALUES('asset_type',?,?)",(v,i))
        con.commit()

    # Seed source/medium rules (GA4-aligned)
    cur = con.execute("SELECT COUNT(*) FROM source_medium_rules")
    if cur.fetchone()[0] == 0:
        rules = [
            ("bing", ["cpc","organic","paid"]),
            ("google", ["cpc","organic","cpm","paid","display"]),
            ("facebook", ["organic","cpc","social","paid","boosted","post"]),
            ("instagram", ["organic","social","cpc","boosted","post"]),
            ("linkedin", ["organic","social","cpc","sponsored","post","sales"]),
            ("youtube", ["organic","instream","social","cpc","video"]),
            ("x", ["organic","social","cpc","promoted"]),
            ("pinterest", ["organic","cpc","social"]),
            ("tiktok", ["organic","social","cpc","paid"]),
            ("mailchimp", ["email","automation"]),
            ("newsletter", ["email"]),
            ("blog", ["organic","referral"]),
            ("website", ["banner","button","slider","referral"]),
            ("yelp", ["organic","review","profile"]),
        ]
        # Some sources may have new mediums (sponsored, video, referral) - add to taxonomy
        for src, meds in rules:
            for m in meds:
                con.execute("INSERT OR IGNORE INTO taxonomy(kind,value) VALUES('medium',?)", (m,))
                con.execute("INSERT OR IGNORE INTO source_medium_rules(source,medium) VALUES(?,?)", (src, m))
        con.commit()

    # seed templates: a few common patterns
    cur = con.execute("SELECT COUNT(*) FROM templates")
    if cur.fetchone()[0] == 0:
        import json as _j
        templates_seed = [
            ("LinkedIn Sponsored Content", "Paid LinkedIn ads with brand awareness focus", {"utm_source":"linkedin","utm_medium":"sponsored","utm_term":"awareness","agency":"internal","targeting_type":"prospecting","asset_type":"image-static"}, "global"),
            ("LinkedIn Organic Post", "Organic posts from brand handles", {"utm_source":"linkedin","utm_medium":"organic","utm_term":"awareness","agency":"internal","asset_type":"image-static"}, "global"),
            ("Facebook Paid Carousel", "Facebook paid ads with carousel creative", {"utm_source":"facebook","utm_medium":"paid","utm_term":"consideration","agency":"internal","targeting_type":"prospecting","asset_type":"image-carousel"}, "global"),
            ("Email Newsletter Link", "Monthly newsletter CTA", {"utm_source":"newsletter","utm_medium":"email","utm_term":"engagement","agency":"internal","asset_type":"text-ad"}, "global"),
            ("Google Search CPC", "Branded search campaigns", {"utm_source":"google","utm_medium":"cpc","utm_term":"conversion","targeting_type":"keyword","asset_type":"text-ad"}, "global"),
            ("YouTube Pre-roll Video", "Paid video ads", {"utm_source":"youtube","utm_medium":"instream","utm_term":"awareness","asset_type":"video-short"}, "global"),
        ]
        for name, desc, preset, scope in templates_seed:
            try:
                con.execute("INSERT INTO templates(name,description,preset_json,country_scope,created_by,created_at) VALUES(?,?,?,?,?,?)",
                            (name, desc, _j.dumps(preset), scope, "system", datetime.utcnow().isoformat(timespec="seconds")+"Z"))
            except sqlite3.IntegrityError: pass
        con.commit()

    # seed ga4_properties: the 5 GBU master properties
    cur = con.execute("SELECT COUNT(*) FROM ga4_properties")
    if cur.fetchone()[0] == 0:
        ga4_seed = [
            ("The Adecco Group", "adeccogroup.com",    "345642469", "G-84G7D6BPQT", "MASTER"),
            ("Pontoon",          "pontoonsolutions.com","433293932", None,           "MASTER"),
            ("Adecco",           "adecco.com",          "373621285", "G-ZY6KEMK0NQ", "MASTER"),
            ("LHH",              "lhh.com",             "307349327", None,           "MASTER"),
            ("Akkodis",          "akkodis.com",         "351905607", None,           "MASTER"),
        ]
        for n,d,pid,mid,nt in ga4_seed:
            try:
                con.execute("INSERT INTO ga4_properties(gbu_name,domain,property_id,measurement_id,notes,is_master,created_at) VALUES(?,?,?,?,?,1,?)",
                            (n, d, pid, mid, nt, datetime.utcnow().isoformat(timespec="seconds")+"Z"))
            except sqlite3.IntegrityError: pass
        con.commit()

    # seed combination_rules: which advanced dimensions apply per source+medium pair
    cur = con.execute("SELECT COUNT(*) FROM combination_rules")
    if cur.fetchone()[0] == 0:
        comb_seed = [
            # (source, medium, dimension, allowed_values_json, required)
            ("linkedin", "sponsored",  "asset_type",     '["text-ad","image-static","image-carousel","video-short","native"]', 1),
            ("linkedin", "sponsored",  "targeting_type", '["prospecting","retargeting","lookalike","interest","custom"]',     1),
            ("linkedin", "sponsored",  "agency",         None,                                                                  0),
            ("linkedin", "sponsored",  "segmentation",   '["c-suite","hr-leaders","talent-acquisition","clients-prospect","clients-existing"]', 0),
            ("linkedin", "cpc",        "asset_type",     '["text-ad","image-static","image-carousel","native"]',              1),
            ("linkedin", "cpc",        "targeting_type", '["prospecting","retargeting","lookalike","keyword"]',               1),
            ("linkedin", "organic",    "asset_type",     '["text-ad","image-static","image-carousel","video-short","story"]',  0),
            ("linkedin", "organic",    "targeting_type", '[]',                                                                0),
            ("linkedin", "organic",    "agency",         '["internal"]',                                                      0),
            ("linkedin", "post",       "asset_type",     '["text-ad","image-static","image-carousel"]',                       0),
            ("linkedin", "social",     "asset_type",     '["text-ad","image-static","image-carousel","video-short","story"]',  0),
            ("facebook", "paid",       "asset_type",     '["image-static","image-carousel","video-short","story","native"]',   1),
            ("facebook", "paid",       "targeting_type", '["prospecting","retargeting","lookalike","interest","contextual","custom"]',1),
            ("facebook", "boosted",    "asset_type",     '["image-static","image-carousel","video-short","story"]',           1),
            ("facebook", "boosted",    "targeting_type", '["interest","lookalike","custom"]',                                 1),
            ("facebook", "social",     "asset_type",     '["image-static","image-carousel","video-short","story"]',           0),
            ("facebook", "organic",    "asset_type",     '["image-static","image-carousel","video-short","story"]',           0),
            ("facebook", "organic",    "agency",         '["internal"]',                                                      0),
            ("facebook", "post",       "asset_type",     '["image-static","image-carousel","video-short","story"]',           0),
            ("instagram","boosted",    "asset_type",     '["image-static","image-carousel","video-short","story"]',           1),
            ("instagram","social",     "asset_type",     '["image-static","image-carousel","video-short","story"]',           0),
            ("instagram","organic",    "asset_type",     '["image-static","image-carousel","video-short","story"]',           0),
            ("instagram","post",       "asset_type",     '["image-static","image-carousel","story"]',                         0),
            ("instagram","cpc",        "asset_type",     '["image-static","image-carousel","video-short","story"]',           1),
            ("tiktok",   "paid",       "asset_type",     '["video-short","story","native"]',                                  1),
            ("tiktok",   "social",     "asset_type",     '["video-short","story"]',                                           0),
            ("tiktok",   "cpc",        "asset_type",     '["video-short","story"]',                                           1),
            ("tiktok",   "organic",    "asset_type",     '["video-short","story"]',                                           0),
            ("youtube",  "instream",   "asset_type",     '["video-short","video-long"]',                                      1),
            ("youtube",  "video",      "asset_type",     '["video-short","video-long"]',                                      1),
            ("youtube",  "organic",    "asset_type",     '["video-short","video-long"]',                                      0),
            ("youtube",  "cpc",        "asset_type",     '["video-short","video-long","native"]',                             1),
            ("google",   "cpc",        "targeting_type", '["keyword","placement","contextual","retargeting"]',                1),
            ("google",   "cpc",        "asset_type",     '["text-ad","image-static"]',                                        1),
            ("google",   "display",    "asset_type",     '["image-static","image-carousel","native","video-short"]',           1),
            ("google",   "display",    "targeting_type", '["placement","contextual","interest","retargeting","lookalike"]',    1),
            ("google",   "cpm",        "asset_type",     '["image-static","image-carousel","native"]',                         1),
            ("google",   "paid",       "asset_type",     '["text-ad","image-static","image-carousel"]',                        1),
            ("google",   "organic",    "asset_type",     '[]',                                                                 0),
            ("google",   "organic",    "agency",         '[]',                                                                 0),
            ("google",   "organic",    "targeting_type", '[]',                                                                 0),
            ("bing",     "cpc",        "targeting_type", '["keyword","placement","retargeting"]',                              1),
            ("bing",     "cpc",        "asset_type",     '["text-ad"]',                                                        1),
            ("bing",     "paid",       "asset_type",     '["text-ad"]',                                                        1),
            ("bing",     "organic",    "asset_type",     '[]',                                                                 0),
            ("bing",     "organic",    "agency",         '[]',                                                                 0),
            ("mailchimp","email",      "asset_type",     '["text-ad"]',                                                        0),
            ("mailchimp","email",      "agency",         '["internal"]',                                                       0),
            ("mailchimp","email",      "targeting_type", '[]',                                                                 0),
            ("mailchimp","automation", "asset_type",     '["text-ad"]',                                                        0),
            ("mailchimp","automation", "agency",         '["internal"]',                                                       0),
            ("mailchimp","automation", "targeting_type", '[]',                                                                 0),
            ("newsletter","email",     "asset_type",     '["text-ad"]',                                                        0),
            ("newsletter","email",     "agency",         '["internal"]',                                                       0),
            ("newsletter","email",     "targeting_type", '[]',                                                                 0),
            ("x",        "promoted",   "asset_type",     '["text-ad","image-static","image-carousel","video-short"]',          1),
            ("x",        "cpc",        "asset_type",     '["text-ad","image-static","image-carousel"]',                        1),
            ("x",        "social",     "asset_type",     '["text-ad","image-static","image-carousel","video-short"]',          0),
            ("x",        "organic",    "asset_type",     '["text-ad","image-static","image-carousel","video-short"]',          0),
            ("pinterest","cpc",        "asset_type",     '["image-static","image-carousel","video-short"]',                    1),
            ("pinterest","social",     "asset_type",     '["image-static","image-carousel","video-short"]',                    0),
            ("pinterest","organic",    "asset_type",     '["image-static","image-carousel"]',                                  0),
            ("blog",     "organic",    "asset_type",     '["text-ad","native","infographic","case-study","whitepaper"]',       0),
            ("blog",     "organic",    "agency",         '[]',                                                                 0),
            ("blog",     "referral",   "asset_type",     '["text-ad","native","infographic"]',                                 0),
            ("website",  "banner",     "asset_type",     '["image-static","image-carousel"]',                                  1),
            ("website",  "button",     "asset_type",     '["text-ad"]',                                                        0),
            ("website",  "slider",     "asset_type",     '["image-static","image-carousel"]',                                  0),
            ("website",  "referral",   "asset_type",     '[]',                                                                 0),
            ("yelp",     "organic",    "asset_type",     '[]',                                                                 0),
            ("yelp",     "review",     "asset_type",     '[]',                                                                 0),
            ("yelp",     "profile",    "asset_type",     '[]',                                                                 0),
        ]
        con.executemany("INSERT INTO combination_rules(source,medium,dimension_kind,allowed_values_json,required) VALUES(?,?,?,?,?)", comb_seed)
        con.commit()

    # seed admins: env admin becomes the bootstrap user
    cur = con.execute("SELECT COUNT(*) FROM admins")
    if cur.fetchone()[0] == 0:
      try:
        from werkzeug.security import generate_password_hash as _gph
        con.execute("INSERT INTO admins(username,password_hash,scope,email,full_name,created_at,created_by) VALUES(?,?,?,?,?,?,?)",
                    (ADMIN_USER, _gph(ADMIN_PASSWORD), "all", ADMIN_EMAIL, "Bootstrap Admin", datetime.utcnow().isoformat(timespec="seconds")+"Z", "system"))
        con.commit()
      except sqlite3.IntegrityError:
        pass  # another worker beat us to the seed
    # Seed admin password
    cur = con.execute("SELECT value FROM settings WHERE key='admin_password_hash'")
    if not cur.fetchone():
        con.execute("INSERT INTO settings(key,value) VALUES(?,?)",
                    ("admin_password_hash", generate_password_hash(ADMIN_PASSWORD)))
        con.commit()

    # Add campaign_id column if missing (migration for v1 -> v2)
    cols = [r[1] for r in con.execute("PRAGMA table_info(links)").fetchall()]
    if "campaign_id" not in cols:
        con.execute("ALTER TABLE links ADD COLUMN campaign_id INTEGER")
    # Migration: add role column to admins
    try:
        cols_admins = [r[1] for r in con.execute("PRAGMA table_info(admins)").fetchall()]
        if "role" not in cols_admins:
            con.execute("ALTER TABLE admins ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
            con.commit()
    except Exception as e:
        print(f"role migration skip: {e}", flush=True)
    # Migration: per-table country_scope
    for tbl in ("campaigns","source_medium_rules","combination_rules","taxonomy","suggestions"):
        try:
            existing = [r[1] for r in con.execute(f"PRAGMA table_info({tbl})").fetchall()]
            if "country_scope" not in existing:
                con.execute(f"ALTER TABLE {tbl} ADD COLUMN country_scope TEXT")
        except Exception as e:
            print(f"migration skip {tbl}: {e}", flush=True)
    con.commit()
    # Migration: campaigns calendar fields
    try:
        cols_camp = [r[1] for r in con.execute("PRAGMA table_info(campaigns)").fetchall()]
        if "start_date" not in cols_camp:
            con.execute("ALTER TABLE campaigns ADD COLUMN start_date TEXT")
            con.execute("ALTER TABLE campaigns ADD COLUMN end_date TEXT")
            con.execute("ALTER TABLE campaigns ADD COLUMN gbu TEXT")
            con.execute("ALTER TABLE campaigns ADD COLUMN owner_email TEXT")
            con.execute("ALTER TABLE campaigns ADD COLUMN description TEXT")
            con.commit()
    except Exception as e:
        print(f"campaign calendar migration skip: {e}", flush=True)
    if "utm_id_built" not in cols:
        con.execute("ALTER TABLE links ADD COLUMN utm_id_built TEXT")
        con.execute("ALTER TABLE links ADD COLUMN agency TEXT")
        con.execute("ALTER TABLE links ADD COLUMN targeting_type TEXT")
        con.execute("ALTER TABLE links ADD COLUMN segmentation TEXT")
        con.execute("ALTER TABLE links ADD COLUMN asset_type TEXT")
    con.commit()

    # Migration: Tealium routing matrix (single source of truth for UTM -> downstream routing)
    con.execute("""
    CREATE TABLE IF NOT EXISTS routing_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        utm_field TEXT NOT NULL,
        destination_system TEXT NOT NULL,
        destination_field TEXT NOT NULL,
        transform TEXT,
        status TEXT NOT NULL DEFAULT 'proposed',  -- live / proposed / planned / deprecated
        notes TEXT,
        sort_order INTEGER DEFAULT 0,
        updated_at TEXT,
        updated_by TEXT
    )""")
    con.commit()
    cur = con.execute("SELECT COUNT(*) FROM routing_rules")
    if cur.fetchone()[0] == 0:
        _now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
        routing_seed = [
            ("utm_source",   "GA4", "session_source / first_user_source", "passthrough (auto-collected by GA4 tag)", "live", "No Tealium work needed"),
            ("utm_medium",   "GA4", "session_medium",                     "passthrough (auto-collected)",            "live", None),
            ("utm_campaign", "GA4", "session_campaign_name",              "passthrough (auto-collected)",            "live", None),
            ("utm_term",     "GA4", "session_manual_term",                "passthrough (auto-collected)",            "live", None),
            ("utm_content",  "GA4", "session_manual_ad_content",          "passthrough (auto-collected)",            "live", None),
            ("utm_id",       "GA4", "session_campaign_id",                "passthrough (auto-collected)",            "live", None),
            ("utm_source",   "Tealium iQ", "utag_data.utm_source",     "pre-loader extension, passthrough", "proposed", "Snippet on /integrations"),
            ("utm_medium",   "Tealium iQ", "utag_data.utm_medium",     "pre-loader extension, passthrough", "proposed", None),
            ("utm_campaign", "Tealium iQ", "utag_data.utm_campaign",   "pre-loader extension, passthrough", "proposed", None),
            ("utm_id",       "Tealium iQ", "utag_data.utm_id",         "passthrough + split on '-'",        "proposed", "Also populates tag_* dimensions"),
            ("tag_agency",       "Tealium iQ", "utag_data.tag_agency",       "utm_id segment 1", "proposed", "Derived from utm_id"),
            ("tag_targeting",    "Tealium iQ", "utag_data.tag_targeting",    "utm_id segment 2", "proposed", "Derived from utm_id"),
            ("tag_segmentation", "Tealium iQ", "utag_data.tag_segmentation", "utm_id segment 3", "proposed", "Derived from utm_id"),
            ("tag_asset",        "Tealium iQ", "utag_data.tag_asset",        "utm_id segment 4+", "proposed", "Derived from utm_id; multi-part values"),
            ("utm_source",   "Salesforce", "utm_source__c",   "hidden form field via web-to-lead", "proposed", "Custom field on Lead"),
            ("utm_medium",   "Salesforce", "utm_medium__c",   "hidden form field via web-to-lead", "proposed", None),
            ("utm_campaign", "Salesforce", "utm_campaign__c", "hidden form field via web-to-lead", "proposed", None),
            ("utm_content",  "Salesforce", "utm_content__c",  "hidden form field via web-to-lead", "proposed", None),
            ("utm_id",       "Salesforce", "utm_id__c",       "hidden form field via web-to-lead", "proposed", None),
            ("utm_id",       "ATS (Bullhorn)", "candidate.utm_link_id", "1st-party cookie (30d) -> hidden field on application form", "proposed", "Phase 5: candidate source attribution"),
            ("utm_campaign", "LinkedIn CAPI", "conversion attribution", "matched via Tealium server-side connector", "planned", "Requires Tealium server-side"),
            ("utm_medium",   "LinkedIn CAPI", "event filter",           "only paid/sponsored traffic forwarded",     "planned", None),
            ("utm_campaign", "AudienceStream", "visitor attribute: last_campaign", "set on landing, persists per visitor", "planned", None),
            ("tag_segmentation", "AudienceStream", "audience enrichment", "segment membership from utm_id dimension", "planned", None),
        ]
        for i, (f, sysname, df, tr, st, nt) in enumerate(routing_seed):
            con.execute("INSERT INTO routing_rules(utm_field,destination_system,destination_field,transform,status,notes,sort_order,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?,?)",
                        (f, sysname, df, tr, st, nt, i, _now, "system"))
        con.commit()

    con.close()

# -------------------- helpers --------------------
def gen_short_code(n=SHORT_CODE_LEN):
    alphabet = string.ascii_lowercase + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(n))
        if not get_db().execute("SELECT 1 FROM links WHERE short_code=?", (code,)).fetchone():
            return code

def build_utm_url(destination, utm_source, utm_medium, utm_campaign,
                  utm_term=None, utm_content=None, utm_id=None):
    parts = []
    if utm_medium:   parts.append(("utm_medium", utm_medium))
    if utm_source:   parts.append(("utm_source", utm_source))
    if utm_campaign: parts.append(("utm_campaign", utm_campaign))
    if utm_term:     parts.append(("utm_term", utm_term))
    if utm_content:  parts.append(("utm_content", utm_content))
    if utm_id:       parts.append(("utm_id", utm_id))
    sep = "&" if "?" in destination else "?"
    return destination + sep + urlencode(parts)

def render_email(template_name, **context):
    """Render an HTML email template + auto-generate plain-text fallback."""
    from flask import render_template
    from html.parser import HTMLParser
    context.setdefault("base", public_base())
    context.setdefault("subject", "the Adecco Group UTM builder")
    html = render_template(f"emails/{template_name}", **context)
    # crude plaintext: strip tags
    class _Stripper(HTMLParser):
        def __init__(self): super().__init__(); self.t = []
        def handle_data(self, d): self.t.append(d)
        def handle_starttag(self, tag, attrs):
            if tag in ("p","br","div","tr","h1","h2","h3","li"): self.t.append("\n")
    s = _Stripper(); s.feed(html)
    plain = " ".join("".join(s.t).split())
    return html, plain

def send_email(subject, body=None, to=None, template=None, **context):
    """Send email. If to is None, sends to all ADMIN_EMAILS. Accepts list or string. Provide body (plain) or template + context (HTML+plaintext)."""
    # Normalize recipient list
    if to is None: to = ADMIN_EMAILS
    if isinstance(to, str): to = [to]
    to = [t for t in to if t]
    if not to: return False
    html = None; plain = body
    if template and not body:
        html, plain = render_email(template, subject=subject, **context)
    if not SMTP_HOST:
        print(f"[EMAIL NOT SENT - no SMTP] To:{to} Subject:{subject}\n{(plain or '')[:200]}", flush=True)
        return False
    try:
        from email.mime.multipart import MIMEMultipart
        if html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(plain or "", "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))
        else:
            msg = MIMEText(plain or "", "plain", "utf-8")
        # to is always a list at this point (normalized at the top of send_email)
        recipients = to if isinstance(to, list) else [to]
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = ", ".join(recipients)
        if SMTP_PORT == 465:
            ctx = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15)
            try:
                if SMTP_USER: ctx.login(SMTP_USER, SMTP_PASS)
                ctx.sendmail(SMTP_FROM, recipients, msg.as_string())
            finally:
                ctx.quit()
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
                s.starttls()
                if SMTP_USER: s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_FROM, recipients, msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL ERR] {e}", flush=True); return False

def can_manage_users():
    return session.get("admin_scope") == "all"

def current_admin_scope():
    return session.get("admin_scope", "all")

def current_admin_username():
    return session.get("admin_user", "system")

def user_required(f):
    """Any authenticated user (regular or admin) can access. Redirects to /signin if not signed in."""
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get("user"):
            return redirect(url_for("public_signin", next=request.path))
        return f(*a, **kw)
    return wrap

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*a, **kw)
    return wrap

def public_base():
    if PUBLIC_BASE_URL: return PUBLIC_BASE_URL.rstrip("/")
    return request.url_root.rstrip("/")

def get_source_medium_map(db):
    out = {}
    for r in db.execute("SELECT source,medium FROM source_medium_rules ORDER BY source,medium").fetchall():
        out.setdefault(r["source"], []).append(r["medium"])
    return out


def ga4_channel(source, medium, campaign=None):
    """Approximate GA4 default channel group rules (simplified). Returns (channel_name, color_class)."""
    s = (source or "").lower()
    m = (medium or "").lower()
    c = (campaign or "").lower()
    paid_re = ["cpc","ppc","paidsearch","retargeting","paid","cpm","display","sponsored","boosted","promoted","instream"]
    social_sources = {"facebook","instagram","linkedin","x","twitter","pinterest","tiktok","reddit","snapchat","threads"}
    video_sources = {"youtube","vimeo","tiktok"}
    search_sources = {"google","bing","yahoo","duckduckgo","baidu","yandex","ecosia"}
    shopping_sources = {"google-shopping","amazon","ebay"}
    def is_paid(x): return any(p in x for p in paid_re)
    if not s and m in ("", "(none)", "not set", None): return ("Direct", "channel-direct")
    if "cross-network" in c: return ("Cross-network", "channel-cross")
    # Email
    if "email" in s or m in ("email","e-mail","newsletter"): return ("Email", "channel-email")
    # SMS
    if s == "sms" or m == "sms": return ("SMS", "channel-sms")
    # Affiliate
    if "affiliate" in m: return ("Affiliates", "channel-affiliate")
    # Referral
    if m == "referral": return ("Referral", "channel-referral")
    # Audio
    if m == "audio": return ("Audio", "channel-audio")
    # Mobile Push
    if m.endswith("push") or "notification" in m: return ("Mobile Push Notifications", "channel-push")
    # Shopping
    if s in shopping_sources or "shop" in s or "shop" in c:
        return (("Paid Shopping" if is_paid(m) else "Organic Shopping"), ("channel-paid-shopping" if is_paid(m) else "channel-organic-shopping"))
    # Video
    if s in video_sources or "video" in m:
        return (("Paid Video" if is_paid(m) else "Organic Video"), ("channel-paid-video" if is_paid(m) else "channel-organic-video"))
    # Social
    if s in social_sources or m in ("social","social-network","social-media","sm"):
        return (("Paid Social" if is_paid(m) else "Organic Social"), ("channel-paid-social" if is_paid(m) else "channel-organic-social"))
    # Search
    if s in search_sources or m == "organic":
        if m == "organic": return ("Organic Search", "channel-organic-search")
        if is_paid(m): return ("Paid Search", "channel-paid-search")
    # Display
    if m in ("display","banner","expandable","interstitial","cpm"): return ("Display", "channel-display")
    # Paid Other
    if is_paid(m): return ("Paid Other", "channel-paid-other")
    return ("Unassigned", "channel-unassigned")

def build_utm_id(agency, targeting_type, segmentation, asset_type):
    """Concatenate non-empty dimensions with hyphens. Returns None if all empty."""
    parts = [p for p in [agency, targeting_type, segmentation, asset_type] if p]
    return "-".join(parts) if parts else None

def parse_utm_id_parts(utm_id, db):
    """Split a composite utm_id back into its 4 dimensions using taxonomy-based
    longest-match parsing. Handles multi-hyphen values (c-suite, video-short) and
    skipped dimensions (build_utm_id drops empty parts). Falls back to the legacy
    positional split when nothing matches the taxonomy."""
    kinds = ["agency", "targeting_type", "segmentation", "asset_type"]
    out = {k: None for k in kinds}
    if not utm_id:
        return out
    vocab = {}
    for r in db.execute("SELECT kind, value FROM taxonomy WHERE kind IN ('agency','targeting_type','segmentation','asset_type')").fetchall():
        vocab.setdefault(r["kind"], set()).add((r["value"] or "").strip().lower())
    tokens = [t for t in utm_id.strip().lower().split("-") if t]
    i = 0
    matched_any = False
    for k in kinds:
        if i >= len(tokens):
            break
        vs = vocab.get(k, set())
        max_len = max((v.count("-") + 1 for v in vs), default=1)
        for L in range(min(max_len, len(tokens) - i), 0, -1):
            cand = "-".join(tokens[i:i+L])
            if cand in vs:
                out[k] = cand
                i += L
                matched_any = True
                break
    if not matched_any:
        # Legacy positional fallback for utm_ids that predate the taxonomy
        parts = utm_id.split("-")
        out["agency"] = parts[0] if len(parts) >= 1 else None
        out["targeting_type"] = parts[1] if len(parts) >= 2 else None
        out["segmentation"] = parts[2] if len(parts) >= 3 else None
        out["asset_type"] = "-".join(parts[3:]) if len(parts) >= 4 else None
    elif i < len(tokens):
        rest = "-".join(tokens[i:])
        out["asset_type"] = (out["asset_type"] + "-" + rest) if out["asset_type"] else rest
    return out


def ga4_links(property_id, campaign_name=None):
    """Return a dict of deep-link URLs for a GA4 property. Filtered by campaign if given."""
    base = f"https://analytics.google.com/analytics/web/#/p{property_id}"
    out = {
        "home": base + "/reports/intelligenthome",
        "traffic_acquisition": base + "/reports/explorer?params=_u..nav%3Dmaui&r=all-pages",
        "acquisition": base + "/reports/dashboard?r=acquisition-overview",
        "events": base + "/reports/dashboard?r=engagement-events",
        "real_time": base + "/reports/realtime",
    }
    if campaign_name:
        # GA4 search query parameter for filtering by campaign in the explorer
        from urllib.parse import quote
        out["traffic_acquisition_filtered"] = base + "/reports/explorer?r=traffic-acquisition&q=" + quote(campaign_name)
    return out

def match_domain_to_property(destination_url):
    """Given a destination URL, find the matching GA4 property based on hostname."""
    if not destination_url: return None
    try:
        from urllib.parse import urlparse
        host = urlparse(destination_url).hostname or ""
        host = host.lower().lstrip(".")
        if host.startswith("www."): host = host[4:]
    except Exception: return None
    db = get_db()
    rows = db.execute("SELECT * FROM ga4_properties").fetchall()
    for r in rows:
        d = (r["domain"] or "").lower().lstrip(".")
        if d.startswith("www."): d = d[4:]
        if host == d or host.endswith("." + d):
            return dict(r)
    return None

def load_country_config(code):
    db = get_db()
    row = db.execute("SELECT config_json FROM country_config WHERE country_code=?", (code,)).fetchone()
    if not row: return None
    import json as _j
    try: return _j.loads(row["config_json"])
    except Exception: return None

def de_abbr(group, value, cfg):
    """Look up an abbreviation in a DE dropdown group. Falls back to value if missing."""
    if not value or not cfg: return ""
    dd = cfg.get("dropdowns", {}).get(group, {})
    return str(dd.get(value, value)).strip()

def lc(s):
    return str(s).strip().lower() if s else ""


@app.context_processor
def inject_admin_info():
    # Self-heal legacy sessions: if "admin" set but "user" missing (old /admin/login from earlier patches), set user too
    if session.get("admin") and not session.get("user"):
        session["user"] = True
        if not session.get("username"): session["username"] = session.get("admin_user", "admin")
        if not session.get("email"): session["email"] = session.get("admin_email", "")
        if not session.get("full_name"): session["full_name"] = session.get("admin_full_name") or session.get("admin_user", "admin")
        if not session.get("role"): session["role"] = "admin"
    info = None
    if session.get("user"):
        info = {
            "username": session.get("username") or session.get("admin_user"),
            "scope": session.get("admin_scope"),
            "email": session.get("email") or session.get("admin_email"),
            "full_name": session.get("full_name") or session.get("admin_full_name"),
            "role": session.get("role"),
            "is_admin": bool(session.get("admin")),
            "can_manage_users": session.get("admin_scope") == "all",
        }
    return {"admin_user_info": info, "current_user": info}

@app.context_processor
def inject_market_context():
    """Detect which builder the user is on (global/de/ch/other) from request path."""
    p = (request.path or "/")
    if p.startswith("/de"):
        return {"current_market": {"code":"de","name":"Germany","flag":"🇩🇪"}}
    if p.startswith("/ch"):
        return {"current_market": {"code":"ch","name":"Switzerland","flag":"🇨🇭"}}
    if p == "/" or p.startswith("/global"):
        return {"current_market": {"code":"global","name":"Global","flag":"🌐"}}
    return {"current_market": None}

# -------------------- public --------------------
@app.route("/")
@user_required
def index():
    db = get_db()
    tax = {}
    for kind in ("source","medium","stage","gbu","country","agency","targeting_type","segmentation","asset_type"):
        rows = db.execute("SELECT value,code FROM taxonomy WHERE kind=? ORDER BY sort_order,value",(kind,)).fetchall()
        tax[kind] = [{"value":r["value"],"code":r["code"]} for r in rows]
    campaigns = db.execute("SELECT id,name FROM campaigns WHERE status='approved' ORDER BY name").fetchall()
    sm_map = get_source_medium_map(db)
    return render_template("index.html", tax=tax, base=public_base(),
                           campaigns=campaigns, sm_map_json=json.dumps(sm_map))


@app.route("/api/ga4-channel")
def api_ga4_channel():
    s = request.args.get("source",""); m = request.args.get("medium",""); c = request.args.get("campaign","")
    name, klass = ga4_channel(s, m, c)
    return jsonify({"channel": name, "css_class": klass})


@app.route("/country")
@user_required
def country_picker():
    return render_template("country_picker.html", base=public_base())

@app.route("/de")
@user_required
def builder_de():
    cfg = load_country_config("de")
    if not cfg: abort(503)
    db = get_db()
    campaigns = db.execute("SELECT id,name FROM campaigns WHERE status='approved' ORDER BY name").fetchall()
    return render_template("builder_de.html", cfg=cfg, base=public_base(), campaigns=campaigns)

@app.route("/ch")
@user_required
def builder_ch():
    cfg = load_country_config("ch")
    if not cfg: abort(503)
    db = get_db()
    campaigns = db.execute("SELECT id,name FROM campaigns WHERE status='approved' ORDER BY name").fetchall()
    import json as _j
    return render_template("builder_ch.html", cfg=cfg, base=public_base(), campaigns=campaigns, sm_map_json=_j.dumps(cfg))

@app.route("/api/de/build", methods=["POST"])
def api_de_build():
    cfg = load_country_config("de")
    if not cfg: return jsonify({"error":"DE config not loaded"}), 503
    data = request.get_json(force=True)
    destination = normalize_destination(data.get("destination"))
    if not destination: return jsonify({"error":"destination required"}), 400
    # Raw values
    campaign_id = lc(data.get("campaign_id"))
    job = lc(data.get("job"))
    client = lc(data.get("client"))
    location = lc(data.get("location"))
    business_unit = lc(data.get("business_unit"))
    platform = lc(data.get("platform"))
    audience = lc(data.get("audience"))
    channel = lc(data.get("channel"))
    campaign_type = lc(data.get("campaign_type"))
    asset_type = lc(data.get("asset_type"))
    agency = lc(data.get("agency"))
    campaign_goal = lc(data.get("campaign_goal"))
    kldb = lc(data.get("kldb"))
    cost_center = lc(data.get("cost_center"))
    start_date = lc(data.get("start_date"))  # YYMMDD
    target_group = lc(data.get("target_group"))  # zg0/zg1/zg2 or empty
    dynamic_source = lc(data.get("dynamic_source"))  # "meta" or "joveo" or empty
    # Validations
    missing = []
    for f in cfg["fields_required"]:
        if not data.get(f): missing.append(f)
    if missing: return jsonify({"error":"Missing required: " + ", ".join(missing)}), 400
    import re as _re
    if cost_center and not _re.match(cfg["costcenter_regex"], cost_center):
        return jsonify({"error":"cost_center must match " + cfg["costcenter_regex"]}), 400
    if start_date and not _re.match(r"^\d{6}$", start_date):
        return jsonify({"error":"start_date must be YYMMDD (6 digits)"}), 400
    if campaign_id and not _re.match(r"^\d{1,5}$", campaign_id):
        return jsonify({"error":"campaign_id must be 1-5 digits"}), 400
    if target_group and target_group not in ("zg0", "zg1", "zg2"):
        return jsonify({"error":"target_group must be zg0, zg1, or zg2"}), 400
    if dynamic_source and dynamic_source not in ("meta", "joveo"):
        return jsonify({"error":"dynamic_source must be meta or joveo"}), 400
    # Abbreviations
    pf_abbr = de_abbr("platforms", platform, cfg) or "na"
    ch_abbr = de_abbr("channels", channel, cfg) or "na"
    ct_abbr = de_abbr("campaign_types", campaign_type, cfg) or "na"
    at_abbr = de_abbr("asset_types", asset_type, cfg) or "na"
    job_abbr = de_abbr("jobs", job, cfg) or ""
    client_abbr = de_abbr("clients", client, cfg) or ""
    loc_abbr = de_abbr("locations", location, cfg) or ""
    bu_map = dict(cfg.get("business_units", []))
    bu_abbr = bu_map.get(business_unit, "na")
    # Build short campaign name in canonical DE order:
    # campaign_id _ platform _ channel _ type _ asset _ costcenter _ date _ kldb _ job _ client? _ target_group? _ bu _ location?
    parts = [campaign_id, pf_abbr, ch_abbr, ct_abbr, at_abbr, cost_center, start_date, kldb, job_abbr]
    if client_abbr: parts.append(client_abbr)
    if target_group: parts.append(target_group)
    parts.append(bu_abbr)
    if loc_abbr: parts.append(loc_abbr)
    campaign_short = "_".join([p for p in parts if p])
    if len(campaign_short) > cfg["char_cap"]:
        cap = cfg["char_cap"]
        return jsonify({"error": "Generated campaign name " + str(len(campaign_short)) + " chars exceeds " + str(cap) + "-char cap. Shorten: " + campaign_short}), 400
    # utm_source: dynamic override if requested
    if dynamic_source == "meta": utm_source = "{{placement}}"
    elif dynamic_source == "joveo": utm_source = "[dynamic source by joveo]"
    else: utm_source = pf_abbr
    utm_medium = ch_abbr
    long_url = build_utm_url(destination, utm_source, utm_medium, campaign_short, utm_term=None, utm_content=None)
    short = gen_short_code()
    now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
    db = get_db()
    # Auto-fill creator_email from session if logged in
    creator_email = normalize_email(data.get("creator_email")) or session.get("email") or None
    cur = db.execute("""INSERT INTO links(short_code,long_url,destination,utm_source,utm_medium,
                 utm_campaign,utm_term,utm_content,gbu,brand_tag,country,country_code,notes,
                 status,requested_by_email,created_at,approved_at,campaign_id,
                 utm_id_built,agency,targeting_type,segmentation,asset_type)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (short, long_url, destination, utm_source, utm_medium, campaign_short,
                None, None, business_unit, bu_abbr, "Germany", "de", data.get("notes"),
                "approved", creator_email, now, now, None,
                None, agency or None, None, None, asset_type or None))
    link_id = cur.lastrowid
    db.execute("INSERT INTO link_events(link_id,event_type,email,ts,ip,ua) VALUES(?,?,?,?,?,?)",
               (link_id, "created", creator_email, now, request.headers.get("X-Forwarded-For", request.remote_addr), request.headers.get("User-Agent","")))
    db.commit()
    ch_name, ch_class = ga4_channel(utm_source, utm_medium)
    prop = match_domain_to_property(destination)
    ga4 = None
    if prop: ga4 = {"property_id": prop["property_id"], "gbu_name": prop["gbu_name"], "domain": prop["domain"], "links": ga4_links(prop["property_id"], campaign_short)}
    return jsonify({"link_id": link_id, "long_url": long_url, "short_url": f"{public_base()}/s/{short}", "short_code": short,
                    "campaign_name": campaign_short, "char_count": len(campaign_short), "char_cap": cfg["char_cap"],
                    "ga4_channel": ch_name, "ga4_channel_class": ch_class, "ga4_property": ga4})

@app.route("/api/ch/build", methods=["POST"])
def api_ch_build():
    cfg = load_country_config("ch")
    if not cfg: return jsonify({"error":"CH config not loaded"}), 503
    data = request.get_json(force=True)
    destination = normalize_destination(data.get("destination"))
    if not destination: return jsonify({"error":"destination required"}), 400
    campaign_name = (data.get("campaign_name") or "").strip()
    if not campaign_name: return jsonify({"error":"campaign_name required"}), 400
    import re as _re
    # CH allows mixed-case input but lowercases for the URL. Allow letters/digits, no spaces.
    if not _re.match(r"^[A-Za-z0-9]+$", campaign_name):
        return jsonify({"error":"campaign_name letters and digits only, no spaces or special chars"}), 400
    campaign_type = (data.get("campaign_type") or "").strip()
    brand = (data.get("brand") or "").strip()
    language = (data.get("language") or "").strip()
    channel = (data.get("channel") or "").strip()
    source = (data.get("source") or "").strip()
    type_ = (data.get("type") or "").strip()
    costcenter = (data.get("costcenter") or "").strip()
    year = (data.get("year") or "").strip()
    missing = []
    for f in ["campaign_type","brand","language","channel","source","type","costcenter","year"]:
        if not data.get(f): missing.append(f)
    if missing: return jsonify({"error":"Missing required: " + ", ".join(missing)}), 400
    if not _re.match(cfg["costcenter_regex"], costcenter):
        return jsonify({"error":"costcenter must be A### (A + 3 digits) or 3 digits"}), 400
    if not _re.match(r"^[0-9]{4}$", year):
        return jsonify({"error":"year must be 4 digits"}), 400
    # Validate channel-source-type pairing
    cs_pairs = {(c.lower(), s.lower()) for c,s in cfg.get("channel_source_pairs", [])}
    st_pairs = {(s.lower(), t.lower()) for s,t in cfg.get("source_type_pairs", [])}
    if (channel.lower(), source.lower()) not in cs_pairs:
        return jsonify({"error": f"Source \"{source}\" is not valid for channel \"{channel}\""}), 400
    if (source.lower(), type_.lower()) not in st_pairs:
        return jsonify({"error": f"Type \"{type_}\" is not valid for source \"{source}\""}), 400
    # Build URL: CH format
    cc_lc = costcenter.lower(); ct_lc = campaign_type.lower(); br_lc = brand.lower()
    cn_lc = campaign_name.lower(); lang_lc = language.lower(); src_lc = source.lower().replace(" ", ""); type_lc = type_.lower()
    medium = cfg["channel_to_medium"].get(channel, "organic")
    utm_source = f"{src_lc}_{type_lc}"
    utm_campaign = f"{cc_lc}_{ct_lc}_{br_lc}_{cn_lc}_{year}_{lang_lc}"
    sep = "&" if "?" in destination else "?"
    long_url = f"{destination}{sep}utm_source={utm_source}&utm_medium={medium}&utm_campaign={utm_campaign}&source={utm_source}"
    short = gen_short_code()
    now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
    db = get_db()
    # Auto-fill creator_email from session if logged in
    creator_email = normalize_email(data.get("creator_email")) or session.get("email") or None
    cur = db.execute("""INSERT INTO links(short_code,long_url,destination,utm_source,utm_medium,
                 utm_campaign,utm_term,utm_content,gbu,brand_tag,country,country_code,notes,
                 status,requested_by_email,created_at,approved_at,campaign_id)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (short, long_url, destination, utm_source, medium, utm_campaign,
                None, None, brand, br_lc, "Switzerland", "ch", data.get("notes"),
                "approved", creator_email, now, now, None))
    link_id = cur.lastrowid
    db.execute("INSERT INTO link_events(link_id,event_type,email,ts,ip,ua) VALUES(?,?,?,?,?,?)",
               (link_id, "created", creator_email, now, request.headers.get("X-Forwarded-For", request.remote_addr), request.headers.get("User-Agent","")))
    db.commit()
    ch_name, ch_class = ga4_channel(utm_source.split("_")[0], medium)
    prop = match_domain_to_property(destination)
    ga4 = None
    if prop: ga4 = {"property_id": prop["property_id"], "gbu_name": prop["gbu_name"], "domain": prop["domain"], "links": ga4_links(prop["property_id"], utm_campaign)}
    return jsonify({"link_id": link_id, "long_url": long_url, "short_url": f"{public_base()}/s/{short}", "short_code": short,
                    "campaign_name": utm_campaign, "ga4_channel": ch_name, "ga4_channel_class": ch_class, "ga4_property": ga4})


@app.route("/api/combination-rules")
def api_combination_rules():
    s = (request.args.get("source") or "").strip().lower()
    m = (request.args.get("medium") or "").strip().lower()
    db = get_db()
    out = {}
    if not s or not m:
        return jsonify({"source": s, "medium": m, "rules": {}})
    rows = db.execute("SELECT dimension_kind, allowed_values_json, required FROM combination_rules WHERE source=? AND medium=?", (s, m)).fetchall()
    import json as _j
    for r in rows:
        try:
            vals = _j.loads(r["allowed_values_json"]) if r["allowed_values_json"] else None
        except Exception:
            vals = None
        out[r["dimension_kind"]] = {"allowed": vals, "required": bool(r["required"])}
    return jsonify({"source": s, "medium": m, "rules": out})

@app.route("/api/source-mediums")
def api_source_mediums():
    return jsonify(get_source_medium_map(get_db()))

@app.route("/api/campaigns")
def api_campaigns():
    db = get_db()
    rows = db.execute("SELECT id,name FROM campaigns WHERE status='approved' ORDER BY name").fetchall()
    return jsonify([{"id":r["id"],"name":r["name"]} for r in rows])

@app.route("/api/campaigns/request", methods=["POST"])
def api_campaign_request():
    data = request.get_json(force=True)
    name, err = normalize_campaign_name(data.get("name"))
    if err: return jsonify({"error": err}), 400
    email = normalize_email(data.get("email"))
    notes = (data.get("notes") or "").strip() or None
    if not email or "@" not in email: return jsonify({"error":"Valid email required"}), 400
    db = get_db()
    existing = db.execute("SELECT id,status FROM campaigns WHERE name=?", (name,)).fetchone()
    if existing:
        return jsonify({"error":f"Campaign '{name}' already exists with status {existing['status']}"}), 409
    now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
    country_scope = (data.get("country_scope") or "global").strip().lower()
    if country_scope not in ("global","de","ch","it","fr","es","at","be","nl","gb","us"):
        country_scope = "global"
    db.execute("INSERT INTO campaigns(name,status,requested_by_email,notes,created_at,country_scope) VALUES(?,?,?,?,?,?)",
               (name, "pending", email, notes, now, country_scope))
    db.commit()
    send_email(
        subject=f"[UTM] New campaign request: {name}",
        template="campaign_request.html",
        to=ADMIN_EMAIL,
        name=name, country_scope=country_scope, email=email, notes=notes,
    )
    return jsonify({"ok":True, "name":name, "status":"pending"})


@app.route("/api/parse-url", methods=["POST"])
def api_parse_url():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url: return jsonify({"error":"url required"}), 400
    from urllib.parse import urlsplit, parse_qs, urlunsplit
    try:
        p = urlsplit(url if url.lower().startswith(("http://","https://")) else "https://" + url)
        q = {k.lower(): (v[0] if v else "") for k,v in parse_qs(p.query).items()}
        # Strip UTM params from query to rebuild clean destination
        clean_q = "&".join(f"{k}={v}" for k,v in parse_qs(p.query).items() for v in v if not k.lower().startswith("utm_") and k.lower() != "source") if p.query else ""
        clean_query = "&".join(f"{k}={vv}" for k,vs in parse_qs(p.query).items() if not k.lower().startswith("utm_") and k.lower() != "source" for vv in vs)
        destination = urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, clean_query, ""))
        # If utm_content has brand_country pattern, split it
        content = q.get("utm_content","")
        gbu = country = None
        if "_" in content:
            db = get_db()
            parts = content.split("_")
            if len(parts) >= 2:
                brand_code, country_code = parts[0], parts[1]
                r = db.execute("SELECT value FROM taxonomy WHERE kind='gbu' AND code=?", (brand_code,)).fetchone()
                if r: gbu = r["value"]
                r = db.execute("SELECT value FROM taxonomy WHERE kind='country' AND code=?", (country_code,)).fetchone()
                if r: country = r["value"]
        # If utm_id has agency-targeting-segmentation-asset, split it back into
        # dimensions via taxonomy-based longest-match (handles c-suite, video-short etc.)
        utm_id = q.get("utm_id","")
        agency = targeting = segmentation = asset = None
        if utm_id:
            _idp = parse_utm_id_parts(utm_id, get_db())
            agency, targeting = _idp["agency"], _idp["targeting_type"]
            segmentation, asset = _idp["segmentation"], _idp["asset_type"]
        return jsonify({
            "destination": destination,
            "utm_source": q.get("utm_source"),
            "utm_medium": q.get("utm_medium"),
            "utm_campaign": q.get("utm_campaign"),
            "utm_term": q.get("utm_term"),
            "utm_content": q.get("utm_content"),
            "utm_id": q.get("utm_id"),
            "gbu": gbu, "country": country,
            "agency": agency, "targeting_type": targeting,
            "segmentation": segmentation, "asset_type": asset,
        })
    except Exception as e:
        return jsonify({"error": f"Could not parse URL: {e}"}), 400


@app.route("/api/prefill-link")
def api_prefill_link():
    lid = request.args.get("id")
    if not lid: return jsonify({"error":"id required"}), 400
    db = get_db()
    r = db.execute("SELECT * FROM links WHERE id=?", (lid,)).fetchone()
    if not r: return jsonify({"error":"link not found"}), 404
    return jsonify({
        "destination": r["destination"],
        "utm_source": r["utm_source"], "utm_medium": r["utm_medium"],
        "utm_campaign": r["utm_campaign"], "utm_term": r["utm_term"],
        "utm_content": r["utm_content"], "utm_id": r["utm_id_built"],
        "gbu": r["gbu"], "country": r["country"],
        "agency": r["agency"], "targeting_type": r["targeting_type"],
        "segmentation": r["segmentation"], "asset_type": r["asset_type"],
    })


@app.route("/api/preflight")
def api_preflight():
    """Pre-flight check on a destination URL. Aware of Tealium + OneTrust + GTM stacks."""
    url = (request.args.get("url") or "").strip()
    if not url: return jsonify({"error":"url required"}), 400
    if not url.lower().startswith(("http://","https://")): url = "https://" + url
    checks = []; total = 0; ok = 0; weight_total = 0; weight_ok = 0
    import socket, ssl, re as _re, time
    from urllib.request import Request, urlopen
    from urllib.parse import urlparse
    p = urlparse(url)

    # HTTPS (weight 1)
    total += 1; weight_total += 1
    if p.scheme == "https":
        checks.append({"name":"HTTPS encryption","status":"ok","detail":"Site uses HTTPS"}); ok += 1; weight_ok += 1
    else:
        checks.append({"name":"HTTPS encryption","status":"fail","detail":"Site uses HTTP, not HTTPS. Visitors see a Not Secure warning."})

    body = ""; headers = {}; status_code = 0; load_time_ms = 0; final_url = url
    try:
        start = time.time()
        req = Request(url, headers={"User-Agent":"Mozilla/5.0 (compatible; TAG-UTM-Builder-Preflight/2.0)"})
        ctx = ssl.create_default_context()
        resp = urlopen(req, timeout=10, context=ctx)
        load_time_ms = int((time.time() - start) * 1000)
        status_code = resp.status
        final_url = resp.url
        raw = resp.read(500_000)  # cap 500KB so we can see more deeply-loaded scripts
        try: body = raw.decode("utf-8", errors="ignore")
        except: body = raw.decode("latin-1", errors="ignore")
        headers = dict(resp.headers)
    except Exception as e:
        checks.append({"name":"Page reachable","status":"fail","detail":f"Could not load: {e}"})
        return jsonify({"url": url, "checks": checks, "score": 0})

    # Status code (weight 2)
    total += 1; weight_total += 2
    if status_code == 200:
        checks.append({"name":"Page reachable","status":"ok","detail":f"HTTP {status_code}, loaded in {load_time_ms}ms"}); ok += 1; weight_ok += 2
    else:
        checks.append({"name":"Page reachable","status":"warn","detail":f"HTTP {status_code} (not 200)"})

    # Redirect awareness
    if final_url and final_url != url:
        total += 1; weight_total += 1
        checks.append({"name":"Redirects","status":"warn","detail":f"Redirects to {final_url}. Consider using the final URL directly to skip the hop."})

    # Speed (weight 2)
    total += 1; weight_total += 2
    if load_time_ms < 1500:
        checks.append({"name":"Page load speed","status":"ok","detail":f"{load_time_ms}ms (fast)"}); ok += 1; weight_ok += 2
    elif load_time_ms < 3500:
        checks.append({"name":"Page load speed","status":"warn","detail":f"{load_time_ms}ms (acceptable, but mobile users on 3G will struggle)"}); weight_ok += 1
    else:
        checks.append({"name":"Page load speed","status":"fail","detail":f"{load_time_ms}ms (very slow, expect drop-off)"})

    body_lc = body.lower()

    # ---- Tag/Consent stack detection ----
    has_onetrust = bool(_re.search(r"(cdn\.cookielaw\.org|otsdkstub|optanon|onetrust\.onconsent)", body_lc))
    has_tealium = bool(_re.search(r"(tags\.tiqcdn\.com|utag\.js|utag_data|tealium)", body_lc))
    has_gtm = bool(_re.search(r"(googletagmanager\.com/gtm\.js|gtm-[a-z0-9]+)", body_lc))
    has_adobe_launch = bool(_re.search(r"(assets\.adobedtm\.com|adobedtm\.com/launch)", body_lc))
    ga4_match = _re.search(r"(G-[A-Z0-9]{6,12})", body)
    has_gtag = "gtag(" in body_lc or "google-analytics.com/g/collect" in body_lc or "googletagmanager.com" in body_lc

    # ---- Consent Management (informational) ----
    if has_onetrust:
        total += 1; weight_total += 1; weight_ok += 1; ok += 1
        checks.append({"name":"Consent management","status":"ok","detail":"OneTrust detected. Cookie consent banner active. Marketing tags (GA4, Tealium) load only after user accepts."})

    # ---- Tag manager (informational) ----
    tag_mgr_names = []
    if has_tealium: tag_mgr_names.append("Tealium iQ")
    if has_gtm: tag_mgr_names.append("Google Tag Manager")
    if has_adobe_launch: tag_mgr_names.append("Adobe Launch")
    if tag_mgr_names:
        total += 1; weight_total += 1; weight_ok += 1; ok += 1
        checks.append({"name":"Tag manager","status":"ok","detail":" + ".join(tag_mgr_names) + " present. GA4 is likely configured as a managed tag inside it."})

    # ---- GA4 tracking (weight 3, with consent awareness) ----
    total += 1; weight_total += 3
    if ga4_match:
        gid = ga4_match.group(1)
        checks.append({"name":"GA4 tracking","status":"ok","detail":f"Measurement ID found directly in HTML: {gid}"}); ok += 1; weight_ok += 3
    elif has_gtag and not has_onetrust:
        checks.append({"name":"GA4 tracking","status":"ok","detail":"Google Tag Manager / gtag.js loaded directly (GA4 likely active immediately)"}); ok += 1; weight_ok += 3
    elif has_tealium or has_gtm or has_adobe_launch:
        # Tag manager + consent stack: GA4 ID won't be in initial HTML — give full credit, this is the correct setup
        if has_onetrust:
            checks.append({"name":"GA4 tracking","status":"ok","detail":"Managed via " + (tag_mgr_names[0] if tag_mgr_names else "tag manager") + " behind OneTrust consent. This is the GDPR-correct setup. Verify live via GA4 DebugView once a user consents."})
            ok += 1; weight_ok += 3
        else:
            checks.append({"name":"GA4 tracking","status":"warn","detail":"Tag manager present but GA4 ID not found in HTML. Check that the GA4 tag is published in the live environment."}); weight_ok += 1
    elif has_onetrust:
        # OneTrust without detectable tag manager: probably loads later via consent
        checks.append({"name":"GA4 tracking","status":"warn","detail":"OneTrust consent banner active. GA4 tags likely fire only after consent, which my crawler cannot do. Verify in browser via GA4 DebugView."}); weight_ok += 1
    else:
        checks.append({"name":"GA4 tracking","status":"fail","detail":"No GA4 measurement ID, no gtag.js, no tag manager, and no consent banner detected. UTMs will be tracked in URLs but not visible in GA4."})

    # ---- Open Graph tags ----
    og_title = _re.search(r"<meta[^>]+property=[\'\"]og:title[\'\"][^>]+content=[\'\"]([^\'\"]+)", body, _re.I)
    og_image = _re.search(r"<meta[^>]+property=[\'\"]og:image[\'\"][^>]+content=[\'\"]([^\'\"]+)", body, _re.I)
    og_desc  = _re.search(r"<meta[^>]+property=[\'\"]og:description[\'\"][^>]+content=[\'\"]([^\'\"]+)", body, _re.I)
    total += 1; weight_total += 2
    if og_title and og_image and og_desc:
        checks.append({"name":"Open Graph tags (social preview)","status":"ok","detail":"og:title, og:image, og:description all present"}); ok += 1; weight_ok += 2
    else:
        missing = []
        if not og_title: missing.append("og:title")
        if not og_image: missing.append("og:image")
        if not og_desc: missing.append("og:description")
        checks.append({"name":"Open Graph tags (social preview)","status":"warn","detail":"Missing: " + ", ".join(missing) + ". LinkedIn / X preview will be ugly."}); weight_ok += 1

    # ---- Viewport ----
    total += 1; weight_total += 1
    if _re.search(r"<meta[^>]+name=[\'\"]viewport[\'\"]", body, _re.I):
        checks.append({"name":"Mobile-friendly viewport","status":"ok","detail":"Viewport meta tag present"}); ok += 1; weight_ok += 1
    else:
        checks.append({"name":"Mobile-friendly viewport","status":"fail","detail":"No viewport meta tag. Page will not render correctly on mobile."})

    # ---- Title ----
    total += 1; weight_total += 1
    t = _re.search(r"<title>([^<]+)</title>", body, _re.I)
    if t:
        title_clean = t.group(1).strip()[:80]
        checks.append({"name":"Page title","status":"ok","detail":f"\"{title_clean}\""}); ok += 1; weight_ok += 1
    else:
        checks.append({"name":"Page title","status":"fail","detail":"No <title> tag found"})

    score = int(100 * weight_ok / weight_total) if weight_total else 0
    return jsonify({"url": url, "final_url": final_url, "load_time_ms": load_time_ms, "checks": checks, "score": score,
                    "stack": {"onetrust": has_onetrust, "tealium": has_tealium, "gtm": has_gtm, "adobe_launch": has_adobe_launch, "ga4_id_inline": ga4_match.group(1) if ga4_match else None}})

@app.route("/api/parse-brief", methods=["POST"])
def api_parse_brief():
    """Rule-based natural-language brief parser. Extracts UTM fields from free text."""
    data = request.get_json(force=True)
    brief = (data.get("brief") or "").strip()
    if not brief: return jsonify({"error":"brief required"}), 400
    text = brief.lower()
    db = get_db()
    out = {}
    # Source detection
    sources = {r["value"]: r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='source'").fetchall()}
    import re as _re_src
    src_aliases = [("linked in","linkedin"),("linkedln","linkedin"),(r"\bfb\b","facebook"),(r"\big\b","instagram"),(r"\bx\b","x"),(r"\btwitter\b","x"),(r"\btt\b","tiktok"),("tik tok","tiktok"),(r"\byt\b","youtube"),("g ads","google"),("google ads","google"),("bing ads","bing")]
    for alias, real in src_aliases:
        if alias.startswith("\\b"):
            if _re_src.search(alias, text) and real in sources: out["utm_source"] = real; break
        elif alias in text and real in sources: out["utm_source"] = real; break
    if "utm_source" not in out:
        for s in sources:
            if s and len(s) >= 3 and s in text: out["utm_source"] = s; break
    # Medium detection
    if "sponsored" in text: out["utm_medium"] = "sponsored"
    elif "paid social" in text or "social paid" in text: out["utm_medium"] = "social"
    elif "paid search" in text: out["utm_medium"] = "cpc"
    elif "cpc" in text or "ppc" in text: out["utm_medium"] = "cpc"
    elif "cpm" in text: out["utm_medium"] = "cpm"
    elif "display" in text: out["utm_medium"] = "display"
    elif "instream" in text or "pre-roll" in text or "preroll" in text: out["utm_medium"] = "instream"
    elif "newsletter" in text or "email" in text: out["utm_medium"] = "email"
    elif "organic" in text: out["utm_medium"] = "organic"
    elif "post" in text and ("linkedin" in text or "facebook" in text or "instagram" in text): out["utm_medium"] = "post"
    elif "paid" in text: out["utm_medium"] = "paid"
    elif "social" in text: out["utm_medium"] = "social"
    # ---- Validate and remap medium against source_medium_rules ----
    # If the parser picked "paid" but the source's allowed mediums don\'t include "paid",
    # try to find the source-specific paid equivalent (e.g. linkedin+paid → sponsored).
    if "utm_source" in out and "utm_medium" in out:
        s = out["utm_source"]; m = out["utm_medium"]
        allowed = [r["medium"] for r in db.execute("SELECT medium FROM source_medium_rules WHERE source=?", (s,)).fetchall()]
        if allowed and m not in allowed:
            # Priority list for "paid"-intent fallbacks
            paid_intent = ["sponsored","cpc","paid","cpm","display","boosted","promoted","instream"]
            organic_intent = ["organic","social","post"]
            email_intent = ["email","automation"]
            # Choose intent bucket based on original text
            buckets = []
            if any(p in text for p in ["paid","sponsored","cpc","cpm","display","ad ","ads"]):
                buckets = paid_intent
            elif any(p in text for p in ["organic","post","social"]):
                buckets = organic_intent
            elif any(p in text for p in ["email","newsletter","mail"]):
                buckets = email_intent
            else:
                buckets = paid_intent + organic_intent
            # Pick the first bucket-medium that\'s allowed for this source
            mapped = next((b for b in buckets if b in allowed), None)
            if mapped:
                out["utm_medium"] = mapped
                out["_medium_remapped_from"] = m

    # Stage / utm_term
    stages = [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='stage'").fetchall()]
    for s in stages:
        if s in text: out["utm_term"] = s; break
    if "utm_term" not in out:
        if "awareness" in text or "brand awareness" in text: out["utm_term"] = "awareness"
        elif "consideration" in text: out["utm_term"] = "consideration"
        elif "conversion" in text or "lead" in text: out["utm_term"] = "conversion"
        elif "retention" in text or "engagement" in text: out["utm_term"] = "engagement"
    # GBU
    gbus = {r["value"].lower(): r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='gbu'").fetchall()}
    for g_lower, g_real in gbus.items():
        if g_lower in text: out["gbu"] = g_real; break
    # Country
    countries = {r["value"].lower(): r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='country'").fetchall()}
    country_aliases = {"dach": ["germany","switzerland","austria"], "uk": ["united kingdom"], "us": ["united states"]}
    import re as _re_c
    for ca, candidates in country_aliases.items():
        if _re_c.search(r"\b" + _re_c.escape(ca) + r"\b", text):
            for cand in candidates:
                if cand in countries: out["country"] = countries[cand]; break
            if "country" in out: break
    if "country" not in out:
        for c_lower, c_real in countries.items():
            if len(c_lower) > 2 and _re_c.search(r"\b" + _re_c.escape(c_lower) + r"\b", text):
                out["country"] = c_real; break
    # Targeting type
    if "retarget" in text: out["targeting_type"] = "retargeting"
    elif "lookalike" in text: out["targeting_type"] = "lookalike"
    elif "interest" in text: out["targeting_type"] = "interest"
    elif "prospect" in text or "new audience" in text or "new customer" in text: out["targeting_type"] = "prospecting"
    elif "keyword" in text: out["targeting_type"] = "keyword"
    # Segmentation
    if "c-suite" in text or "c suite" in text or "ceo" in text or "executive" in text: out["segmentation"] = "c-suite"
    elif "hr leader" in text or "chro" in text or "hr exec" in text: out["segmentation"] = "hr-leaders"
    elif "talent acquisition" in text or "recruiter" in text: out["segmentation"] = "talent-acquisition"
    elif "candidate" in text or "job seeker" in text or "applicant" in text: out["segmentation"] = "candidates"
    elif "alumni" in text: out["segmentation"] = "alumni"
    elif "existing client" in text: out["segmentation"] = "clients-existing"
    elif "prospect client" in text or "new client" in text: out["segmentation"] = "clients-prospect"
    elif "employee" in text: out["segmentation"] = "employees"
    elif "student" in text or "graduate" in text: out["segmentation"] = "students"
    # Asset type
    if "carousel" in text: out["asset_type"] = "image-carousel"
    elif "video" in text and ("short" in text or "30s" in text or "15s" in text or "reel" in text): out["asset_type"] = "video-short"
    elif "video" in text: out["asset_type"] = "video-long"
    elif "story" in text or "stories" in text: out["asset_type"] = "story"
    elif "static" in text or "image" in text or "banner" in text: out["asset_type"] = "image-static"
    elif "native" in text: out["asset_type"] = "native"
    elif "text ad" in text or "search ad" in text: out["asset_type"] = "text-ad"
    elif "whitepaper" in text or "ebook" in text: out["asset_type"] = "whitepaper"
    elif "webinar" in text: out["asset_type"] = "webinar"
    elif "case study" in text or "case-study" in text: out["asset_type"] = "case-study"
    elif "infographic" in text: out["asset_type"] = "infographic"
    elif "podcast" in text: out["asset_type"] = "podcast"
    # Agency
    if "internal" in text or "in-house" in text or "inhouse" in text: out["agency"] = "internal"
    # Campaign hint (existing or new)
    camps = [r["name"] for r in db.execute("SELECT name FROM campaigns WHERE status='approved'").fetchall()]
    for c in camps:
        if c in text.replace(" ", "_") or c.replace("_", " ") in text:
            out["utm_campaign"] = c; break
    return jsonify({"brief": brief, "suggested": out, "confidence": len(out)})


@app.route("/api/build/variants", methods=["POST"])
def api_build_variants():
    """Build N variants of the same UTM with different utm_content suffixes (v1, v2, ...)."""
    data = request.get_json(force=True)
    count = int(data.get("variant_count", 3))
    if count < 2 or count > 10: return jsonify({"error":"variant_count must be 2-10"}), 400
    results = []
    base_content = data.get("base_content") or ""
    for i in range(count):
        variant_payload = dict(data)
        # Remove control fields
        variant_payload.pop("variant_count", None)
        variant_payload.pop("base_content", None)
        suffix = f"_v{i+1}"
        # Append variant suffix to utm_content
        cv = base_content
        if not cv:
            # let the normal logic compute brand_country, then we re-append
            cv = ""
        variant_payload["_variant_suffix"] = suffix
        # Call build logic
        with app.test_request_context(json=variant_payload, headers=dict(request.headers)):
            response = api_build()
            if hasattr(response, "get_json"):
                results.append(response.get_json())
            else:
                results.append({"error":"variant build failed"})
    return jsonify({"variants": results, "count": len(results)})


@app.route("/api/me")
def api_me():
    """Return current signed-in user info for the extension."""
    if not session.get("user"):
        return jsonify({"signed_in": False})
    return jsonify({
        "signed_in": True,
        "username": session.get("username") or session.get("admin_user"),
        "email": session.get("email") or session.get("admin_email"),
        "full_name": session.get("full_name") or session.get("admin_full_name"),
        "role": session.get("role"),
        "is_admin": bool(session.get("admin")),
    })

@app.route("/api/dropdowns")
def api_dropdowns():
    """Combined dropdown payload for the extension popup."""
    db = get_db()
    out = {
        "campaigns": [{"id": r["id"], "name": r["name"]} for r in db.execute("SELECT id, name FROM campaigns WHERE status='approved' ORDER BY name").fetchall()],
        "sources":   [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='source' ORDER BY value").fetchall()],
        "mediums":   [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='medium' ORDER BY value").fetchall()],
        "gbus":      [{"value": r["value"], "code": r["code"]} for r in db.execute("SELECT value, code FROM taxonomy WHERE kind='gbu' ORDER BY sort_order").fetchall()],
        "countries": [{"value": r["value"], "code": r["code"]} for r in db.execute("SELECT value, code FROM taxonomy WHERE kind='country' ORDER BY sort_order").fetchall()],
        "sm_map":    get_source_medium_map(db),
    }
    return jsonify(out)

@app.route("/api/build", methods=["OPTIONS"])
@app.route("/api/me", methods=["OPTIONS"])
@app.route("/api/dropdowns", methods=["OPTIONS"])
def api_options_preflight():
    return ("", 204)

@app.route("/extension/download")
@user_required
def extension_download():
    """Serve the packaged Chrome extension ZIP."""
    import os as _os
    from flask import send_file
    path = "/app/static/utm-extension.zip"
    if not _os.path.exists(path):
        return "Extension not yet built. Ask the admin.", 404
    return send_file(path, as_attachment=True, download_name="tag-utm-builder-extension-v1.0.0.zip", mimetype="application/zip")

@app.route("/extension")
@user_required
def extension_page():
    return render_template("extension.html", base=public_base())


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    data = request.get_json(force=True)
    kind = (data.get("kind") or "").strip().lower()
    message = (data.get("message") or "").strip()
    email = normalize_email(data.get("email")) or session.get("email") or None
    page = (data.get("page") or "").strip()[:300] or None
    if kind not in ("bug","wish","praise","other"):
        return jsonify({"error":"Please pick a feedback kind"}), 400
    if len(message) < 6:
        return jsonify({"error":"Message too short. Be specific enough so we can act."}), 400
    if len(message) > 4000:
        return jsonify({"error":"Message too long. Keep it under 4000 chars."}), 400
    db = get_db()
    now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
    username = session.get("username") or session.get("admin_user") or None
    db.execute("INSERT INTO feedback(kind, message, email, page, user_agent, ip, username, status, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (kind, message, email, page, request.headers.get("User-Agent","")[:500], request.headers.get("X-Forwarded-For", request.remote_addr), username, "new", now))
    db.commit()
    icon = {"bug":"🐛","wish":"💡","praise":"🎉","other":"💬"}.get(kind, "💬")
    kind_label = {"bug":"Bug report","wish":"Feature wish","praise":"Praise","other":"Comment"}.get(kind, "Feedback")
    send_email(
        subject=f"{icon} [{kind_label}] from " + (email or username or "anonymous"),
        template="feedback_received.html",
        to=None,  # all admins
        kind=kind, kind_icon=icon, kind_label=kind_label, message=message, email=email, page=page, username=username, ts=now,
    )
    return jsonify({"ok": True, "id": db.execute("SELECT last_insert_rowid() AS x").fetchone()["x"]})

@app.route("/admin/feedback")
@login_required
def admin_feedback():
    db = get_db()
    new_items = db.execute("SELECT * FROM feedback WHERE status='new' ORDER BY created_at DESC").fetchall()
    resolved = db.execute("SELECT * FROM feedback WHERE status='resolved' ORDER BY resolved_at DESC LIMIT 100").fetchall()
    return render_template("admin_feedback.html", new_items=new_items, resolved=resolved)

@app.route("/admin/feedback/<int:fid>/<action>", methods=["POST"])
@login_required
def admin_feedback_action(fid, action):
    if action not in ("resolve","reopen","delete"): abort(400)
    db = get_db()
    if action == "resolve":
        db.execute("UPDATE feedback SET status='resolved', resolved_at=? WHERE id=?", (datetime.utcnow().isoformat(timespec='seconds')+'Z', fid))
    elif action == "reopen":
        db.execute("UPDATE feedback SET status='new', resolved_at=NULL WHERE id=?", (fid,))
    elif action == "delete":
        db.execute("DELETE FROM feedback WHERE id=?", (fid,))
    db.commit()
    return redirect(request.referrer or url_for("admin_feedback"))

@app.route("/api/build", methods=["POST"])
def api_build():
    data = request.get_json(force=True)
    destination = normalize_destination(data.get("destination"))
    if not destination: return jsonify({"error":"destination required"}), 400

    utm_source = (data.get("utm_source") or "").strip().lower() or None
    utm_medium = (data.get("utm_medium") or "").strip().lower() or None
    utm_campaign = (data.get("utm_campaign") or "").strip().lower() or None
    utm_term = (data.get("utm_term") or "").strip().lower() or None
    gbu = (data.get("gbu") or "").strip() or None
    country = (data.get("country") or "").strip() or None
    notes = (data.get("notes") or "").strip() or None
    # Auto-fill creator_email from session if logged in
    creator_email = normalize_email(data.get("creator_email")) or session.get("email") or None
    agency = (data.get("agency") or "").strip().lower() or None
    targeting_type = (data.get("targeting_type") or "").strip().lower() or None
    segmentation = (data.get("segmentation") or "").strip().lower() or None
    asset_type = (data.get("asset_type") or "").strip().lower() or None

    if not utm_campaign: return jsonify({"error":"Campaign required. Pick one or request a new one."}), 400

    db = get_db()
    # Validate campaign is approved
    camp = db.execute("SELECT id,status FROM campaigns WHERE name=?", (utm_campaign,)).fetchone()
    if not camp or camp["status"] != "approved":
        return jsonify({"error":f"Campaign '{utm_campaign}' is not approved. Pick an approved campaign or request a new one."}), 400

    # Validate source/medium combination
    if utm_source and utm_medium:
        ok = db.execute("SELECT 1 FROM source_medium_rules WHERE source=? AND medium=?",
                        (utm_source, utm_medium)).fetchone()
        if not ok:
            return jsonify({"error":f"Source '{utm_source}' and medium '{utm_medium}' is not an allowed combination."}), 400

    brand_tag = country_code = None
    if gbu:
        r = db.execute("SELECT code FROM taxonomy WHERE kind='gbu' AND value=?", (gbu,)).fetchone()
        brand_tag = r["code"] if r else None
    if country:
        r = db.execute("SELECT code FROM taxonomy WHERE kind='country' AND value=?", (country,)).fetchone()
        country_code = r["code"] if r else None
    utm_content = None
    if brand_tag and country_code: utm_content = f"{brand_tag}_{country_code}"
    elif brand_tag: utm_content = brand_tag
    # Variant suffix injection
    _vsfx = data.get("_variant_suffix")
    if _vsfx and utm_content: utm_content = utm_content + str(_vsfx)
    elif _vsfx: utm_content = str(_vsfx).lstrip("_")

    utm_id_built = build_utm_id(agency, targeting_type, segmentation, asset_type)
    long_url = build_utm_url(destination, utm_source, utm_medium, utm_campaign,
                             utm_term=utm_term, utm_content=utm_content, utm_id=utm_id_built)

    short = gen_short_code()
    now = datetime.utcnow().isoformat(timespec="seconds")+"Z"

    cur = db.execute("""INSERT INTO links(short_code,long_url,destination,utm_source,utm_medium,
                 utm_campaign,utm_term,utm_content,gbu,brand_tag,country,country_code,notes,
                 status,requested_by_email,created_at,approved_at,campaign_id,
                 utm_id_built,agency,targeting_type,segmentation,asset_type)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (short, long_url, destination, utm_source, utm_medium, utm_campaign,
                utm_term, utm_content, gbu, brand_tag, country, country_code, notes,
                "approved", creator_email, now, now, camp["id"],
                utm_id_built, agency, targeting_type, segmentation, asset_type))
    link_id = cur.lastrowid

    db.execute("INSERT INTO link_events(link_id,event_type,email,ts,ip,ua) VALUES(?,?,?,?,?,?)",
               (link_id, "created", creator_email, now,
                request.headers.get("X-Forwarded-For", request.remote_addr),
                request.headers.get("User-Agent","")))
    db.commit()

    ch_name, ch_class = ga4_channel(utm_source, utm_medium, utm_campaign)
    prop = match_domain_to_property(destination)
    ga4 = None
    if prop:
        ga4 = {"property_id": prop["property_id"], "gbu_name": prop["gbu_name"], "domain": prop["domain"], "links": ga4_links(prop["property_id"], utm_campaign)}
    return jsonify({
        "link_id": link_id,
        "long_url": long_url,
        "short_url": f"{public_base()}/s/{short}",
        "short_code": short,
        "utm_content": utm_content,
        "utm_id": utm_id_built,
        "ga4_channel": ch_name,
        "ga4_channel_class": ch_class,
        "ga4_property": ga4,
    })

@app.route("/api/copy-log", methods=["POST"])
def api_copy_log():
    data = request.get_json(force=True)
    link_id = data.get("link_id")
    email = normalize_email(data.get("email"))
    which = (data.get("which") or "short").strip().lower()
    if not link_id: return jsonify({"error":"link_id required"}), 400
    if not email or "@" not in email: return jsonify({"error":"Valid email required"}), 400
    db = get_db()
    if not db.execute("SELECT 1 FROM links WHERE id=?", (link_id,)).fetchone():
        return jsonify({"error":"Link not found"}), 404
    now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
    db.execute("INSERT INTO link_events(link_id,event_type,email,ts,ip,ua) VALUES(?,?,?,?,?,?)",
               (link_id, f"copied_{which}", email, now,
                request.headers.get("X-Forwarded-For", request.remote_addr),
                request.headers.get("User-Agent","")))
    # Backfill creator email if missing
    db.execute("UPDATE links SET requested_by_email=COALESCE(requested_by_email, ?) WHERE id=?",
               (email, link_id))
    db.commit()
    return jsonify({"ok":True})


@app.route("/api/email-link", methods=["POST"])
def api_email_link():
    data = request.get_json(force=True)
    link_id = data.get("link_id")
    email = normalize_email(data.get("email"))
    if not link_id: return jsonify({"error":"link_id required"}), 400
    if not email or "@" not in email: return jsonify({"error":"Valid email required"}), 400
    db = get_db()
    link = db.execute("SELECT * FROM links WHERE id=?", (link_id,)).fetchone()
    if not link: return jsonify({"error":"Link not found"}), 404
    short_url = public_base() + '/s/' + link['short_code']
    body_lines = [
        'Here is the UTM link you generated on the Adecco Group UTM builder.',
        '',
        'Campaign: ' + (link['utm_campaign'] or '-'),
        'Source / Medium: ' + (link['utm_source'] or '-') + ' / ' + (link['utm_medium'] or '-'),
        'Brand / Country: ' + (link['gbu'] or '-') + ' / ' + (link['country'] or '-'),
        'Stage: ' + (link['utm_term'] or '-'),
        'utm_content: ' + (link['utm_content'] or '-'),
        '',
        'Short URL: ' + short_url,
        'Long URL:  ' + link['long_url'],
        'Destination: ' + link['destination'],
        '',
        'QR code: scan or download from ' + public_base() + '/',
        '',
        'Created: ' + link['created_at'],
        '--',
        'The Adecco Group UTM builder',
        public_base() + '/',
    ]
    body = chr(10).join(body_lines)
    ok = send_email(
        subject="[UTM] Your link for " + (link["utm_campaign"] or "campaign"),
        template="link_self.html",
        to=email,
        short_url=short_url, long_url=link["long_url"], destination=link["destination"],
        utm_campaign=link["utm_campaign"], utm_source=link["utm_source"], utm_medium=link["utm_medium"],
        utm_term=link["utm_term"], utm_content=link["utm_content"], utm_id=link["utm_id_built"],
        gbu=link["gbu"], country=link["country"], created_at=link["created_at"],
    )
    now = datetime.utcnow().isoformat(timespec='seconds')+'Z'
    db.execute("INSERT INTO link_events(link_id,event_type,email,ts,ip,ua) VALUES(?,?,?,?,?,?)",
               (link_id, "emailed", email, now,
                request.headers.get("X-Forwarded-For", request.remote_addr),
                request.headers.get("User-Agent","")))
    db.execute("UPDATE links SET requested_by_email=COALESCE(requested_by_email, ?) WHERE id=?", (email, link_id))
    db.commit()
    return jsonify({"ok":True, "sent": bool(ok), "smtp_configured": bool(SMTP_HOST)})

@app.route("/s/<code>")
def shortlink(code):
    db = get_db()
    row = db.execute("SELECT * FROM links WHERE short_code=?", (code,)).fetchone()
    if not row: abort(404)
    if row["status"] != "approved":
        return render_template("pending.html", code=code), 403
    db.execute("UPDATE links SET clicks=clicks+1 WHERE id=?", (row["id"],))
    db.execute("INSERT INTO clicks(link_id,ts,ip,ua,referer) VALUES(?,?,?,?,?)",
               (row["id"], datetime.utcnow().isoformat(timespec="seconds")+"Z",
                request.headers.get("X-Forwarded-For", request.remote_addr),
                request.headers.get("User-Agent",""),
                request.headers.get("Referer","")))
    db.commit()
    return redirect(row["long_url"], code=302)



@app.route("/privacy")
def page_privacy():
    return render_template("privacy.html", base=public_base())

@app.route("/support")
def page_support():
    return render_template("support.html", base=public_base())

@app.route("/extension/submission-kit")
@user_required
def extension_submission_kit():
    """Serve the complete Microsoft Edge Add-ons / Chrome Web Store submission kit."""
    import os as _os
    from flask import send_file
    path = "/app/static/submission-kit.zip"
    if not _os.path.exists(path):
        return "Submission kit not yet built.", 404
    return send_file(path, as_attachment=True, download_name="tag-utm-builder-submission-kit-v1.0.0.zip", mimetype="application/zip")

@app.route("/integrations")
@user_required
def page_integrations():
    return render_template("integrations.html", base=public_base())

@app.route("/routing")
@user_required
def page_routing():
    db = get_db()
    rows = db.execute("SELECT * FROM routing_rules WHERE status != 'deprecated' ORDER BY sort_order, utm_field, destination_system").fetchall()
    field_order = ["utm_source","utm_medium","utm_campaign","utm_term","utm_content","utm_id",
                   "tag_agency","tag_targeting","tag_segmentation","tag_asset"]
    fields = sorted({r["utm_field"] for r in rows},
                    key=lambda f: field_order.index(f) if f in field_order else 99)
    systems = []
    for r in rows:
        if r["destination_system"] not in systems:
            systems.append(r["destination_system"])
    cell = {}
    for r in rows:
        cell.setdefault((r["utm_field"], r["destination_system"]), []).append(r)
    return render_template("routing_matrix.html", rows=rows, fields=fields, systems=systems,
                           cell=cell, base=public_base())

@app.route("/admin/routing", methods=["GET","POST"])
@login_required
def admin_routing():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
        if action == "add":
            f = (request.form.get("utm_field") or "").strip().lower()
            sysname = (request.form.get("destination_system") or "").strip()
            df = (request.form.get("destination_field") or "").strip()
            tr = (request.form.get("transform") or "").strip() or None
            st = (request.form.get("status") or "proposed").strip().lower()
            nt = (request.form.get("notes") or "").strip() or None
            if st not in ("live","proposed","planned","deprecated"): st = "proposed"
            if f and sysname and df:
                db.execute("INSERT INTO routing_rules(utm_field,destination_system,destination_field,transform,status,notes,updated_at,updated_by) VALUES(?,?,?,?,?,?,?,?)",
                           (f, sysname, df, tr, st, nt, now, current_admin_username()))
                db.commit()
        elif action == "status":
            st = (request.form.get("status") or "").strip().lower()
            if st in ("live","proposed","planned","deprecated"):
                db.execute("UPDATE routing_rules SET status=?, updated_at=?, updated_by=? WHERE id=?",
                           (st, now, current_admin_username(), request.form.get("id")))
                db.commit()
        elif action == "delete":
            db.execute("DELETE FROM routing_rules WHERE id=?", (request.form.get("id"),))
            db.commit()
        return redirect(url_for("admin_routing"))
    rules = db.execute("SELECT * FROM routing_rules ORDER BY utm_field, destination_system").fetchall()
    return render_template("admin_routing.html", rules=rules)

@app.route("/healthz")
def healthz(): return "ok", 200

# -------------------- admin --------------------

@app.route("/signup", methods=["GET","POST"])
def public_signup():
    err = None; ok_msg = None
    if request.method == "POST":
        uname = (request.form.get("username") or "").strip().lower()
        email = (request.form.get("email") or "").strip().lower()
        pwd = request.form.get("password") or ""
        full_name = (request.form.get("full_name") or "").strip() or None
        import re as _re
        if not _re.match(r"^[a-z0-9._-]{3,40}$", uname):
            err = "Username: lowercase letters, numbers, dots, underscores, hyphens (3-40 chars)"
        elif not email or "@" not in email:
            err = "Valid work email required"
        elif len(pwd) < 8:
            err = "Password must be at least 8 characters"
        else:
            db = get_db()
            try:
                db.execute("INSERT INTO admins(username,password_hash,role,scope,email,full_name,created_at,created_by) VALUES(?,?,?,?,?,?,?,?)",
                           (uname, generate_password_hash(pwd), "user", "user", email, full_name,
                            datetime.utcnow().isoformat(timespec="seconds")+"Z", "self-signup"))
                db.commit()
                ok_msg = f"Account created. Sign in below."
            except sqlite3.IntegrityError:
                err = f"Username \"{uname}\" or email already exists. Try signing in instead."
    return render_template("signup.html", err=err, ok_msg=ok_msg)

@app.route("/signin", methods=["GET","POST"])
def public_signin():
    """Unified sign-in: routes admin to /admin/inbox, regular user to /"""
    err = None
    if request.method == "POST":
        u = (request.form.get("username") or "").strip().lower()
        p = request.form.get("password") or ""
        db = get_db()
        row = db.execute("SELECT * FROM admins WHERE LOWER(username)=? OR LOWER(email)=?", (u, u)).fetchone()
        if row and check_password_hash(row["password_hash"], p):
            session.clear()
            session["user_id"] = row["id"]
            session["user"] = True
            session["admin_user"] = row["username"]  # keeps backwards compat
            session["username"] = row["username"]
            session["email"] = row["email"] or ""
            session["full_name"] = row["full_name"] or row["username"]
            session["role"] = row["role"] or "admin"
            session["admin_scope"] = row["scope"] or "all"
            session["admin_full_name"] = row["full_name"] or row["username"]
            session["admin_email"] = row["email"] or ""
            if (row["role"] or "admin") == "admin":
                session["admin"] = True
            db.execute("UPDATE admins SET last_login_at=? WHERE id=?", (datetime.utcnow().isoformat(timespec="seconds")+"Z", row["id"]))
            db.commit()
            target = request.args.get("next")
            if not target:
                target = url_for("admin_inbox") if session.get("admin") else url_for("index")
            return redirect(target)
        err = "Sign in failed. Check your username/email and password."
    return render_template("signin.html", err=err)

@app.route("/signout")
def public_signout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/me")
@user_required
def my_profile():
    if not session.get("user"): return redirect(url_for("public_signin", next=request.path))
    db = get_db()
    me = db.execute("SELECT * FROM admins WHERE id=?", (session.get("user_id"),)).fetchone()
    if not me: session.clear(); return redirect(url_for("public_signin"))
    my_links = db.execute("SELECT * FROM links WHERE requested_by_email=? ORDER BY created_at DESC LIMIT 50", (me["email"],)).fetchall()
    return render_template("my_profile.html", me=me, my_links=my_links, base=public_base())

@app.route("/admin/login", methods=["GET","POST"])
@app.route("/login", methods=["GET","POST"])
def admin_login():
    """Backwards-compatible: forward both GET and POST to the unified /signin handler."""
    if request.method == "POST":
        # Process the POST directly so old bookmarks still work
        return public_signin()
    # GET: redirect to canonical /signin (preserves ?next= param)
    nxt = request.args.get("next")
    return redirect(url_for("public_signin", **({"next": nxt} if nxt else {})))

@app.route("/admin/logout")
def admin_logout():
    session.clear(); return redirect(url_for("admin_login"))

@app.route("/admin")
@login_required
def admin_dashboard():
    db = get_db()
    pending_campaigns = db.execute("SELECT * FROM campaigns WHERE status='pending' ORDER BY created_at DESC").fetchall()
    approved_campaigns = db.execute("SELECT c.*, COUNT(l.id) AS link_count FROM campaigns c LEFT JOIN links l ON l.campaign_id=c.id WHERE c.status='approved' GROUP BY c.id ORDER BY c.approved_at DESC").fetchall()
    rejected_campaigns = db.execute("SELECT * FROM campaigns WHERE status='rejected' ORDER BY created_at DESC LIMIT 50").fetchall()
    links = db.execute("""SELECT l.*,
                            (SELECT GROUP_CONCAT(DISTINCT email) FROM link_events WHERE link_id=l.id AND email IS NOT NULL) AS event_emails,
                            (SELECT COUNT(*) FROM link_events WHERE link_id=l.id AND event_type LIKE 'copied%') AS copy_count
                          FROM links l ORDER BY l.created_at DESC LIMIT 300""").fetchall()
    total_clicks = db.execute("SELECT COALESCE(SUM(clicks),0) AS c FROM links").fetchone()["c"]
    return render_template("admin.html",
        pending_campaigns=pending_campaigns, approved_campaigns=approved_campaigns,
        rejected_campaigns=rejected_campaigns, links=links,
        total_clicks=total_clicks, base=public_base())


@app.route("/admin/performance")
@login_required
def admin_performance():
    db = get_db()
    total_links = db.execute("SELECT COUNT(*) AS n FROM links").fetchone()["n"]
    total_clicks = db.execute("SELECT COALESCE(SUM(clicks),0) AS n FROM links").fetchone()["n"]
    total_copies = db.execute("SELECT COUNT(*) AS n FROM link_events WHERE event_type LIKE ?", ("copied%",)).fetchone()["n"]
    total_emails = db.execute("SELECT COUNT(*) AS n FROM link_events WHERE event_type=?", ("emailed",)).fetchone()["n"]
    by_campaign = db.execute("""SELECT utm_campaign, COUNT(*) AS links, COALESCE(SUM(clicks),0) AS clicks
                                  FROM links WHERE utm_campaign IS NOT NULL
                                  GROUP BY utm_campaign ORDER BY clicks DESC, links DESC LIMIT 30""").fetchall()
    by_source = db.execute("""SELECT utm_source, COUNT(*) AS links, COALESCE(SUM(clicks),0) AS clicks
                                FROM links WHERE utm_source IS NOT NULL
                                GROUP BY utm_source ORDER BY clicks DESC LIMIT 20""").fetchall()
    by_medium = db.execute("""SELECT utm_medium, COUNT(*) AS links, COALESCE(SUM(clicks),0) AS clicks
                                FROM links WHERE utm_medium IS NOT NULL
                                GROUP BY utm_medium ORDER BY clicks DESC LIMIT 20""").fetchall()
    by_country = db.execute("""SELECT country, COUNT(*) AS links, COALESCE(SUM(clicks),0) AS clicks
                                  FROM links WHERE country IS NOT NULL
                                  GROUP BY country ORDER BY clicks DESC LIMIT 20""").fetchall()
    by_gbu = db.execute("""SELECT gbu, COUNT(*) AS links, COALESCE(SUM(clicks),0) AS clicks
                              FROM links WHERE gbu IS NOT NULL
                              GROUP BY gbu ORDER BY clicks DESC""").fetchall()
    timeline = db.execute("""SELECT substr(ts,1,10) AS day, COUNT(*) AS clicks FROM clicks
                                 WHERE ts >= date('now','-30 days')
                                 GROUP BY substr(ts,1,10) ORDER BY day""").fetchall()
    # GA4 channel classification across all links
    rows = db.execute("SELECT utm_source, utm_medium, clicks FROM links").fetchall()
    channel_buckets = {}
    for r in rows:
        name, klass = ga4_channel(r["utm_source"], r["utm_medium"])
        b = channel_buckets.setdefault(name, {"links":0, "clicks":0, "class":klass})
        b["links"] += 1; b["clicks"] += (r["clicks"] or 0)
    by_channel = sorted(channel_buckets.items(), key=lambda x: x[1]["clicks"], reverse=True)
    ga4_props_list = db.execute("SELECT * FROM ga4_properties ORDER BY gbu_name").fetchall()
    return render_template("admin_performance.html",
        ga4_properties=ga4_props_list,
        total_links=total_links, total_clicks=total_clicks,
        total_copies=total_copies, total_emails=total_emails,
        by_campaign=by_campaign, by_source=by_source, by_medium=by_medium,
        by_country=by_country, by_gbu=by_gbu,
        by_channel=by_channel, timeline=timeline)


@app.route("/admin/inbox")
@login_required
def admin_inbox():
    db = get_db()
    scope = current_admin_scope()
    if scope == "all":
        pending_campaigns = db.execute("SELECT * FROM campaigns WHERE status='pending' ORDER BY created_at DESC").fetchall()
        pending_suggestions = db.execute("SELECT * FROM suggestions WHERE status='pending' ORDER BY created_at DESC").fetchall()
    else:
        pending_campaigns = db.execute("SELECT * FROM campaigns WHERE status='pending' AND (country_scope=? OR country_scope IS NULL) ORDER BY created_at DESC", (scope,)).fetchall()
        pending_suggestions = db.execute("SELECT * FROM suggestions WHERE status='pending' AND (country_scope=? OR country_scope IS NULL) ORDER BY created_at DESC", (scope,)).fetchall()
    return render_template("admin_inbox.html",
                           pending_campaigns=pending_campaigns,
                           pending_suggestions=pending_suggestions,
                           current_scope=scope)

@app.route("/admin/structure")
@login_required
def admin_structure():
    db = get_db()
    tax = {}
    for kind in ("source","medium","stage","gbu","country","agency","targeting_type","segmentation","asset_type"):
        tax[kind] = db.execute("SELECT * FROM taxonomy WHERE kind=? ORDER BY sort_order,value",(kind,)).fetchall()
    rules = db.execute("SELECT * FROM source_medium_rules ORDER BY source,medium").fetchall()
    comb_rules = db.execute("SELECT * FROM combination_rules ORDER BY source,medium,dimension_kind").fetchall()
    countries = db.execute("SELECT * FROM country_config ORDER BY country_name").fetchall()
    scope = current_admin_scope()
    if scope == "all":
        campaigns = db.execute("SELECT c.*, COUNT(l.id) AS link_count FROM campaigns c LEFT JOIN links l ON l.campaign_id=c.id WHERE c.status='approved' GROUP BY c.id ORDER BY c.name").fetchall()
    else:
        campaigns = db.execute("SELECT c.*, COUNT(l.id) AS link_count FROM campaigns c LEFT JOIN links l ON l.campaign_id=c.id WHERE c.status='approved' AND (c.country_scope=? OR c.country_scope IS NULL) GROUP BY c.id ORDER BY c.name", (scope,)).fetchall()
    sources = [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='source' ORDER BY value").fetchall()]
    mediums = [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='medium' ORDER BY value").fetchall()]
    return render_template("admin_structure.html", tax=tax, rules=rules, comb_rules=comb_rules,
                           countries=countries, campaigns=campaigns,
                           sources=sources, mediums=mediums)


@app.route("/admin/calendar")
@login_required
def admin_calendar():
    import datetime as _dt
    db = get_db()
    year = int(request.args.get("year") or _dt.datetime.utcnow().year)
    rows = db.execute("SELECT id, name, status, start_date, end_date, gbu, owner_email, requested_by_email, description, admin_comment FROM campaigns WHERE status='approved' AND (start_date IS NOT NULL OR end_date IS NOT NULL OR name LIKE '%_' || ? OR name LIKE '%-' || ?) ORDER BY start_date, name", (str(year), str(year))).fetchall()
    all_camps = db.execute("SELECT id, name, status, start_date, end_date, gbu, owner_email, requested_by_email, description, admin_comment, created_at, approved_at FROM campaigns WHERE status='approved' ORDER BY name").fetchall()
    gbus = [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='gbu' ORDER BY sort_order").fetchall()]
    return render_template("admin_calendar.html", year=year, scheduled=rows, all_campaigns=all_camps, gbus=gbus, base=public_base())

@app.route("/admin/campaign/<int:cid>/edit", methods=["POST"])
@login_required
def admin_campaign_edit(cid):
    db = get_db()
    fields = {
        "start_date": (request.form.get("start_date") or "").strip() or None,
        "end_date": (request.form.get("end_date") or "").strip() or None,
        "gbu": (request.form.get("gbu") or "").strip() or None,
        "owner_email": (request.form.get("owner_email") or "").strip().lower() or None,
        "description": (request.form.get("description") or "").strip() or None,
    }
    sets = ", ".join([f"{k}=?" for k in fields])
    db.execute(f"UPDATE campaigns SET {sets} WHERE id=?", list(fields.values()) + [cid])
    db.commit()
    return redirect(request.referrer or url_for("admin_calendar"))


@app.route("/admin/feed")
@login_required
def admin_activity_feed():
    db = get_db()
    scope = current_admin_scope()
    # Build a unified chronological feed from multiple tables
    events = []
    # Campaigns: created + approved + rejected
    if scope == "all":
        camps = db.execute("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT 100").fetchall()
    else:
        camps = db.execute("SELECT * FROM campaigns WHERE country_scope=? OR country_scope IS NULL ORDER BY created_at DESC LIMIT 100", (scope,)).fetchall()
    for c in camps:
        events.append({"ts": c["created_at"], "type": "campaign_requested", "title": "Campaign requested", "desc": c["name"], "actor": c["requested_by_email"] or "anonymous", "meta": {"campaign_id": c["id"], "scope": c["country_scope"]}})
        if c["approved_at"] and c["status"] == "approved":
            events.append({"ts": c["approved_at"], "type": "campaign_approved", "title": "Campaign approved", "desc": c["name"], "actor": "admin", "meta": {"comment": c["admin_comment"]}})
        elif c["status"] == "rejected":
            events.append({"ts": c["created_at"], "type": "campaign_rejected", "title": "Campaign rejected", "desc": c["name"], "actor": "admin", "meta": {"comment": c["admin_comment"]}})
    # Links built (last 200)
    links = db.execute("SELECT * FROM links ORDER BY created_at DESC LIMIT 200").fetchall()
    for l in links:
        events.append({"ts": l["created_at"], "type": "link_built", "title": "Link built", "desc": f"/s/{l['short_code']} → {l['utm_campaign'] or 'no campaign'}", "actor": l["requested_by_email"] or "anonymous", "meta": {"link_id": l["id"], "clicks": l["clicks"], "gbu": l["gbu"]}})
    # Suggestions
    sugs = db.execute("SELECT * FROM suggestions ORDER BY created_at DESC LIMIT 50").fetchall()
    for s in sugs:
        events.append({"ts": s["created_at"], "type": "suggestion_received", "title": "Taxonomy suggestion", "desc": f"{s['kind']}: {s['value']}" + (f" + {s['related_value']}" if s["related_value"] else ""), "actor": s["email"] or "anonymous", "meta": {"status": s["status"], "notes": s["notes"]}})
        if s["status"] == "approved" and s["resolved_at"]:
            events.append({"ts": s["resolved_at"], "type": "suggestion_approved", "title": "Suggestion approved", "desc": f"{s['kind']}: {s['value']}", "actor": "admin", "meta": {}})
    # Click bursts: any link that got >10 clicks in last 7 days, fire an event
    burst = db.execute("""SELECT link_id, COUNT(*) AS c, MAX(ts) AS last_ts FROM clicks WHERE ts >= date('now','-7 days') GROUP BY link_id HAVING COUNT(*) > 10 ORDER BY c DESC LIMIT 10""").fetchall()
    for b in burst:
        l = db.execute("SELECT short_code, utm_campaign FROM links WHERE id=?", (b["link_id"],)).fetchone()
        if l:
            events.append({"ts": b["last_ts"], "type": "click_burst", "title": f"Click burst: {b['c']} clicks/7d", "desc": f"/s/{l['short_code']} → {l['utm_campaign'] or ''}", "actor": "system", "meta": {"count": b["c"]}})
    # Sort by ts desc, take top 100
    events.sort(key=lambda e: e.get("ts","") or "", reverse=True)
    events = events[:120]
    return render_template("admin_activity_feed.html", events=events, scope=scope, base=public_base())

@app.route("/admin/campaigns")
@login_required
def admin_campaigns():
    db = get_db()
    pending = db.execute("SELECT * FROM campaigns WHERE status='pending' ORDER BY created_at DESC").fetchall()
    approved = db.execute("SELECT c.*, COUNT(l.id) AS link_count FROM campaigns c LEFT JOIN links l ON l.campaign_id=c.id WHERE c.status='approved' GROUP BY c.id ORDER BY c.approved_at DESC").fetchall()
    rejected = db.execute("SELECT * FROM campaigns WHERE status='rejected' ORDER BY created_at DESC LIMIT 50").fetchall()
    return render_template("admin_campaigns.html",
                           pending=pending, approved=approved, rejected=rejected)

@app.route("/admin/campaign/<int:cid>/approve", methods=["POST"])
@login_required
def admin_campaign_approve(cid):
    db = get_db()
    now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
    row = db.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    if not row: abort(404)
    scope = current_admin_scope()
    if scope != "all" and row["country_scope"] and row["country_scope"] != scope:
        return "Out of scope: this campaign belongs to " + str(row["country_scope"]), 403
    original_name = row["name"]
    # Allow name edit during approval
    edited_name, err = normalize_campaign_name(request.form.get("name") or original_name)
    if err: return jsonify({"error": err}), 400
    comment = (request.form.get("admin_comment") or "").strip() or None
    name_changed = (edited_name != original_name)
    try:
        if name_changed:
            db.execute("UPDATE campaigns SET name=?, original_name=?, status='approved', approved_at=?, admin_comment=? WHERE id=?",
                       (edited_name, original_name, now, comment, cid))
        else:
            db.execute("UPDATE campaigns SET status='approved', approved_at=?, admin_comment=? WHERE id=?",
                       (now, comment, cid))
        db.commit()
    except sqlite3.IntegrityError:
        return jsonify({"error": f"Campaign name '{edited_name}' already exists. Pick a different name."}), 409
    # Email requester
    if row["requested_by_email"]:
        subj_action = "modified and approved" if name_changed else "approved"
        body_lines = [
            f"Hi,",
            "",
            f"Your campaign request has been {subj_action}.",
            "",
            f"Original request:  {original_name}",
            f"Final name:        {edited_name}" + ("  (renamed by admin)" if name_changed else ""),
            "",
        ]
        if comment:
            body_lines += ["Admin comment:", comment, ""]
        body_lines += [
            f"You can now use this campaign in the builder:",
            f"{public_base()}/",
            "",
            "Cheers,",
            "the Adecco Group UTM builder",
        ]
        send_email(
            subject=f"[UTM] Campaign {subj_action}: {edited_name}",
            template="campaign_approved.html",
            to=row["requested_by_email"],
            original_name=original_name, final_name=edited_name, renamed=name_changed, comment=comment,
        )
    # Notify SIBLING admins that this was already approved (so they don't act on the same request)
    try:
        actor = current_admin_username()
        actor_email = session.get("admin_email") or session.get("email") or ""
        siblings = [e for e in ADMIN_EMAILS if e and e.lower() != (actor_email or "").lower()]
        if siblings:
            send_email(
                subject=f"[UTM] FYI: campaign \"{edited_name}\" was already approved by {actor}",
                template="campaign_sibling_notice.html",
                to=siblings,
                final_name=edited_name, original_name=original_name, action="approved",
                actor=actor, actor_email=actor_email, comment=comment, requested_by=row["requested_by_email"],
            )
    except Exception as _e:
        print(f"[sibling notice fail] {_e}", flush=True)
    return redirect(request.referrer or url_for("admin_inbox"))

@app.route("/admin/campaign/<int:cid>/reject", methods=["POST"])
@login_required
def admin_campaign_reject(cid):
    db = get_db()
    row = db.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    if not row: abort(404)
    comment = (request.form.get("admin_comment") or "").strip() or None
    db.execute("UPDATE campaigns SET status='rejected', admin_comment=? WHERE id=?", (comment, cid))
    db.commit()
    if row["requested_by_email"]:
        body_lines = [
            "Hi,",
            "",
            f"Your campaign request '{row['name']}' was not approved.",
            "",
        ]
        if comment:
            body_lines += ["Reason:", comment, ""]
        else:
            body_lines += ["No specific reason was given. Please contact the admin if you want to discuss.", ""]
        body_lines += [
            "If you want to submit a revised name, you can do so from the builder at",
            f"{public_base()}/",
            "",
            "Cheers,",
            "the Adecco Group UTM builder",
        ]
        send_email(
            subject=f"[UTM] Campaign rejected: " + row["name"],
            template="campaign_rejected.html",
            to=row["requested_by_email"],
            name=row["name"], comment=comment,
        )
    # Notify sibling admins
    try:
        actor = current_admin_username()
        actor_email = session.get("admin_email") or session.get("email") or ""
        siblings = [e for e in ADMIN_EMAILS if e and e.lower() != (actor_email or "").lower()]
        if siblings:
            send_email(
                subject=f"[UTM] FYI: campaign \"{row['name']}\" was already rejected by {actor}",
                template="campaign_sibling_notice.html",
                to=siblings,
                final_name=row["name"], original_name=row["name"], action="rejected",
                actor=actor, actor_email=actor_email, comment=comment, requested_by=row["requested_by_email"],
            )
    except Exception as _e:
        print(f"[sibling notice fail] {_e}", flush=True)
    return redirect(request.referrer or url_for("admin_inbox"))

@app.route("/admin/campaign/<int:cid>/delete", methods=["POST"])
@login_required
def admin_campaign_delete(cid):
    db = get_db()
    db.execute("DELETE FROM campaigns WHERE id=?", (cid,))
    db.commit()
    return redirect(request.referrer or url_for("admin_campaigns"))

@app.route("/admin/campaign/add", methods=["POST"])
@login_required
def admin_campaign_add():
    db = get_db()
    name, err = normalize_campaign_name(request.form.get("name"))
    scope = current_admin_scope()
    creator_scope = "global" if scope == "all" else scope
    if name and not err:
        now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
        try:
            db.execute("INSERT INTO campaigns(name,status,requested_by_email,created_at,approved_at,country_scope) VALUES(?,?,?,?,?,?)",
                       (name, "approved", current_admin_username(), now, now, creator_scope))
            db.commit()
        except sqlite3.IntegrityError:
            pass
    return redirect(request.referrer or url_for("admin_campaigns"))


@app.route("/admin/combination-rules", methods=["GET","POST"])
@login_required
def admin_comb_rules():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            s = (request.form.get("source") or "").strip().lower()
            m = (request.form.get("medium") or "").strip().lower()
            k = (request.form.get("dimension_kind") or "").strip().lower()
            vals = (request.form.get("allowed_values") or "").strip()
            required = 1 if request.form.get("required") else 0
            import json as _j
            if vals:
                lst = [v.strip().lower() for v in vals.replace(",", " ").split() if v.strip()]
                vals_json = _j.dumps(lst)
            else:
                vals_json = None
            if s and m and k in ("agency","targeting_type","segmentation","asset_type"):
                try:
                    db.execute("INSERT OR REPLACE INTO combination_rules(source,medium,dimension_kind,allowed_values_json,required) VALUES(?,?,?,?,?)",
                               (s, m, k, vals_json, required))
                    db.commit()
                except sqlite3.IntegrityError: pass
        elif action == "delete":
            db.execute("DELETE FROM combination_rules WHERE id=?", (request.form.get("id"),))
            db.commit()
        return redirect(url_for("admin_comb_rules"))
    rules = db.execute("SELECT * FROM combination_rules ORDER BY source, medium, dimension_kind").fetchall()
    sources = [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='source' ORDER BY value").fetchall()]
    mediums = [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='medium' ORDER BY value").fetchall()]
    return render_template("admin_comb_rules.html", rules=rules, sources=sources, mediums=mediums)

@app.route("/admin/rules", methods=["GET","POST"])
@login_required
def admin_rules():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            src = (request.form.get("source") or "").strip().lower()
            med = (request.form.get("medium") or "").strip().lower()
            if src and med:
                # Make sure both exist in taxonomy
                db.execute("INSERT OR IGNORE INTO taxonomy(kind,value) VALUES('source',?)",(src,))
                db.execute("INSERT OR IGNORE INTO taxonomy(kind,value) VALUES('medium',?)",(med,))
                try:
                    db.execute("INSERT INTO source_medium_rules(source,medium) VALUES(?,?)",(src,med))
                    db.commit()
                except sqlite3.IntegrityError: pass
        elif action == "delete":
            db.execute("DELETE FROM source_medium_rules WHERE id=?", (request.form.get("id"),))
            db.commit()
        return redirect(url_for("admin_rules"))
    sources = [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='source' ORDER BY value").fetchall()]
    mediums = [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind='medium' ORDER BY value").fetchall()]
    sm_map = get_source_medium_map(db)
    rules = db.execute("SELECT * FROM source_medium_rules ORDER BY source,medium").fetchall()
    return render_template("admin_rules.html", sources=sources, mediums=mediums,
                           sm_map=sm_map, rules=rules)

@app.route("/admin/link/<int:lid>/events")
@login_required
def admin_link_events(lid):
    db = get_db()
    link = db.execute("SELECT * FROM links WHERE id=?", (lid,)).fetchone()
    events = db.execute("SELECT * FROM link_events WHERE link_id=? ORDER BY ts DESC", (lid,)).fetchall()
    if not link: abort(404)
    return render_template("admin_link_events.html", link=link, events=events, base=public_base())

@app.route("/admin/link/<int:lid>/delete", methods=["POST"])
@login_required
def admin_delete(lid):
    db = get_db()
    db.execute("DELETE FROM links WHERE id=?", (lid,))
    db.commit()
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/api/suggest", methods=["POST"])
def api_suggest():
    data = request.get_json(force=True)
    kind = (data.get("kind") or "").strip().lower()
    raw_value = (data.get("value") or "").strip()
    value = raw_value if kind == "other" else raw_value.lower()
    related = (data.get("related_value") or "").strip().lower() or None
    email = normalize_email(data.get("email"))
    notes = (data.get("notes") or "").strip() or None
    if kind not in ("source","medium","source_medium","stage","agency","targeting_type","segmentation","asset_type","other"):
        return jsonify({"error":"Invalid kind."}), 400
    if not value: return jsonify({"error":"Value required"}), 400
    if not email or "@" not in email: return jsonify({"error":"Valid email required for suggestions"}), 400
    import re as _re
    if kind != "other" and not _re.match(r"^[a-z0-9_-]{2,60}$", value):
        return jsonify({"error":"Value must be lowercase letters, numbers, underscore or hyphen. 2 to 60 chars."}), 400
    if kind == "other" and len(value) < 4:
        return jsonify({"error":"Describe your suggestion in at least 4 characters."}), 400
    if kind == "source_medium" and (not related or not _re.match(r"^[a-z0-9_-]{2,60}$", related)):
        return jsonify({"error":"For a source+medium pair, both values must be lowercase a-z 0-9 _ -."}), 400
    db = get_db()
    now = datetime.utcnow().isoformat(timespec='seconds')+'Z'
    db.execute("INSERT INTO suggestions(kind,value,related_value,email,notes,status,created_at) VALUES(?,?,?,?,?,?,?)",
               (kind, value, related, email, notes, "pending", now))
    db.commit()
    desc = kind + ': ' + value + (' + ' + related if related else '')
    body_lines = [
        'A new taxonomy suggestion is waiting for review.',
        '',
        'Kind: ' + kind,
        'Value: ' + value,
        'Related: ' + (related or '-'),
        'Requested by: ' + email,
        'Notes: ' + (notes or '-'),
        '',
        'Review at: ' + public_base() + '/admin/suggestions',
    ]
    send_email(subject="[UTM] New taxonomy suggestion: " + desc,
        template="suggestion_received.html",
        to=ADMIN_EMAIL,
        kind=kind, value=value, related=related, email=email, notes=notes,
    )
    return jsonify({"ok":True, "kind":kind, "value":value, "status":"pending"})

@app.route("/admin/suggestions")
@login_required
def admin_suggestions():
    db = get_db()
    pending = db.execute("SELECT * FROM suggestions WHERE status='pending' ORDER BY created_at DESC").fetchall()
    resolved = db.execute("SELECT * FROM suggestions WHERE status!='pending' ORDER BY resolved_at DESC LIMIT 100").fetchall()
    return render_template("admin_suggestions.html", pending=pending, resolved=resolved)

@app.route("/admin/suggestion/<int:sid>/approve", methods=["POST"])
@login_required
def admin_suggestion_approve(sid):
    db = get_db()
    s = db.execute("SELECT * FROM suggestions WHERE id=?", (sid,)).fetchone()
    if not s: abort(404)
    scope = current_admin_scope()
    if scope != "all" and s["country_scope"] and s["country_scope"] != scope:
        return "Out of scope: this suggestion belongs to " + str(s["country_scope"]), 403
    now = datetime.utcnow().isoformat(timespec='seconds')+'Z'
    if s["kind"] in ("source","medium","stage"):
        db.execute("INSERT OR IGNORE INTO taxonomy(kind,value) VALUES(?,?)", (s["kind"], s["value"]))
    elif s["kind"] == "source_medium":
        db.execute("INSERT OR IGNORE INTO taxonomy(kind,value) VALUES('source',?)", (s['value'],))
        db.execute("INSERT OR IGNORE INTO taxonomy(kind,value) VALUES('medium',?)", (s['related_value'],))
        try:
            db.execute("INSERT INTO source_medium_rules(source,medium) VALUES(?,?)", (s["value"], s["related_value"]))
        except sqlite3.IntegrityError: pass
    db.execute("UPDATE suggestions SET status='approved', resolved_at=? WHERE id=?", (now, sid))
    db.commit()
    if s["email"]:
        desc = s['kind'] + ': ' + s['value'] + (' + ' + s['related_value'] if s['related_value'] else '')
        send_email(subject="[UTM] Your taxonomy suggestion was approved",
            template="suggestion_approved.html",
            to=s["email"],
            kind=s["kind"], value=s["value"], related=s["related_value"],
        )
    return redirect(request.referrer or url_for("admin_inbox"))

@app.route("/admin/suggestion/<int:sid>/reject", methods=["POST"])
@login_required
def admin_suggestion_reject(sid):
    db = get_db()
    now = datetime.utcnow().isoformat(timespec='seconds')+'Z'
    db.execute("UPDATE suggestions SET status='rejected', resolved_at=? WHERE id=?", (now, sid))
    db.commit()
    return redirect(request.referrer or url_for("admin_inbox"))

@app.route("/admin/suggestion/<int:sid>/delete", methods=["POST"])
@login_required
def admin_suggestion_delete(sid):
    db = get_db()
    db.execute("DELETE FROM suggestions WHERE id=?", (sid,))
    db.commit()
    return redirect(request.referrer or url_for("admin_inbox"))


@app.route("/admin/countries", methods=["GET","POST"])
@login_required
def admin_countries():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save":
            code = (request.form.get("country_code") or "").strip().lower()
            name = (request.form.get("country_name") or "").strip()
            cfg = (request.form.get("config_json") or "{}").strip()
            import json as _j
            try: _j.loads(cfg)
            except Exception: cfg = "{}"
            now = datetime.utcnow().isoformat(timespec="seconds")+"Z"
            db.execute("INSERT OR REPLACE INTO country_config(country_code,country_name,config_json,updated_at) VALUES(?,?,?,?)",
                       (code, name, cfg, now))
            db.commit()
        elif action == "delete":
            db.execute("DELETE FROM country_config WHERE country_code=?", (request.form.get("country_code"),))
            db.commit()
        return redirect(url_for("admin_countries"))
    rows = db.execute("SELECT * FROM country_config ORDER BY country_name").fetchall()
    countries = db.execute("SELECT value AS country_name, code AS country_code FROM taxonomy WHERE kind='country' ORDER BY sort_order").fetchall()
    return render_template("admin_countries.html", configs=rows, countries=countries)


@app.route("/admin/users", methods=["GET","POST"])
@login_required
def admin_users():
    if not can_manage_users(): abort(403)
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            uname = (request.form.get("username") or "").strip().lower()
            pwd = request.form.get("password") or ""
            scope = (request.form.get("scope") or "all").strip().lower()
            email = (request.form.get("email") or "").strip().lower() or None
            full_name = (request.form.get("full_name") or "").strip() or None
            import re as _re
            if not _re.match(r"^[a-z0-9._-]{3,40}$", uname):
                return "Username must be lowercase letters, numbers, dots, underscores, hyphens (3-40 chars)", 400
            if len(pwd) < 8:
                return "Password must be at least 8 characters", 400
            if scope not in ("all","global","de","ch","it","fr","es","at","be","nl","gb","us"):
                return "Invalid scope", 400
            try:
                db.execute("INSERT INTO admins(username,password_hash,scope,email,full_name,created_at,created_by) VALUES(?,?,?,?,?,?,?)",
                           (uname, generate_password_hash(pwd), scope, email, full_name, datetime.utcnow().isoformat(timespec="seconds")+"Z", current_admin_username()))
                db.commit()
            except sqlite3.IntegrityError:
                return f"Username \"{uname}\" already exists", 409
        elif action == "delete":
            uid = request.form.get("id")
            row = db.execute("SELECT username FROM admins WHERE id=?", (uid,)).fetchone()
            if row and row["username"] == current_admin_username():
                return "You cannot delete your own account while signed in", 400
            db.execute("DELETE FROM admins WHERE id=?", (uid,))
            db.commit()
        elif action == "reset_password":
            uid = request.form.get("id")
            new_pwd = request.form.get("new_password") or ""
            if len(new_pwd) < 8: return "Password must be at least 8 characters", 400
            db.execute("UPDATE admins SET password_hash=? WHERE id=?", (generate_password_hash(new_pwd), uid))
            db.commit()
        elif action == "change_scope":
            uid = request.form.get("id")
            new_scope = (request.form.get("scope") or "").strip().lower()
            if new_scope in ("all","global","de","ch","it","fr","es","at","be","nl","gb","us"):
                db.execute("UPDATE admins SET scope=? WHERE id=?", (new_scope, uid))
                db.commit()
        return redirect(url_for("admin_users"))
    rows = db.execute("SELECT * FROM admins ORDER BY role DESC, scope, username").fetchall()
    return render_template("admin_users.html", admins=rows, me=current_admin_username())


@app.route("/admin/ga4", methods=["GET","POST"])
@login_required
def admin_ga4():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            gbu = (request.form.get("gbu_name") or "").strip()
            dom = (request.form.get("domain") or "").strip().lower().lstrip(".")
            pid = (request.form.get("property_id") or "").strip()
            mid = (request.form.get("measurement_id") or "").strip() or None
            notes = (request.form.get("notes") or "").strip() or None
            if dom and pid:
                try:
                    db.execute("INSERT OR REPLACE INTO ga4_properties(gbu_name,domain,property_id,measurement_id,notes,is_master,created_at) VALUES(?,?,?,?,?,1,?)",
                               (gbu, dom, pid, mid, notes, datetime.utcnow().isoformat(timespec="seconds")+"Z"))
                    db.commit()
                except sqlite3.IntegrityError: pass
        elif action == "delete":
            db.execute("DELETE FROM ga4_properties WHERE id=?", (request.form.get("id"),))
            db.commit()
        return redirect(url_for("admin_ga4"))
    props = db.execute("SELECT * FROM ga4_properties ORDER BY gbu_name").fetchall()
    return render_template("admin_ga4.html", props=props)


@app.route("/api/templates")
def api_templates():
    scope = (request.args.get("scope") or "global").strip().lower()
    db = get_db()
    rows = db.execute("SELECT id, name, description, preset_json FROM templates WHERE is_active=1 AND (country_scope=? OR country_scope='global') ORDER BY name", (scope,)).fetchall()
    import json as _j
    out = []
    for r in rows:
        try: preset = _j.loads(r["preset_json"])
        except Exception: preset = {}
        out.append({"id":r["id"], "name":r["name"], "description":r["description"], "preset":preset})
    return jsonify(out)

@app.route("/admin/templates", methods=["GET","POST"])
@login_required
def admin_templates():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            name = (request.form.get("name") or "").strip()
            desc = (request.form.get("description") or "").strip() or None
            scope = (request.form.get("country_scope") or "global").strip().lower()
            preset = {}
            for f in ["utm_source","utm_medium","utm_term","utm_campaign","gbu","country","agency","targeting_type","segmentation","asset_type"]:
                v = (request.form.get(f) or "").strip()
                if v: preset[f] = v
            import json as _j
            if name and preset:
                db.execute("INSERT INTO templates(name,description,preset_json,country_scope,created_by,created_at) VALUES(?,?,?,?,?,?)",
                           (name, desc, _j.dumps(preset), scope, current_admin_username(), datetime.utcnow().isoformat(timespec="seconds")+"Z"))
                db.commit()
        elif action == "delete":
            db.execute("DELETE FROM templates WHERE id=?", (request.form.get("id"),))
            db.commit()
        elif action == "toggle":
            tid = request.form.get("id")
            db.execute("UPDATE templates SET is_active = 1-is_active WHERE id=?", (tid,))
            db.commit()
        return redirect(url_for("admin_templates"))
    rows = db.execute("SELECT * FROM templates ORDER BY country_scope, name").fetchall()
    # Get taxonomy lists for the add-form
    tax = {}
    for kind in ("source","medium","stage","gbu","country","agency","targeting_type","segmentation","asset_type"):
        tax[kind] = [r["value"] for r in db.execute("SELECT value FROM taxonomy WHERE kind=? ORDER BY value",(kind,)).fetchall()]
    return render_template("admin_templates.html", templates=rows, tax=tax)


@app.route("/admin/promote/<int:uid>", methods=["POST"])
@login_required
def admin_promote(uid):
    if not can_manage_users(): abort(403)
    new_role = request.form.get("role") or "admin"
    new_scope = request.form.get("scope") or "all"
    if new_role not in ("user","admin"): return "Invalid role", 400
    db = get_db()
    db.execute("UPDATE admins SET role=?, scope=? WHERE id=?", (new_role, new_scope if new_role == "admin" else "user", uid))
    db.commit()
    return redirect(request.referrer or url_for("admin_users"))


@app.route("/admin/links/bulk-delete", methods=["POST"])
@login_required
def admin_bulk_delete():
    data = request.get_json(force=True) or {}
    ids = data.get("ids") or []
    db = get_db()
    ids_int = [int(i) for i in ids if str(i).isdigit()]
    if not ids_int: return jsonify({"error":"no ids"}), 400
    placeholders = ",".join("?" * len(ids_int))
    db.execute(f"DELETE FROM links WHERE id IN ({placeholders})", ids_int)
    db.commit()
    return jsonify({"deleted": len(ids_int)})

@app.route("/admin/links/bulk-export")
@login_required
def admin_bulk_export():
    ids_param = (request.args.get("ids") or "").strip()
    if not ids_param: return "no ids", 400
    ids_int = [int(i) for i in ids_param.split(",") if i.isdigit()]
    if not ids_int: return "no valid ids", 400
    db = get_db()
    placeholders = ",".join("?" * len(ids_int))
    rows = db.execute(f"SELECT * FROM links WHERE id IN ({placeholders}) ORDER BY created_at DESC", ids_int).fetchall()
    headers = ["id","short_code","short_url","long_url","destination","utm_source","utm_medium","utm_campaign","utm_term","utm_content","gbu","country","status","clicks","requested_by_email","created_at"]
    out = [",".join(headers)]
    for r in rows:
        vals = [str(r["id"]), r["short_code"], f"{public_base()}/s/{r['short_code']}",
                r["long_url"], r["destination"] or "", r["utm_source"] or "",
                r["utm_medium"] or "", r["utm_campaign"] or "", r["utm_term"] or "",
                r["utm_content"] or "", r["gbu"] or "", r["country"] or "",
                r["status"], str(r["clicks"]), r["requested_by_email"] or "", r["created_at"]]
        out.append(",".join("\"" + v.replace("\"","\"\"") + "\"" for v in vals))
    return Response(chr(10).join(out), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=utm-links-export.csv"})

@app.route("/admin/taxonomy", methods=["GET","POST"])
@login_required
def admin_taxonomy():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            kind = request.form.get("kind")
            value = (request.form.get("value") or "").strip()
            code = (request.form.get("code") or "").strip().lower() or None
            # Lowercase the value too, except gbu/country labels which may be Title Case ("United States")
            if kind in ("source","medium","stage"):
                value = value.lower()
            if kind and value:
                try:
                    db.execute("INSERT INTO taxonomy(kind,value,code) VALUES(?,?,?)",(kind,value,code))
                    db.commit()
                except sqlite3.IntegrityError: pass
        elif action == "delete":
            db.execute("DELETE FROM taxonomy WHERE id=?", (request.form.get("id"),))
            db.commit()
        return redirect(url_for("admin_taxonomy"))
    tax = {}
    for kind in ("source","medium","stage","gbu","country","agency","targeting_type","segmentation","asset_type"):
        tax[kind] = db.execute("SELECT * FROM taxonomy WHERE kind=? ORDER BY sort_order,value",(kind,)).fetchall()
    return render_template("taxonomy.html", tax=tax)

@app.route("/admin/export.csv")
@login_required
def admin_export():
    db = get_db()
    rows = db.execute("SELECT * FROM links ORDER BY created_at DESC").fetchall()
    headers = ["id","short_code","short_url","long_url","destination","utm_source","utm_medium",
               "utm_campaign","utm_term","utm_content","gbu","country","status","clicks",
               "requested_by_email","created_at"]
    out = [",".join(headers)]
    for r in rows:
        vals = [str(r["id"]), r["short_code"], f"{public_base()}/s/{r['short_code']}",
                r["long_url"], r["destination"] or "", r["utm_source"] or "",
                r["utm_medium"] or "", r["utm_campaign"] or "", r["utm_term"] or "",
                r["utm_content"] or "", r["gbu"] or "", r["country"] or "",
                r["status"], str(r["clicks"]), r["requested_by_email"] or "", r["created_at"]]
        out.append(",".join('"' + v.replace('"','""') + '"' for v in vals))
    return Response("\n".join(out), mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=utm-links.csv"})

with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","8000")), debug=False)
