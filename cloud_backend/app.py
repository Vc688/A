import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from html import escape
import importlib.util
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import boto3
import jwt
import psycopg
import requests
import stripe
from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
LEGACY_APP_PATH = BACKEND_DIR / "legacy_reference_app.py"
legacy_spec = importlib.util.spec_from_file_location("legacy_reference_app", LEGACY_APP_PATH)
legacy_app = importlib.util.module_from_spec(legacy_spec)
assert legacy_spec and legacy_spec.loader
legacy_spec.loader.exec_module(legacy_app)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


load_env_file(BACKEND_DIR / ".env")

DATA_DIR = BACKEND_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
STORAGE_DIR = BACKEND_DIR / "storage"
STORAGE_DIR.mkdir(exist_ok=True)
UPLOAD_DIR = STORAGE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
BACKGROUND_DIR = STORAGE_DIR / "backgrounds"
BACKGROUND_DIR.mkdir(exist_ok=True)
EXPORT_DIR = STORAGE_DIR / "exports"
EXPORT_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "app.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip().lower()
S3_BUCKET = os.getenv("S3_BUCKET", "").strip()
S3_REGION = os.getenv("S3_REGION", "").strip()
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "").strip() or None
S3_PREFIX = os.getenv("S3_PREFIX", "torah-center").strip().strip("/")
WEB_BASE_URL = os.getenv("WEB_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
WORKER_MODE = os.getenv("WORKER_MODE", "inline").strip().lower()
ENABLE_RENDER_DEPLOYMENT = os.getenv("ENABLE_RENDER_DEPLOYMENT", "1").strip().lower() in {"1", "true", "yes", "on"}

APP_SECRET = os.getenv("MOBILE_APP_SECRET", "change-this-secret")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@torahcenter.app")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")
TOKEN_MAX_AGE_SECONDS = int(os.getenv("TOKEN_MAX_AGE_SECONDS", "604800"))
PRODUCT_NAME = os.getenv("PRODUCT_NAME", "Torah Center")
SHUL_NAME = os.getenv("SHUL_NAME", PRODUCT_NAME)
DEFAULT_HEADER_LEFT = os.getenv("DEFAULT_PDF_HEADER_LEFT", PRODUCT_NAME)
DEFAULT_HEADER_RIGHT = os.getenv("DEFAULT_PDF_HEADER_RIGHT", "Torah from our Rabbis")
DEFAULT_FOOTER_TEXT = os.getenv("DEFAULT_PDF_FOOTER_TEXT", PRODUCT_NAME)
LETTER_WIDTH = 612.0
LETTER_HEIGHT = 792.0
SKILL_SOURCE_LIMIT_BYTES = 50 * 1024 * 1024
TERMS_LIBRARY_PATH = DATA_DIR / "hebrew_terms_library.json"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", getattr(legacy_app, "OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", str(getattr(legacy_app, "OPENAI_TIMEOUT_SECONDS", 1800))))
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", getattr(legacy_app, "TRANSCRIBE_MODEL", "gpt-4o-transcribe"))
REVIEW_MODEL = os.getenv("REVIEW_MODEL", getattr(legacy_app, "REVIEW_MODEL", "gpt-4.1-mini"))
ARTICLE_MODEL = os.getenv("ARTICLE_MODEL", os.getenv("PAMPHLET_MODEL", getattr(legacy_app, "PAMPHLET_MODEL", "gpt-4.1")))
TRANSCRIBE_CHUNK_SECONDS = int(os.getenv("TRANSCRIBE_CHUNK_SECONDS", str(getattr(legacy_app, "TRANSCRIBE_CHUNK_SECONDS", 1200))))
CLERK_PUBLISHABLE_KEY = os.getenv("CLERK_PUBLISHABLE_KEY", "").strip()
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY", "").strip()
CLERK_FRONTEND_API_URL = os.getenv("CLERK_FRONTEND_API_URL", "").strip().rstrip("/")
CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL", "").strip()
CLERK_AFTER_SIGN_IN_URL = os.getenv("CLERK_AFTER_SIGN_IN_URL", WEB_BASE_URL)
CLERK_AFTER_SIGN_UP_URL = os.getenv("CLERK_AFTER_SIGN_UP_URL", WEB_BASE_URL)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_CURRENCY = os.getenv("STRIPE_CURRENCY", "usd").strip().lower()
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", f"{WEB_BASE_URL}/?billing=success")
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", f"{WEB_BASE_URL}/?billing=cancel")
SINGLE_UNLOCK_PRICE_CENTS = int(os.getenv("SINGLE_UNLOCK_PRICE_CENTS", "500"))
MONTHLY_PLAN_PRICE_CENTS = int(os.getenv("MONTHLY_PLAN_PRICE_CENTS", "1500"))
CLERK_ENABLED = bool(CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY)
STRIPE_ENABLED = bool(STRIPE_SECRET_KEY)
jwt_jwks_cache: dict[str, tuple[float, dict]] = {}

legacy_app.OPENAI_API_KEY = OPENAI_API_KEY or getattr(legacy_app, "OPENAI_API_KEY", "")
legacy_app.OPENAI_BASE_URL = OPENAI_BASE_URL
legacy_app.OPENAI_TIMEOUT_SECONDS = OPENAI_TIMEOUT_SECONDS
legacy_app.TRANSCRIBE_MODEL = TRANSCRIBE_MODEL
legacy_app.REVIEW_MODEL = REVIEW_MODEL
legacy_app.PAMPHLET_MODEL = ARTICLE_MODEL
legacy_app.TRANSCRIBE_CHUNK_SECONDS = TRANSCRIBE_CHUNK_SECONDS
if STRIPE_ENABLED:
    stripe.api_key = STRIPE_SECRET_KEY

app = Flask(__name__, template_folder=str(BACKEND_DIR / "templates"))
app.secret_key = APP_SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"},
)
serializer = URLSafeTimedSerializer(APP_SECRET)


def sql(query: str) -> str:
    if not USE_POSTGRES:
        return query
    return query.replace("?", "%s")


class DBConnection:
    def __init__(self):
        self.is_postgres = USE_POSTGRES
        if self.is_postgres:
            self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        else:
            self.conn = sqlite3.connect(DB_PATH)
            self.conn.row_factory = sqlite3.Row

    def execute(self, query: str, params=None):
        return self.conn.execute(sql(query), params or ())

    def executescript(self, script: str):
        if not self.is_postgres:
            return self.conn.executescript(script)
        results = []
        for statement in [part.strip() for part in script.split(";") if part.strip()]:
            results.append(self.conn.execute(statement))
        return results

    def commit(self):
        return self.conn.commit()

    def close(self):
        return self.conn.close()


def s3_client():
    if STORAGE_BACKEND != "s3":
        return None
    session_kwargs = {}
    if S3_REGION:
        session_kwargs["region_name"] = S3_REGION
    return boto3.client("s3", endpoint_url=S3_ENDPOINT_URL, **session_kwargs)


def storage_key(*parts: str) -> str:
    clean_parts = [part.strip("/\\") for part in parts if part]
    if S3_PREFIX:
        clean_parts.insert(0, S3_PREFIX)
    return "/".join(clean_parts)


def db() -> DBConnection:
    return DBConnection()


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def init_db() -> None:
    conn = db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY,
              clerk_user_id TEXT,
              email TEXT NOT NULL UNIQUE,
              password_hash TEXT,
              stripe_customer_id TEXT,
              stripe_subscription_id TEXT,
              subscription_status TEXT NOT NULL DEFAULT 'inactive',
              subscription_current_period_end TEXT,
              role TEXT NOT NULL DEFAULT 'member',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              status TEXT NOT NULL,
              progress INTEGER NOT NULL,
              message TEXT NOT NULL,
              rabbi_name TEXT NOT NULL,
              topic TEXT NOT NULL,
              raw_transcript TEXT,
              final_transcript TEXT,
              one_pager TEXT,
              edited_one_pager TEXT,
              review_status TEXT NOT NULL,
              review_items TEXT NOT NULL,
              voice_profile TEXT,
              transliteration_mode TEXT NOT NULL DEFAULT 'auto',
              pdf_line_spacing REAL NOT NULL,
              pdf_font_size REAL NOT NULL,
              pdf_background_mode TEXT NOT NULL,
              pdf_custom_background TEXT,
              pdf_header_left TEXT,
              pdf_header_right TEXT,
              pdf_footer_text TEXT,
              pdf_header_left_size REAL NOT NULL DEFAULT 15,
              pdf_header_right_size REAL NOT NULL DEFAULT 14,
              pdf_footer_size REAL NOT NULL DEFAULT 10,
              pdf_header_left_align TEXT NOT NULL DEFAULT 'left',
              pdf_header_right_align TEXT NOT NULL DEFAULT 'right',
              pdf_footer_align TEXT NOT NULL DEFAULT 'center',
              pdf_body_align TEXT NOT NULL DEFAULT 'left',
              pdf_header_y REAL NOT NULL DEFAULT 738,
              pdf_footer_y REAL NOT NULL DEFAULT 30,
              source_kind TEXT,
              source_path TEXT,
              transcript_input TEXT,
              pamphlet_input TEXT,
              billing_state TEXT NOT NULL DEFAULT 'locked',
              stripe_checkout_session_id TEXT,
              stripe_payment_intent_id TEXT,
              unlocked_at TEXT,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        if USE_POSTGRES:
            existing_columns = {
                row["column_name"]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                    ("jobs",),
                ).fetchall()
            }
            existing_user_columns = {
                row["column_name"]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                    ("users",),
                ).fetchall()
            }
        else:
            existing_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            existing_user_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(users)").fetchall()
            }
        migrations = {
            "voice_profile": "ALTER TABLE jobs ADD COLUMN voice_profile TEXT",
            "transliteration_mode": "ALTER TABLE jobs ADD COLUMN transliteration_mode TEXT NOT NULL DEFAULT 'auto'",
            "pdf_header_left": "ALTER TABLE jobs ADD COLUMN pdf_header_left TEXT",
            "pdf_header_right": "ALTER TABLE jobs ADD COLUMN pdf_header_right TEXT",
            "pdf_footer_text": "ALTER TABLE jobs ADD COLUMN pdf_footer_text TEXT",
            "pdf_header_left_size": "ALTER TABLE jobs ADD COLUMN pdf_header_left_size REAL NOT NULL DEFAULT 15",
            "pdf_header_right_size": "ALTER TABLE jobs ADD COLUMN pdf_header_right_size REAL NOT NULL DEFAULT 14",
            "pdf_footer_size": "ALTER TABLE jobs ADD COLUMN pdf_footer_size REAL NOT NULL DEFAULT 10",
            "pdf_header_left_align": "ALTER TABLE jobs ADD COLUMN pdf_header_left_align TEXT NOT NULL DEFAULT 'left'",
            "pdf_header_right_align": "ALTER TABLE jobs ADD COLUMN pdf_header_right_align TEXT NOT NULL DEFAULT 'right'",
            "pdf_footer_align": "ALTER TABLE jobs ADD COLUMN pdf_footer_align TEXT NOT NULL DEFAULT 'center'",
            "pdf_body_align": "ALTER TABLE jobs ADD COLUMN pdf_body_align TEXT NOT NULL DEFAULT 'left'",
            "pdf_header_y": "ALTER TABLE jobs ADD COLUMN pdf_header_y REAL NOT NULL DEFAULT 738",
            "pdf_footer_y": "ALTER TABLE jobs ADD COLUMN pdf_footer_y REAL NOT NULL DEFAULT 30",
            "source_kind": "ALTER TABLE jobs ADD COLUMN source_kind TEXT",
            "source_path": "ALTER TABLE jobs ADD COLUMN source_path TEXT",
            "transcript_input": "ALTER TABLE jobs ADD COLUMN transcript_input TEXT",
            "pamphlet_input": "ALTER TABLE jobs ADD COLUMN pamphlet_input TEXT",
            "billing_state": "ALTER TABLE jobs ADD COLUMN billing_state TEXT NOT NULL DEFAULT 'locked'",
            "stripe_checkout_session_id": "ALTER TABLE jobs ADD COLUMN stripe_checkout_session_id TEXT",
            "stripe_payment_intent_id": "ALTER TABLE jobs ADD COLUMN stripe_payment_intent_id TEXT",
            "unlocked_at": "ALTER TABLE jobs ADD COLUMN unlocked_at TEXT",
        }
        user_migrations = {
            "clerk_user_id": "ALTER TABLE users ADD COLUMN clerk_user_id TEXT",
            "stripe_customer_id": "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT",
            "stripe_subscription_id": "ALTER TABLE users ADD COLUMN stripe_subscription_id TEXT",
            "subscription_status": "ALTER TABLE users ADD COLUMN subscription_status TEXT NOT NULL DEFAULT 'inactive'",
            "subscription_current_period_end": "ALTER TABLE users ADD COLUMN subscription_current_period_end TEXT",
            "role": "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member'",
        }
        for column_name, statement in migrations.items():
            if column_name not in existing_columns:
                conn.execute(statement)
        for column_name, statement in user_migrations.items():
            if column_name not in existing_user_columns:
                conn.execute(statement)
        conn.execute("UPDATE jobs SET pdf_font_size = 12 WHERE pdf_font_size IS NULL OR pdf_font_size <= 0")
        conn.execute("UPDATE jobs SET pdf_line_spacing = 1.0 WHERE pdf_line_spacing IS NULL OR pdf_line_spacing <= 0")
        conn.execute("UPDATE jobs SET pdf_body_align = 'left' WHERE pdf_body_align IS NULL OR TRIM(pdf_body_align) = ''")
        conn.execute("UPDATE jobs SET billing_state = 'locked' WHERE billing_state IS NULL OR TRIM(billing_state) = ''")
        conn.execute("UPDATE users SET subscription_status = 'inactive' WHERE subscription_status IS NULL OR TRIM(subscription_status) = ''")
        conn.execute("CREATE INDEX IF NOT EXISTS jobs_user_id_idx ON jobs (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs (status)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_clerk_user_id_idx ON users (clerk_user_id)")
        conn.commit()
    finally:
        conn.close()


def ensure_admin() -> None:
    default_email = "admin@torahcenter.app"
    default_password = "ChangeMe123!"
    conn = db()
    try:
        configured = conn.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
        default_row = None
        if ADMIN_EMAIL != default_email:
            default_row = conn.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (default_email,)).fetchone()

        password_hash = generate_password_hash(ADMIN_PASSWORD)
        if configured:
            conn.execute(
                "UPDATE users SET password_hash = ?, role = 'admin' WHERE id = ?",
                (password_hash, configured["id"]),
            )
        elif default_row and default_row["password_hash"] and check_password_hash(default_row["password_hash"], default_password):
            # Migrate the untouched bootstrap admin account to the configured credentials.
            conn.execute(
                "UPDATE users SET email = ?, password_hash = ?, role = 'admin' WHERE id = ?",
                (ADMIN_EMAIL, password_hash, default_row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, ADMIN_EMAIL, password_hash, "admin", now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


def user_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "clerk_user_id": row.get("clerk_user_id") if isinstance(row, dict) else row["clerk_user_id"] if "clerk_user_id" in row.keys() else None,
        "stripe_customer_id": row.get("stripe_customer_id") if isinstance(row, dict) else row["stripe_customer_id"] if "stripe_customer_id" in row.keys() else None,
        "stripe_subscription_id": row.get("stripe_subscription_id") if isinstance(row, dict) else row["stripe_subscription_id"] if "stripe_subscription_id" in row.keys() else None,
        "subscription_status": row.get("subscription_status") if isinstance(row, dict) else row["subscription_status"] if "subscription_status" in row.keys() else "inactive",
        "subscription_current_period_end": row.get("subscription_current_period_end") if isinstance(row, dict) else row["subscription_current_period_end"] if "subscription_current_period_end" in row.keys() else None,
        "role": row.get("role") if isinstance(row, dict) else row["role"] if "role" in row.keys() else "member",
    }


def job_from_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["review_items"] = json.loads(data.get("review_items") or "[]")
    if data.get("voice_profile"):
        try:
            data["voice_profile"] = json.loads(data["voice_profile"])
        except json.JSONDecodeError:
            data["voice_profile"] = None
    return data


def user_has_subscription(user: dict | None) -> bool:
    if not user:
        return False
    if (user.get("role") or "").lower() == "admin":
        return True
    return (user.get("subscription_status") or "").lower() in {"active", "trialing", "unlimited"}


def job_is_unlocked(job: dict, user: dict | None = None) -> bool:
    if user_has_subscription(user):
        return True
    return (job.get("billing_state") or "").lower() == "unlocked"


def default_terms_library() -> dict:
    entries = []
    for entry in legacy_app.GLOSSARY_ENTRIES:
        entries.append(
            {
                "canonical": entry.get("canonical", ""),
                "display": entry.get("display", ""),
                "variants": entry.get("variants", []),
            }
        )
    for memory_entry in legacy_app.memory_lookup_entries():
        raw_text = legacy_app.clean_spacing(memory_entry.get("raw_text", ""))
        replacement = legacy_app.clean_spacing(memory_entry.get("replacement", ""))
        if not raw_text or not replacement:
            continue
        entries.append(
            {
                "canonical": replacement,
                "display": replacement,
                "variants": [raw_text],
            }
        )
    return {"entries": entries, "updated_at": now_iso()}


def load_terms_library() -> dict:
    if not TERMS_LIBRARY_PATH.exists():
        library = default_terms_library()
        TERMS_LIBRARY_PATH.write_text(json.dumps(library, indent=2), encoding="utf-8")
        return library
    with TERMS_LIBRARY_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_terms_library(library: dict) -> None:
    library["updated_at"] = now_iso()
    TERMS_LIBRARY_PATH.write_text(json.dumps(library, indent=2), encoding="utf-8")


def library_entries() -> list[dict]:
    return load_terms_library().get("entries", [])


def create_job_record(user_id: str, rabbi_name: str, topic: str, transliteration_mode: str = "auto") -> str:
    job_id = uuid.uuid4().hex
    timestamp = now_iso()
    conn = db()
    try:
        conn.execute(
            """
            INSERT INTO jobs (
              id, user_id, status, progress, message, rabbi_name, topic,
              raw_transcript, final_transcript, one_pager, edited_one_pager,
              review_status, review_items, voice_profile, transliteration_mode,
              pdf_line_spacing, pdf_font_size, pdf_background_mode, pdf_custom_background,
              pdf_header_left, pdf_header_right, pdf_footer_text,
              pdf_header_left_size, pdf_header_right_size, pdf_footer_size,
              pdf_header_left_align, pdf_header_right_align, pdf_footer_align, pdf_body_align,
              pdf_header_y, pdf_footer_y, source_kind, source_path, transcript_input, pamphlet_input,
              billing_state, stripe_checkout_session_id, stripe_payment_intent_id, unlocked_at,
              error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                user_id,
                "queued",
                5,
                "Upload received.",
                rabbi_name,
                topic,
                None,
                None,
                None,
                None,
                "not_needed",
                "[]",
                None,
                transliteration_mode,
                1.0,
                12.0,
                "default",
                None,
                DEFAULT_HEADER_LEFT,
                DEFAULT_HEADER_RIGHT,
                DEFAULT_FOOTER_TEXT,
                15.0,
                14.0,
                10.0,
                "left",
                "right",
                "center",
                "left",
                738.0,
                30.0,
                None,
                None,
                None,
                None,
                "locked",
                None,
                None,
                None,
                None,
                timestamp,
                timestamp,
            ),
        )
        conn.commit()
        return job_id
    finally:
        conn.close()


def update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    conn = db()
    try:
        current = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return
        current_status = current["status"]
        incoming_status = fields.get("status")
        if current_status in {"canceled", "deleted"} and incoming_status not in {None, current_status}:
            return
        fields["updated_at"] = now_iso()
        if "review_items" in fields and not isinstance(fields["review_items"], str):
            fields["review_items"] = json.dumps(fields["review_items"])
        columns = ", ".join(f"{key} = ?" for key in fields.keys())
        values = list(fields.values()) + [job_id]
        conn.execute(f"UPDATE jobs SET {columns} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def get_job(job_id: str, user_id: str | None = None) -> dict | None:
    conn = db()
    try:
        if user_id:
            row = conn.execute("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return job_from_row(row) if row else None
    finally:
        conn.close()


def list_jobs(user_id: str) -> list[dict]:
    conn = db()
    try:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [job_from_row(row) for row in rows]
    finally:
        conn.close()


def cancel_job_record(job_id: str, user_id: str) -> bool:
    conn = db()
    try:
        row = conn.execute("SELECT status FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()
        if not row:
            return False
        if row["status"] in {"completed", "failed", "canceled"}:
            return True
        conn.execute(
            "UPDATE jobs SET status = ?, progress = ?, message = ?, error = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            ("canceled", 100, "Project canceled.", None, now_iso(), job_id, user_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def delete_job_record(job_id: str, user_id: str) -> bool:
    conn = db()
    try:
        row = conn.execute("SELECT pdf_custom_background FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()
        if not row:
            return False
        background_path = row["pdf_custom_background"]
        conn.execute("DELETE FROM jobs WHERE id = ? AND user_id = ?", (job_id, user_id))
        conn.commit()
    finally:
        conn.close()
    storage_delete(background_path)
    return True


def auth_token(user: dict) -> str:
    return serializer.dumps({"user_id": user["id"], "email": user["email"]})


def parse_token(token: str) -> dict:
    payload = serializer.loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    return payload


def fetch_user_by_id(user_id: str) -> dict | None:
    conn = db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return user_to_dict(row) if row else None
    finally:
        conn.close()


def fetch_user_by_email(email: str) -> sqlite3.Row | None:
    conn = db()
    try:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()


def fetch_user_by_clerk_user_id(clerk_user_id: str):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM users WHERE clerk_user_id = ?", (clerk_user_id,)).fetchone()
        return user_to_dict(row) if row else None
    finally:
        conn.close()


def fetch_user_by_stripe_customer_id(customer_id: str) -> dict | None:
    conn = db()
    try:
        row = conn.execute("SELECT * FROM users WHERE stripe_customer_id = ?", (customer_id,)).fetchone()
        return user_to_dict(row) if row else None
    finally:
        conn.close()


def fetch_user_by_stripe_subscription_id(subscription_id: str) -> dict | None:
    conn = db()
    try:
        row = conn.execute("SELECT * FROM users WHERE stripe_subscription_id = ?", (subscription_id,)).fetchone()
        return user_to_dict(row) if row else None
    finally:
        conn.close()


def upsert_clerk_user(clerk_user_id: str, email: str) -> dict:
    existing = fetch_user_by_clerk_user_id(clerk_user_id)
    if existing:
        if existing["email"] != email:
            conn = db()
            try:
                conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, existing["id"]))
                conn.commit()
            finally:
                conn.close()
            existing["email"] = email
        return existing

    row = fetch_user_by_email(email)
    if row:
        conn = db()
        try:
            conn.execute("UPDATE users SET clerk_user_id = ? WHERE id = ?", (clerk_user_id, row["id"]))
            conn.commit()
        finally:
            conn.close()
        return {**user_to_dict(row), "clerk_user_id": clerk_user_id}

    conn = db()
    try:
        user_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO users (id, clerk_user_id, email, password_hash, subscription_status, role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, clerk_user_id, email, None, "inactive", "member", now_iso()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return user_to_dict(row)
    finally:
        conn.close()


def authenticate_credentials(email: str, password: str) -> dict | None:
    row = fetch_user_by_email(email)
    if not row or not row["password_hash"] or not check_password_hash(row["password_hash"], password):
        return None
    return user_to_dict(row)


def clerk_headers() -> dict:
    return {"Authorization": f"Bearer {CLERK_SECRET_KEY}"} if CLERK_SECRET_KEY else {}


def clerk_jwks() -> dict:
    if not CLERK_JWKS_URL:
        raise RuntimeError("CLERK_JWKS_URL is not configured.")
    cached = jwt_jwks_cache.get(CLERK_JWKS_URL)
    if cached and (time.time() - cached[0]) < 3600:
        return cached[1]
    response = requests.get(CLERK_JWKS_URL, timeout=30)
    response.raise_for_status()
    body = response.json()
    jwt_jwks_cache[CLERK_JWKS_URL] = (time.time(), body)
    return body


def clerk_user_email(clerk_user_id: str) -> str:
    if not CLERK_SECRET_KEY:
        raise RuntimeError("CLERK_SECRET_KEY is not configured.")
    response = requests.get(
        f"https://api.clerk.com/v1/users/{clerk_user_id}",
        headers=clerk_headers(),
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    for address in body.get("email_addresses", []):
        if address.get("id") == body.get("primary_email_address_id"):
            return legacy_app.clean_spacing(address.get("email_address", "")).lower()
    email_addresses = body.get("email_addresses") or []
    if email_addresses:
        return legacy_app.clean_spacing(email_addresses[0].get("email_address", "")).lower()
    raise RuntimeError("The Clerk user does not have an email address.")


def verify_clerk_token(token: str) -> dict | None:
    if not CLERK_ENABLED or not token:
        return None
    jwks = clerk_jwks()
    header = jwt.get_unverified_header(token)
    key = None
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == header.get("kid"):
            key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            break
    if key is None:
        raise RuntimeError("Could not find the matching Clerk signing key.")
    claims = jwt.decode(
        token,
        key=key,
        algorithms=["RS256"],
        options={"verify_aud": False},
    )
    return claims


def clerk_token_from_request() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.replace("Bearer ", "", 1).strip()
    return request.cookies.get("__session", "").strip()


def current_clerk_user() -> dict | None:
    if not CLERK_ENABLED:
        return None
    token = clerk_token_from_request()
    if not token:
        return None
    try:
        claims = verify_clerk_token(token)
    except Exception:
        return None
    clerk_user_id = claims.get("sub")
    if not clerk_user_id:
        return None
    existing = fetch_user_by_clerk_user_id(clerk_user_id)
    if existing:
        return existing
    email = legacy_app.clean_spacing((claims.get("email") or claims.get("email_address") or "")).lower()
    if not email:
        email = clerk_user_email(clerk_user_id)
    return upsert_clerk_user(clerk_user_id, email)


def current_session_user() -> dict | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return fetch_user_by_id(user_id)


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_clerk_user()
        if not user:
            header = request.headers.get("Authorization", "")
            token = header.replace("Bearer ", "", 1).strip() if header.startswith("Bearer ") else ""
            if token:
                try:
                    payload = parse_token(token)
                    user = fetch_user_by_id(payload["user_id"])
                except BadSignature:
                    return jsonify({"error": "Authorization token is invalid or expired."}), 401
            else:
                user = current_session_user()
        if not user:
            return jsonify({"error": "Authorization is required."}), 401
        request.current_user = user
        return fn(*args, **kwargs)

    return wrapper


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
    return response


def browser_login_required():
    return redirect(url_for("login_page", next=request.path))


def browser_user():
    user = current_clerk_user() or current_session_user()
    return user if user else None


@app.route("/", methods=["GET"])
def home_page():
    user = browser_user()
    if not user:
        return redirect(url_for("login_page"))
    jobs_data = [sanitize_job_for_user(job, user) for job in list_jobs(user["id"])]
    selected_job_id = request.args.get("job_id")
    selected_job = None
    if selected_job_id:
        job = get_job(selected_job_id, user["id"])
        selected_job = sanitize_job_for_user(job, user) if job else None
    elif jobs_data:
        selected_job = jobs_data[0]
    return render_template(
        "app.html",
        shul_name=SHUL_NAME,
        product_name=PRODUCT_NAME,
        user=user,
        jobs=jobs_data,
        selected_job=selected_job,
        clerk_enabled=CLERK_ENABLED,
        clerk_publishable_key=CLERK_PUBLISHABLE_KEY,
        clerk_frontend_api_url=CLERK_FRONTEND_API_URL,
        clerk_after_sign_in_url=CLERK_AFTER_SIGN_IN_URL,
        clerk_after_sign_up_url=CLERK_AFTER_SIGN_UP_URL,
        stripe_enabled=STRIPE_ENABLED,
    )


@app.route("/healthz", methods=["GET"])
def healthcheck():
    return jsonify({"ok": True, "product": PRODUCT_NAME})


@app.route("/login", methods=["GET"])
def login_page():
    if browser_user():
        return redirect(url_for("home_page"))
    return render_template(
        "login.html",
        shul_name=PRODUCT_NAME,
        error=None,
        clerk_enabled=CLERK_ENABLED,
        clerk_publishable_key=CLERK_PUBLISHABLE_KEY,
        clerk_frontend_api_url=CLERK_FRONTEND_API_URL,
        clerk_after_sign_in_url=CLERK_AFTER_SIGN_IN_URL,
        clerk_after_sign_up_url=CLERK_AFTER_SIGN_UP_URL,
    )


@app.route("/login", methods=["POST"])
def login_submit():
    email = legacy_app.clean_spacing(request.form.get("email", "")).lower()
    password = request.form.get("password", "")
    user = authenticate_credentials(email, password)
    if not user:
        return render_template("login.html", shul_name=PRODUCT_NAME, error="Email or password is incorrect."), 401
    session["user_id"] = user["id"]
    session["user_email"] = user["email"]
    return redirect(url_for("home_page"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "torah-center-article-studio"})


@app.route("/api/auth/login", methods=["POST"])
def login():
    payload = request.get_json(silent=True) or {}
    email = legacy_app.clean_spacing(payload.get("email", "")).lower()
    password = payload.get("password", "")
    if not email or not password:
        return jsonify({"error": "Email and password are required."}), 400

    user = authenticate_credentials(email, password)
    if not user:
        return jsonify({"error": "Email or password is incorrect."}), 401
    return jsonify({"token": auth_token(user), "user": user})


def local_path_for_storage(stored_path: str) -> Path:
    if not stored_path:
        raise RuntimeError("No storage path is available.")
    return Path(stored_path)


def storage_put_bytes(data: bytes, destination: str) -> str:
    if STORAGE_BACKEND == "s3":
        if not S3_BUCKET:
            raise RuntimeError("S3_BUCKET is not configured for hosted storage.")
        client = s3_client()
        client.put_object(Bucket=S3_BUCKET, Key=destination, Body=data)
        return destination
    target = STORAGE_DIR / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return str(target)


def storage_read_bytes(stored_path: str) -> bytes:
    if STORAGE_BACKEND == "s3":
        client = s3_client()
        response = client.get_object(Bucket=S3_BUCKET, Key=stored_path)
        return response["Body"].read()
    return local_path_for_storage(stored_path).read_bytes()


def storage_delete(stored_path: str | None) -> None:
    if not stored_path:
        return
    try:
        if STORAGE_BACKEND == "s3":
            client = s3_client()
            client.delete_object(Bucket=S3_BUCKET, Key=stored_path)
        else:
            local_path_for_storage(stored_path).unlink(missing_ok=True)
    except Exception:
        return


@contextmanager
def stored_file_path(stored_path: str, suffix: str = ""):
    if STORAGE_BACKEND != "s3":
        yield str(local_path_for_storage(stored_path))
        return
    payload = storage_read_bytes(stored_path)
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
        handle.write(payload)
        temp_path = handle.name
    try:
        yield temp_path
    finally:
        try:
            Path(temp_path).unlink(missing_ok=True)
        except OSError:
            pass


def store_uploaded_file(file_storage, folder: Path, prefix: str = "uploads") -> str:
    filename = legacy_app.safe_filename(file_storage.filename)
    data = file_storage.read()
    file_storage.stream.seek(0)
    destination = storage_key(prefix, f"{uuid.uuid4().hex}_{filename}")
    return storage_put_bytes(data, destination)


def sanitize_job_for_user(job: dict, user: dict | None) -> dict:
    sanitized = dict(job)
    access_locked = False
    if sanitized.get("status") == "completed" and not job_is_unlocked(sanitized, user):
        access_locked = True
        for key in ("raw_transcript", "final_transcript", "one_pager", "edited_one_pager"):
            sanitized[key] = None
        sanitized["message"] = "Article complete. Unlock it to view the transcript, article, and export tools."
    sanitized["access_locked"] = access_locked
    sanitized["can_unlock"] = sanitized.get("status") == "completed" and not user_has_subscription(user)
    sanitized["has_subscription"] = user_has_subscription(user)
    sanitized["single_unlock_price_cents"] = SINGLE_UNLOCK_PRICE_CENTS
    sanitized["monthly_price_cents"] = MONTHLY_PLAN_PRICE_CENTS
    return sanitized


def sync_user_customer(user_id: str, stripe_customer_id: str) -> None:
    conn = db()
    try:
        conn.execute("UPDATE users SET stripe_customer_id = ? WHERE id = ?", (stripe_customer_id, user_id))
        conn.commit()
    finally:
        conn.close()


def set_user_subscription(user_id: str, *, status: str, subscription_id: str | None = None, current_period_end: str | None = None) -> None:
    conn = db()
    try:
        conn.execute(
            "UPDATE users SET subscription_status = ?, stripe_subscription_id = ?, subscription_current_period_end = ? WHERE id = ?",
            (status, subscription_id, current_period_end, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def unlock_job(job_id: str, payment_intent_id: str | None = None, checkout_session_id: str | None = None) -> None:
    update_job(
        job_id,
        billing_state="unlocked",
        stripe_payment_intent_id=payment_intent_id,
        stripe_checkout_session_id=checkout_session_id,
        unlocked_at=now_iso(),
    )


def stripe_customer_for_user(user: dict) -> str:
    if not STRIPE_ENABLED:
        raise RuntimeError("Stripe is not configured.")
    if user.get("stripe_customer_id"):
        return user["stripe_customer_id"]
    customer = stripe.Customer.create(email=user["email"], metadata={"user_id": user["id"]})
    sync_user_customer(user["id"], customer["id"])
    user["stripe_customer_id"] = customer["id"]
    return customer["id"]


def can_view_job_content(job: dict, user: dict) -> bool:
    return job.get("status") != "completed" or job_is_unlocked(job, user)


def ensure_job_content_access(job: dict, user: dict) -> None:
    if not can_view_job_content(job, user):
        raise RuntimeError("Payment is required before this completed article can be viewed or exported.")


def build_skill_transcription_prompt() -> str:
    important_terms = [entry.get("display", "") for entry in library_entries()[:24] if entry.get("display")]
    return (
        "Transcribe this media into readable English text.\n"
        "Preserve Hebrew names, Jewish terms, and transliterated religious language as accurately as possible.\n"
        "Do not replace unclear Hebrew or transliterated words with generic placeholders like 'foreign language' or 'unclear term'.\n"
        "If a Hebrew term is uncertain, transcribe the closest plausible sound and use a narrow uncertainty note only if needed.\n"
        "Do not summarize, rewrite into an article, or flatten domain vocabulary.\n\n"
        "Useful term spellings:\n"
        + "\n".join(f"- {term}" for term in important_terms)
    )


def transcription_chunk_message(index: int, total_chunks: int) -> str:
    return (
        f"Transcribing chunk {index} of {total_chunks} with OpenAI while preserving Hebrew transliterations."
    )


def transcription_progress_value(processed_seconds: float, total_seconds: float) -> int:
    if total_seconds <= 0:
        return 28
    ratio = max(0.0, min(processed_seconds / total_seconds, 1.0))
    return min(68, max(20, 20 + int(ratio * 48)))


def update_transcription_progress(
    job_id: str | None,
    *,
    processed_seconds: float,
    total_seconds: float,
    chunk_index: int = 1,
    total_chunks: int = 1,
) -> None:
    if not job_id:
        return
    progress = transcription_progress_value(processed_seconds, total_seconds)
    remaining_seconds = max(0.0, total_seconds - processed_seconds)
    remaining_minutes = max(1, round(remaining_seconds / 60))
    update_job(
        job_id,
        status="running",
        progress=progress,
        message=(
            f"{transcription_chunk_message(chunk_index, total_chunks)} "
            f"About {remaining_minutes} minute(s) of audio remain to transcribe."
        ),
    )


class DurationLimitExceededError(RuntimeError):
    pass


def current_openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip() or OPENAI_API_KEY


def openai_headers() -> dict:
    api_key = current_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to cloud_backend/.env before running the hosted app.")
    legacy_app.OPENAI_API_KEY = api_key
    return {"Authorization": f"Bearer {api_key}"}


def openai_json_headers() -> dict:
    return {**openai_headers(), "Content-Type": "application/json"}


def openai_request(method: str, url: str, *, retries: int = 4, retry_label: str = "OpenAI request", **kwargs):
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.request(method, url, **kwargs)
            if response.status_code not in getattr(legacy_app, "RETRYABLE_STATUS_CODES", {408, 429, 500, 502, 503, 504}):
                return response
            last_error = RuntimeError(f"{retry_label} error {response.status_code}: {response.text}")
        except requests.exceptions.RequestException as exc:
            last_error = exc
        if attempt == retries:
            break
        time.sleep(min(2 ** attempt, 8))
    if isinstance(last_error, Exception):
        raise last_error
    raise RuntimeError(f"{retry_label} failed after retries.")


def openai_chat(messages: list[dict], *, model: str, temperature: float = 0.2, json_mode: bool = False) -> str:
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    response = openai_request(
        "POST",
        f"{OPENAI_BASE_URL}/chat/completions",
        retry_label="OpenAI chat",
        headers=openai_json_headers(),
        json=payload,
        timeout=OPENAI_TIMEOUT_SECONDS,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenAI chat error {response.status_code}: {response.text}")
    body = response.json()
    content = (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError(f"Unexpected OpenAI response: {body}")
    return content


def openai_json(messages: list[dict], *, model: str, temperature: float = 0.1) -> dict:
    content = openai_chat(messages, model=model, temperature=temperature, json_mode=True)
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"OpenAI returned invalid JSON: {content}") from exc


def transcribe_chunk_openai(file_path: Path) -> str:
    with file_path.open("rb") as audio_file:
        response = openai_request(
            "POST",
            f"{OPENAI_BASE_URL}/audio/transcriptions",
            retry_label="OpenAI transcription",
            headers=openai_headers(),
            data={
                "model": TRANSCRIBE_MODEL,
                "response_format": "json",
                "prompt": build_skill_transcription_prompt(),
            },
            files={"file": (file_path.name, audio_file, "audio/mpeg")},
            timeout=OPENAI_TIMEOUT_SECONDS,
        )
    if response.status_code >= 400:
        if response.status_code == 400 and "longer than 1400 seconds" in response.text.lower():
            raise DurationLimitExceededError(response.text)
        raise RuntimeError(f"OpenAI transcription error {response.status_code}: {response.text}")
    body = response.json()
    transcript = legacy_app.clean_spacing(body.get("text", ""))
    if not transcript:
        raise RuntimeError(f"Unexpected transcription response: {body}")
    return transcript


def transliteration_context_text() -> str:
    lines = []
    for entry in library_entries()[:60]:
        display = legacy_app.clean_spacing(entry.get("display", ""))
        variants = [legacy_app.clean_spacing(variant) for variant in entry.get("variants", []) if legacy_app.clean_spacing(variant)]
        if not display:
            continue
        if variants:
            lines.append(f"- {display}: {', '.join(variants[:4])}")
        else:
            lines.append(f"- {display}")
    return "\n".join(lines) or "- No library entries available."


def build_transliteration_prompt(transcript: str) -> str:
    return (
        "You are transliterating Hebrew and Jewish religious vocabulary inside an English transcript.\n"
        "Return strict JSON with keys `transcript` and `low_confidence_items`.\n"
        "`transcript` must be the full transcript with Hebrew/Jewish terms lightly normalized into readable English transliteration.\n"
        "`low_confidence_items` must be a list of objects with keys `raw_text`, `suggested`, and `context` for only the uncertain terms that still need a person to review.\n"
        "Do not summarize. Do not remove content. Do not replace unknown terms with generic placeholders.\n"
        "Use the provided library and repeated context from the transcript before marking anything uncertain.\n\n"
        "Hebrew terms library:\n"
        f"{transliteration_context_text()}\n\n"
        "Transcript:\n"
        f"{transcript}"
    )


def auto_transliterate_transcript(transcript: str) -> tuple[str, list[dict]]:
    parsed = openai_json(
        [
            {
                "role": "system",
                "content": "You normalize Hebrew transliteration inside English transcripts and return only valid JSON.",
            },
            {
                "role": "user",
                "content": build_transliteration_prompt(transcript),
            },
        ],
        model=REVIEW_MODEL,
        temperature=0.1,
    )
    transliterated = legacy_app.clean_spacing(parsed.get("transcript", transcript))
    review_items = []
    for index, item in enumerate(parsed.get("low_confidence_items", []), start=1):
        raw_text = legacy_app.clean_spacing(item.get("raw_text", ""))
        if not raw_text:
            continue
        review_items.append(
            {
                "id": f"auto_{index}",
                "raw_text": raw_text,
                "context": legacy_app.clean_spacing(item.get("context", "")) or raw_text,
                "suggestions": [legacy_app.clean_spacing(item.get("suggested", ""))] if legacy_app.clean_spacing(item.get("suggested", "")) else [],
                "clarification": legacy_app.clean_spacing(item.get("suggested", "")),
            }
        )
    return transliterated, review_items


def build_hebrew_render_prompt(transcript: str) -> str:
    return (
        "You are converting Hebrew and Jewish religious vocabulary inside an English transcript into Hebrew script where confident.\n"
        "Return strict JSON with keys `transcript` and `low_confidence_items`.\n"
        "`transcript` must preserve the full English transcript but render confident Hebrew/Jewish terms in Hebrew script.\n"
        "Do not summarize. Do not remove content. Do not invent Hebrew for terms that are uncertain.\n"
        "Only convert to Hebrew script when you are confident from transcript context, repeated usage, or the terms library.\n"
        "For uncertain items, keep the original wording in the transcript and include a review object with keys `raw_text`, `suggested`, and `context`.\n\n"
        "Hebrew terms library:\n"
        f"{transliteration_context_text()}\n\n"
        "Transcript:\n"
        f"{transcript}"
    )


def render_hebrew_terms_transcript(transcript: str) -> tuple[str, list[dict]]:
    parsed = openai_json(
        [
            {
                "role": "system",
                "content": "You conservatively render confident Hebrew/Jewish terms into Hebrew script inside English transcripts and return only valid JSON.",
            },
            {
                "role": "user",
                "content": build_hebrew_render_prompt(transcript),
            },
        ],
        model=REVIEW_MODEL,
        temperature=0.1,
    )
    rendered = legacy_app.clean_spacing(parsed.get("transcript", transcript))
    review_items = []
    for index, item in enumerate(parsed.get("low_confidence_items", []), start=1):
        raw_text = legacy_app.clean_spacing(item.get("raw_text", ""))
        if not raw_text:
            continue
        suggested = legacy_app.clean_spacing(item.get("suggested", ""))
        review_items.append(
            {
                "id": f"hebrew_{index}",
                "raw_text": raw_text,
                "context": legacy_app.clean_spacing(item.get("context", "")) or raw_text,
                "suggestions": [suggested] if suggested else [],
                "clarification": suggested,
            }
        )
    return rendered, review_items


def build_voice_analysis_prompt(transcript: str) -> str:
    return (
        "Analyze this Torah transcript for speaker voice. Return strict JSON with keys:\n"
        "`signature_phrases` (array of exact or near-exact phrases worth preserving),\n"
        "`emotional_temperature`, `cadence`, `priorities`, and `quoted_language` (array).\n"
        "Keep the analysis short and faithful to transcript language.\n"
        "Do not summarize the class. Do not add facts.\n\n"
        f"Transcript:\n{transcript}"
    )


def transcript_language_anchors(transcript: str) -> list[str]:
    candidates = []
    for chunk in re.split(r"(?<=[\.\?\!])\s+|\n+", transcript):
        line = legacy_app.clean_spacing(chunk)
        if len(line) < 35 or len(line) > 220:
            continue
        score = 0
        if "?" in line:
            score += 3
        if "!" in line:
            score += 2
        if '"' in line or "'" in line:
            score += 2
        if re.search(r"\b(Shabbat|Hashem|Torah|mitzvah|kedushah|Moshe|Rabbi|halach|berit|bereshit|Yisrael)\b", line, re.IGNORECASE):
            score += 2
        if re.search(r"\b(we|you|I)\b", line):
            score += 1
        if score:
            candidates.append((score, line))
    unique = []
    seen = set()
    for _, line in sorted(candidates, key=lambda item: (-item[0], item[1])):
        lowered = line.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(line)
        if len(unique) == 8:
            break
    return unique


def analyze_voice(transcript: str) -> dict:
    return openai_json(
        [
            {
                "role": "system",
                "content": "You analyze a transcript for voice markers and return only valid JSON.",
            },
            {
                "role": "user",
                "content": build_voice_analysis_prompt(transcript),
            },
        ],
        model=REVIEW_MODEL,
        temperature=0.2,
    )


def transcribe_media_with_chunks(file_path: Path, *, job_id: str | None = None, split_reason: str = "size") -> str:
    max_chunk_bytes = legacy_app.TARGET_CHUNK_BYTES if split_reason == "size" else None
    chunk_dir, chunks = legacy_app.split_audio_with_ffmpeg(
        file_path,
        max_chunk_bytes=max_chunk_bytes,
        split_reason=split_reason,
    )
    transcripts = []
    try:
        total_chunks = len(chunks)
        chunk_durations = []
        for chunk in chunks:
            try:
                chunk_durations.append(legacy_app.media_duration_seconds(chunk))
            except Exception:
                chunk_durations.append(0.0)
        total_duration = sum(chunk_durations)
        processed_before_chunk = 0.0
        for index, chunk in enumerate(chunks, start=1):
            if job_id:
                progress = 18 + int(((index - 1) / max(total_chunks, 1)) * 42)
                update_job(
                    job_id,
                    status="running",
                    progress=min(progress, 58),
                    message=transcription_chunk_message(index, total_chunks),
                )
            chunk_duration = chunk_durations[index - 1]
            transcripts.append(transcribe_chunk_openai(chunk))
            processed_before_chunk += chunk_duration
            if job_id:
                progress = 18 + int((index / max(total_chunks, 1)) * 42)
                update_job(
                    job_id,
                    status="running",
                    progress=min(progress, 60),
                    message=f"Finished transcribing chunk {index} of {total_chunks}.",
                )
    finally:
        legacy_app.shutil.rmtree(chunk_dir, ignore_errors=True)

    transcript = legacy_app.clean_spacing("\n".join(part for part in transcripts if part))
    if not transcript:
        raise RuntimeError("Chunked transcription returned empty text.")
    return transcript


def transcribe_media_with_skill(file_path: Path, job_id: str | None = None) -> str:
    source_size = file_path.stat().st_size
    if source_size > legacy_app.MAX_AUDIO_BYTES:
        if job_id and source_size > SKILL_SOURCE_LIMIT_BYTES:
            max_mb = SKILL_SOURCE_LIMIT_BYTES // (1024 * 1024)
            update_job(
                job_id,
                status="running",
                progress=16,
                message=(
                    f"Source media is over {max_mb} MB. Splitting it into smaller chunks before transcription."
                ),
            )
        if job_id:
            update_job(
                job_id,
                status="running",
                progress=18,
                message="Large media detected. Splitting it into smaller chunks for the transcription workflow.",
            )
        return transcribe_media_with_chunks(file_path, job_id=job_id, split_reason="size")

    if job_id:
        update_job(
            job_id,
            status="running",
            progress=20,
            message="Transcribing audio with OpenAI while preserving Hebrew transliterations.",
        )
    return transcribe_chunk_openai(file_path)


def build_article_prompt(rabbi_name: str, topic: str, transcript: str, glossary_entries, voice_profile: dict | None) -> str:
    voice_profile = voice_profile or {}
    signature_phrases = "\n".join(f"- {phrase}" for phrase in voice_profile.get("signature_phrases", [])[:6]) or "- None identified"
    quoted_language = "\n".join(f"- {phrase}" for phrase in voice_profile.get("quoted_language", [])[:6]) or "- None identified"
    language_anchors = "\n".join(f"- {phrase}" for phrase in transcript_language_anchors(transcript)) or "- No anchor lines extracted"
    return (
        "Turn this spoken Torah material into a polished single-page article.\n"
        "The article must be composed nearly entirely from the transcript's own language.\n"
        "Prefer lifting, compressing, and lightly polishing transcript phrasing over inventing new phrasing.\n"
        "Only paraphrase where needed for flow, compression, and transitions.\n"
        "The article must preserve the speaker's voice, tone, phrasing, priorities, and recognizable cadence.\n"
        "Write it as a coherent article, not as a transcript summary and not as lecture notes.\n"
        "Target 350 to 450 words.\n"
        "Use four to six short paragraphs.\n"
        "Open with the strongest idea or claim.\n"
        "Keep the speaker's natural warmth and cadence, but remove filler and repetition.\n"
        "Use the speaker's own turns of phrase wherever they read naturally on the page.\n"
        "Preserve at least three signature phrases or quoted lines when the transcript supports them.\n"
        "For most sentences, stay very close to transcript wording.\n"
        "Do not use recap language like 'the lecture discusses', 'the Rabbi explains', 'this class covers', or 'the speaker says'.\n"
        "Do not add facts that are not grounded in the transcript.\n"
        "Do not include a title line, byline, or section labels in the generated body.\n"
        "This article is for topic: "
        f"{topic}\n"
        f"Speaker: {rabbi_name}\n\n"
        "Voice notes:\n"
        f"- Emotional temperature: {voice_profile.get('emotional_temperature', 'not specified')}\n"
        f"- Cadence: {voice_profile.get('cadence', 'not specified')}\n"
        f"- Priorities: {voice_profile.get('priorities', 'not specified')}\n"
        "Signature phrases to preserve if natural:\n"
        f"{signature_phrases}\n"
        "Quoted language worth keeping if natural:\n"
        f"{quoted_language}\n\n"
        "Transcript language anchors to reuse directly where natural:\n"
        f"{language_anchors}\n\n"
        "Relevant glossary forms:\n"
        f"{legacy_app.glossary_context(glossary_entries)}\n\n"
        "Transcript:\n"
        f"{transcript}"
    )


def refine_article_for_voice(article: str, transcript: str, voice_profile: dict | None) -> str:
    content = openai_chat(
        [
            {
                "role": "system",
                "content": "You revise articles so they sound closer to the original speaker without adding facts.",
            },
            {
                "role": "user",
                "content": (
                    "Revise this draft so it sounds more recognizably like the transcript speaker.\n"
                    "Rebuild sentences from transcript wording wherever possible.\n"
                    "Keep the article composed mostly from transcript language rather than fresh paraphrase.\n"
                    "Keep it publication-ready and within one page.\n"
                    "Remove any recap/report framing.\n"
                    f"Voice notes: {json.dumps(voice_profile or {}, ensure_ascii=True)}\n\n"
                    f"Transcript:\n{transcript}\n\n"
                    f"Draft:\n{article}"
                ),
            },
        ],
        model=ARTICLE_MODEL,
        temperature=0.35,
    )
    return legacy_app.clean_spacing(content)


def generate_article(rabbi_name: str, topic: str, transcript: str, glossary_entries, voice_profile: dict | None) -> str:
    article = legacy_app.clean_spacing(
        openai_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You write concise publication-ready articles from spoken Torah recordings while preserving the speaker's recognizable voice."
                    ),
                },
                {
                    "role": "user",
                    "content": build_article_prompt(rabbi_name, topic, transcript, glossary_entries, voice_profile),
                },
            ],
            model=ARTICLE_MODEL,
            temperature=0.25,
        )
    )
    if not article:
        raise RuntimeError("OpenAI article generation returned empty text.")
    article = refine_article_for_voice(article, transcript, voice_profile)
    if re.search(r"\b(the lecture discusses|the rabbi explains|this class covers|the speaker says|the text covers)\b", article, flags=re.IGNORECASE):
        article = refine_article_for_voice(article, transcript, voice_profile)
    return article


def finalize_transcript_for_mode(transcript: str, transliteration_mode: str) -> str:
    if transliteration_mode == "hebrew":
        return transcript
    return legacy_app.normalize_confirmed_terms(transcript)


def finish_transcript_pipeline(job_id: str, transcript: str) -> None:
    job = get_job(job_id)
    if not job:
        return
    if job.get("status") == "canceled":
        return
    owner = fetch_user_by_id(job["user_id"])
    raw_transcript = legacy_app.apply_memory_clarifications(transcript)
    transliteration_mode = (job.get("transliteration_mode") or "auto").strip().lower()
    update_job(job_id, progress=70, message="Reviewing the transcript for Hebrew transliteration and voice cues.", raw_transcript=raw_transcript)
    transliterated_transcript = raw_transcript
    review_items = []
    if transliteration_mode == "auto":
        try:
            transliterated_transcript, review_items = auto_transliterate_transcript(raw_transcript)
        except Exception:
            transliterated_transcript = raw_transcript
            review_items = legacy_app.detect_review_items(raw_transcript)
    elif transliteration_mode == "hebrew":
        try:
            transliterated_transcript, review_items = render_hebrew_terms_transcript(raw_transcript)
        except Exception:
            transliterated_transcript = raw_transcript
            review_items = legacy_app.detect_review_items(raw_transcript)
    else:
        review_items = legacy_app.detect_review_items(raw_transcript)

    if review_items:
        update_job(
            job_id,
            status="needs_review",
            progress=76,
            message="Please review unclear Hebrew terms before the article is generated.",
            review_items=review_items,
            review_status="required",
            final_transcript=transliterated_transcript if transliteration_mode in {"auto", "hebrew"} else None,
        )
        return

    final_transcript = finalize_transcript_for_mode(transliterated_transcript, transliteration_mode)
    glossary_entries = legacy_app.matched_glossary_entries(final_transcript)
    voice_profile = analyze_voice(final_transcript)
    update_job(
        job_id,
        status="running",
        progress=82,
        final_transcript=final_transcript,
        review_status="completed",
        voice_profile=json.dumps(voice_profile),
        message="Writing the article in the speaker's voice.",
    )
    article = generate_article(job["rabbi_name"], job["topic"], final_transcript, glossary_entries, voice_profile)
    billing_state = "unlocked" if user_has_subscription(owner) else "locked"
    message = "Article ready." if billing_state == "unlocked" else "Article ready. Unlock it to view the transcript and exports."
    update_job(job_id, status="completed", progress=100, message=message, one_pager=article, edited_one_pager=article, billing_state=billing_state)


def process_job(job_id: str, source_kind: str, source_path: str | Path | None = None, transcript_text: str | None = None, pamphlet_text: str | None = None) -> None:
    try:
        existing_job = get_job(job_id)
        if not existing_job or existing_job.get("status") == "canceled":
            return
        owner = fetch_user_by_id(existing_job["user_id"])
        if source_kind == "pamphlet":
            cleaned = legacy_app.clean_spacing(pamphlet_text or "")
            billing_state = "unlocked" if user_has_subscription(owner) else "locked"
            update_job(job_id, status="completed", progress=100, message="Article text is ready." if billing_state == "unlocked" else "Article text is ready. Unlock it to view and export.", one_pager=cleaned, edited_one_pager=cleaned, review_status="completed", billing_state=billing_state)
            return

        if source_kind == "transcript":
            update_job(job_id, status="running", progress=30, message="Using your pasted transcript and moving straight into review.")
            finish_transcript_pipeline(job_id, legacy_app.clean_spacing(transcript_text or ""))
            return

        if not source_path:
            raise RuntimeError("No source file was provided.")
        source_name = str(source_path)
        suffix = Path(source_name).suffix.lower()
        with stored_file_path(source_name, suffix=suffix) as local_path_str:
            transcribe_path = Path(local_path_str)
            cleanup_mp3 = None
            if transcribe_path.suffix.lower() == ".mp4":
                update_job(job_id, status="running", progress=12, message="Converting video to .mp3 before transcription.")
                transcribe_path = legacy_app.convert_media_to_mp3(transcribe_path)
                cleanup_mp3 = transcribe_path

            update_job(
                job_id,
                status="running",
                progress=20,
                message="Starting the English transcription workflow for this recording.",
            )
            transcript = transcribe_media_with_skill(transcribe_path, job_id=job_id)
            if cleanup_mp3:
                try:
                    cleanup_mp3.unlink(missing_ok=True)
                except OSError:
                    pass
        finish_transcript_pipeline(job_id, transcript)
    except Exception as exc:
        update_job(job_id, status="failed", progress=100, message="Processing failed.", error=f"Processing failed: {exc}")


def claim_next_job() -> dict | None:
    conn = db()
    try:
        if USE_POSTGRES:
            row = conn.execute(
                """
                UPDATE jobs
                SET status = ?, progress = ?, message = ?, updated_at = ?
                WHERE id = (
                  SELECT id FROM jobs WHERE status = ? ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                RETURNING *
                """,
                ("running", 8, "Worker picked up this job.", now_iso(), "queued"),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC LIMIT 1", ("queued",)).fetchone()
            if row:
                conn.execute(
                    "UPDATE jobs SET status = ?, progress = ?, message = ?, updated_at = ? WHERE id = ?",
                    ("running", 8, "Worker picked up this job.", now_iso(), row["id"]),
                )
                row = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        conn.commit()
        return job_from_row(row) if row else None
    finally:
        conn.close()


def process_queued_job(job: dict) -> None:
    process_job(
        job["id"],
        job.get("source_kind") or "audio",
        job.get("source_path"),
        job.get("transcript_input"),
        job.get("pamphlet_input"),
    )


def worker_loop(poll_seconds: int = 3) -> None:
    init_db()
    ensure_admin()
    while True:
        job = claim_next_job()
        if job:
            process_queued_job(job)
            continue
        time.sleep(poll_seconds)


def pdf_text_x(text: str, align: str, default_x: float, font_size: float, page_width: float = LETTER_WIDTH) -> float:
    estimated_width = max(len(text) * font_size * 0.42, font_size * 2)
    if align == "center":
        return max(36.0, (page_width - estimated_width) / 2.0)
    if align == "right":
        return max(36.0, page_width - estimated_width - 46.0)
    return default_x


def pdf_body_text_x(text: str, align: str, body_left: float, body_width: float, font_size: float) -> float:
    estimated_width = max(pdf_text_width(text, font_size), font_size * 2)
    if align == "center":
        return max(body_left, body_left + (body_width - estimated_width) / 2.0)
    if align == "right":
        return max(body_left, body_left + body_width - estimated_width)
    return body_left


def pdf_char_width_factor(character: str) -> float:
    if character == " ":
        return 0.25
    if character in "ilI'`.,;:!|":
        return 0.2
    if character in "mwMW@#%&":
        return 0.78
    if character in "frtJ()[]{}":
        return 0.32
    if character in "-_":
        return 0.28
    if character.isupper():
        return 0.62
    return 0.5


def pdf_text_width(text: str, font_size: float) -> float:
    return sum(pdf_char_width_factor(character) for character in text) * font_size


def pdf_justify_word_spacing(text: str, body_width: float, font_size: float) -> float:
    spaces = text.count(" ")
    if spaces <= 0:
        return 0.0
    estimated_width = max(pdf_text_width(text, font_size), font_size * 2)
    extra_width = body_width - estimated_width
    if extra_width <= 0:
        return 0.0
    return min(extra_width / spaces, font_size * 0.45)


def ensure_jpeg_background(background_path: Path) -> Path:
    if background_path.suffix.lower() in {".jpg", ".jpeg"}:
        return background_path
    ffmpeg = legacy_app.ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to convert custom backgrounds into PDF-safe JPEG images.")
    jpg_path = background_path.with_suffix(".jpg")
    result = legacy_app.subprocess.run(
        [ffmpeg, "-y", "-i", str(background_path), str(jpg_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not jpg_path.exists():
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Could not convert the custom background image: {detail}")
    return jpg_path


def jpeg_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise RuntimeError("Custom background must be a valid JPEG image for PDF export.")
    index = 2
    while index < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        segment_length = int.from_bytes(data[index:index + 2], "big")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3}:
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            return width, height
        index += segment_length
    raise RuntimeError("Could not determine the custom background dimensions.")


def wrap_lines(text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if pdf_text_width(trial, font_size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


PDF_TEXT_TRANSLATIONS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2026": "...",
        "\u00a0": " ",
    }
)


def pdf_safe_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").translate(PDF_TEXT_TRANSLATIONS)
    return "".join(character if ord(character) < 128 else "?" for character in normalized)


def fit_pdf_layout(job: dict) -> dict:
    topic = legacy_app.clean_spacing(job.get("topic", ""))
    rabbi_name = legacy_app.clean_spacing(job.get("rabbi_name", ""))
    paragraphs = legacy_app.split_pamphlet_body(
        legacy_app.clean_spacing(job.get("edited_one_pager") or job.get("one_pager") or ""),
        topic=topic,
        rabbi_name=rabbi_name,
    )
    if not paragraphs:
        raise RuntimeError("No article text is available to export.")

    forced_size = float(job.get("pdf_font_size") or 12.0)
    requested_line_spacing = float(job.get("pdf_line_spacing") or 1.0)
    body_align = (job.get("pdf_body_align") or "left").strip().lower()
    line_spacing = requested_line_spacing
    font_size = forced_size
    body_width = 504
    line_height = font_size * line_spacing * 1.18
    paragraph_gap = max(6, font_size * 0.8)
    wrapped: list[list[str]] = []
    for paragraph in paragraphs:
        wrapped.append(wrap_lines(paragraph, "Times-Roman", font_size, body_width))
    return {
        "font_size": font_size,
        "line_height": line_height,
        "paragraph_gap": paragraph_gap,
        "paragraphs": wrapped,
        "body_width": body_width,
        "body_align": body_align,
        "line_spacing": line_spacing,
    }


def build_pdf_bytes(job: dict) -> BytesIO:
    try:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph
    except ImportError as exc:
        raise RuntimeError("reportlab is required for PDF export. Restart the backend after installing updated requirements.") from exc

    topic = pdf_safe_text(legacy_app.clean_spacing(job.get("topic", "")))
    rabbi_name = pdf_safe_text(legacy_app.clean_spacing(job.get("rabbi_name", "")))
    paragraphs = legacy_app.split_pamphlet_body(
        legacy_app.clean_spacing(job.get("edited_one_pager") or job.get("one_pager") or ""),
        topic=topic,
        rabbi_name=rabbi_name,
    )
    if not paragraphs:
        raise RuntimeError("No article text is available to export.")

    buffer = BytesIO()
    width, height = letter
    mode = (job.get("pdf_background_mode") or "default").strip().lower()
    header_left = pdf_safe_text(legacy_app.clean_spacing(job.get("pdf_header_left", DEFAULT_HEADER_LEFT)))
    header_right = pdf_safe_text(legacy_app.clean_spacing(job.get("pdf_header_right", DEFAULT_HEADER_RIGHT)))
    footer_text = pdf_safe_text(legacy_app.clean_spacing(job.get("pdf_footer_text", DEFAULT_FOOTER_TEXT)))
    header_left_size = float(job.get("pdf_header_left_size") or 15.0)
    header_right_size = float(job.get("pdf_header_right_size") or 14.0)
    footer_size = float(job.get("pdf_footer_size") or 10.0)
    header_y = float(job.get("pdf_header_y") or 738.0)
    footer_y = float(job.get("pdf_footer_y") or 30.0)
    header_left_align = (job.get("pdf_header_left_align") or "left").strip().lower()
    header_right_align = (job.get("pdf_header_right_align") or "right").strip().lower()
    footer_align = (job.get("pdf_footer_align") or "center").strip().lower()
    body_align = (job.get("pdf_body_align") or "left").strip().lower()
    font_size = max(float(job.get("pdf_font_size") or 12.0), 0.1)
    line_spacing = max(float(job.get("pdf_line_spacing") or 1.0), 0.1)
    leading = max(font_size * line_spacing * 1.15, font_size + 0.1)
    paragraph_gap = max(6.0, font_size * 0.8)

    background_reader = None
    if mode == "custom":
        stored_background = job.get("pdf_custom_background") or ""
        if not stored_background:
            raise RuntimeError("A custom background is selected, but no background image has been uploaded.")
        with stored_file_path(stored_background, suffix=Path(str(stored_background)).suffix or ".jpg") as background_path:
            jpeg_path = ensure_jpeg_background(Path(background_path))
            background_reader = ImageReader(str(jpeg_path))

    def draw_aligned_text(canvas_obj, text: str, font_name: str, size: float, align: str, default_x: float, y: float, color: str):
        if not text:
            return
        canvas_obj.saveState()
        canvas_obj.setFillColor(HexColor(color))
        canvas_obj.setFont(font_name, size)
        text_width = canvas_obj.stringWidth(text, font_name, size)
        if align == "center":
            x = max(36.0, (width - text_width) / 2.0)
        elif align == "right":
            x = max(36.0, width - text_width - 46.0)
        else:
            x = default_x
        canvas_obj.drawString(x, y, text)
        canvas_obj.restoreState()

    def draw_page(canvas_obj, include_title: bool):
        if background_reader is not None:
            canvas_obj.drawImage(background_reader, 0, 0, width=width, height=height, preserveAspectRatio=False, mask='auto')
        if mode == "default":
            canvas_obj.saveState()
            canvas_obj.setStrokeColor(HexColor("#bda18f"))
            canvas_obj.setLineWidth(1)
            canvas_obj.rect(18, 18, 576, 756)
            canvas_obj.line(46, 724, 566, 724)
            canvas_obj.line(46, 46, 566, 46)
            canvas_obj.restoreState()
        draw_aligned_text(canvas_obj, header_left, "Times-Roman" if mode != "custom" else "Times-Bold", header_left_size, header_left_align, 64, header_y, "#9e7a6b")
        draw_aligned_text(canvas_obj, header_right, "Times-Italic", header_right_size, header_right_align, 404, header_y, "#9e7a6b")
        if include_title:
            draw_aligned_text(canvas_obj, topic, "Times-Bold", 19, "center", 76, 690, "#333333")
            byline = f"By {rabbi_name}" if rabbi_name else ""
            draw_aligned_text(canvas_obj, byline, "Times-Italic", 12, "center", 100, 670, "#5c514a")
        draw_aligned_text(canvas_obj, footer_text, "Times-Roman", footer_size, footer_align, 160, footer_y, "#5c514a")

    first_page_top = min(620, header_y - 118)
    continuation_page_top = min(690, header_y - 34)
    bottom_limit = max(76, footer_y + 48)
    first_frame = Frame(54, bottom_limit, 504, max(36, first_page_top - bottom_limit), leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="first")
    later_frame = Frame(54, bottom_limit, 504, max(36, continuation_page_top - bottom_limit), leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0, id="later")

    align_map = {
        "left": TA_LEFT,
        "center": TA_CENTER,
        "right": TA_RIGHT,
        "justify": TA_JUSTIFY,
    }
    body_style = ParagraphStyle(
        "Body",
        fontName="Times-Roman",
        fontSize=font_size,
        leading=leading,
        alignment=align_map.get(body_align, TA_LEFT),
        spaceBefore=0,
        spaceAfter=paragraph_gap,
    )

    story = [Paragraph(escape(pdf_safe_text(paragraph)), body_style) for paragraph in paragraphs]
    doc = BaseDocTemplate(buffer, pagesize=letter, leftMargin=54, rightMargin=54, topMargin=height - first_page_top, bottomMargin=bottom_limit)
    doc.addPageTemplates(
        [
            PageTemplate(id="First", frames=[first_frame], onPage=lambda canv, doc_obj: draw_page(canv, True), autoNextPageTemplate="Later"),
            PageTemplate(id="Later", frames=[later_frame], onPage=lambda canv, doc_obj: draw_page(canv, False)),
        ]
    )
    doc.build(story)
    buffer.seek(0)
    return buffer


def build_png_bytes(job: dict) -> BytesIO:
    try:
        import fitz
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for PNG export. Restart the backend after installing updated requirements.") from exc

    pdf_buffer = build_pdf_bytes(job)
    pdf_bytes = pdf_buffer.getvalue()
    if not pdf_bytes:
        raise RuntimeError("Could not build the PDF needed for PNG export.")

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise RuntimeError(f"Could not open the PDF for PNG export: {exc}") from exc

    images: list[Image.Image] = []
    separator = 28
    try:
        zoom_matrix = fitz.Matrix(2.0, 2.0)
        for page in document:
            pixmap = page.get_pixmap(matrix=zoom_matrix, alpha=False)
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            images.append(image)
    finally:
        document.close()

    if not images:
        raise RuntimeError("The generated PDF had no pages to convert into PNG.")

    if len(images) == 1:
        combined = images[0]
    else:
        max_width = max(image.width for image in images)
        total_height = sum(image.height for image in images) + separator * (len(images) - 1)
        combined = Image.new("RGB", (max_width, total_height), "white")
        cursor_y = 0
        for image in images:
            x = (max_width - image.width) // 2
            combined.paste(image, (x, cursor_y))
            cursor_y += image.height + separator

    output = BytesIO()
    combined.save(output, format="PNG")
    output.seek(0)
    return output


def xml_run(text: str, *, bold: bool = False, italic: bool = False, size_half_points: int = 24) -> str:
    formatting = ['<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>']
    formatting.append(f"<w:sz w:val=\"{size_half_points}\"/>")
    formatting.append(f"<w:szCs w:val=\"{size_half_points}\"/>")
    if bold:
        formatting.append("<w:b/>")
    if italic:
        formatting.append("<w:i/>")
    escaped = escape(text)
    return "<w:r>" f"<w:rPr>{''.join(formatting)}</w:rPr>" f"<w:t xml:space=\"preserve\">{escaped}</w:t>" "</w:r>"


def xml_paragraph(text: str, *, align: str = "both", bold: bool = False, italic: bool = False, size_half_points: int = 24, spacing_after: int = 120) -> str:
    return (
        "<w:p>"
        f"<w:pPr><w:jc w:val=\"{align}\"/><w:spacing w:after=\"{spacing_after}\"/></w:pPr>"
        f"{xml_run(text, bold=bold, italic=italic, size_half_points=size_half_points)}"
        "</w:p>"
    )


def build_docx_bytes(job: dict) -> BytesIO:
    topic = legacy_app.clean_spacing(job.get("topic", ""))
    rabbi_name = legacy_app.clean_spacing(job.get("rabbi_name", ""))
    header_left = legacy_app.clean_spacing(job.get("pdf_header_left", DEFAULT_HEADER_LEFT))
    body_align = (job.get("pdf_body_align") or "left").strip().lower()
    body_align_docx = "both" if body_align == "justify" else ("both" if body_align == "left" else body_align)
    paragraphs = legacy_app.split_pamphlet_body(
        legacy_app.clean_spacing(job.get("edited_one_pager") or job.get("one_pager") or ""),
        topic=topic,
        rabbi_name=rabbi_name,
    )
    document_parts = [xml_paragraph(header_left or PRODUCT_NAME, align="center", bold=True, size_half_points=30, spacing_after=80)]
    if topic:
        document_parts.append(xml_paragraph(topic, align="center", bold=True, size_half_points=28, spacing_after=60))
    if rabbi_name:
        document_parts.append(xml_paragraph(f"By {rabbi_name}", align="center", italic=True, size_half_points=24, spacing_after=120))
    for paragraph_text in paragraphs:
        document_parts.append(xml_paragraph(paragraph_text, align=body_align_docx, size_half_points=24, spacing_after=140))

    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
 xmlns:v="urn:schemas-microsoft-com:vml"
 xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:w10="urn:schemas-microsoft-com:office:word"
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
 xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
 xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk"
 xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml"
 xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
 mc:Ignorable="w14 wp14">
  <w:body>
    {''.join(document_parts)}
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>"""

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        docx.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        docx.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>""",
        )
        docx.writestr("word/document.xml", document_xml)
        docx.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(topic or PRODUCT_NAME)}</dc:title>
  <dc:creator>{escape(rabbi_name or PRODUCT_NAME)}</dc:creator>
  <cp:lastModifiedBy>{escape(PRODUCT_NAME)}</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{datetime.utcnow().replace(microsecond=0).isoformat()}Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{datetime.utcnow().replace(microsecond=0).isoformat()}Z</dcterms:modified>
</cp:coreProperties>""",
        )
        docx.writestr(
            "docProps/app.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>{escape(PRODUCT_NAME)}</Application>
</Properties>""",
        )
    buffer.seek(0)
    return buffer


@app.route("/api/jobs", methods=["GET"])
@require_auth
def jobs():
    return jsonify(
        {
            "jobs": [sanitize_job_for_user(job, request.current_user) for job in list_jobs(request.current_user["id"])],
            "subscription_active": user_has_subscription(request.current_user),
        }
    )


@app.route("/api/tools/hebrew-terms", methods=["GET"])
@require_auth
def get_hebrew_terms():
    return jsonify(load_terms_library())


@app.route("/api/tools/hebrew-terms", methods=["PUT"])
@require_auth
def save_hebrew_terms():
    payload = request.get_json(silent=True) or {}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return jsonify({"error": "Entries must be a list."}), 400
    normalized_entries = []
    for entry in entries:
        display = legacy_app.clean_spacing(entry.get("display", ""))
        if not display:
            continue
        normalized_entries.append(
            {
                "canonical": legacy_app.clean_spacing(entry.get("canonical", "")) or display,
                "display": display,
                "variants": [
                    legacy_app.clean_spacing(variant)
                    for variant in entry.get("variants", [])
                    if legacy_app.clean_spacing(variant)
                ],
            }
        )
    library = {"entries": normalized_entries}
    save_terms_library(library)
    return jsonify({"ok": True, "library": library})


@app.route("/api/jobs", methods=["POST"])
@require_auth
def create_job():
    rabbi_name = legacy_app.clean_spacing(request.form.get("rabbi_name", ""))
    topic = legacy_app.clean_spacing(request.form.get("topic", ""))
    transcript_text = legacy_app.clean_spacing(request.form.get("transcript_text", ""))
    pamphlet_text = legacy_app.clean_spacing(request.form.get("pamphlet_text", ""))
    transliteration_mode = (request.form.get("transliteration_mode", "auto") or "auto").strip().lower()
    audio = request.files.get("audio")

    if not rabbi_name:
        return jsonify({"error": "Rabbi name is required."}), 400
    if not topic:
        return jsonify({"error": "Topic is required."}), 400
    if transliteration_mode not in {"auto", "manual-review-heavy", "hebrew"}:
        return jsonify({"error": "Translation rules mode is invalid."}), 400
    if not pamphlet_text and not transcript_text and (not audio or not audio.filename):
        return jsonify({"error": "Choose audio or provide transcript/article text."}), 400

    job_id = create_job_record(request.current_user["id"], rabbi_name, topic, transliteration_mode)

    if pamphlet_text:
        update_job(job_id, source_kind="pamphlet", pamphlet_input=pamphlet_text)
        worker = threading.Thread(target=process_job, args=(job_id, "pamphlet", None, None, pamphlet_text), daemon=True)
    elif transcript_text:
        update_job(job_id, source_kind="transcript", transcript_input=transcript_text)
        worker = threading.Thread(target=process_job, args=(job_id, "transcript", None, transcript_text, None), daemon=True)
    else:
        extension = Path(audio.filename).suffix.lower()
        if extension not in {".mp3", ".mp4"}:
            return jsonify({"error": "Only .mp3 and .mp4 files are supported."}), 400
        source_path = store_uploaded_file(audio, UPLOAD_DIR)
        update_job(job_id, source_kind="audio", source_path=str(source_path))
        worker = threading.Thread(target=process_job, args=(job_id, "audio", source_path, None, None), daemon=True)

    if WORKER_MODE == "inline":
        worker.start()
    else:
        update_job(job_id, status="queued", progress=6, message="Job queued for hosted processing.")
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/<job_id>", methods=["GET"])
@require_auth
def job_detail(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(sanitize_job_for_user(job, request.current_user))


@app.route("/api/jobs/<job_id>/review", methods=["POST"])
@require_auth
def review_job(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job.get("status") != "needs_review":
        return jsonify({"error": "This job is not waiting for review."}), 400

    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers") or {}
    review_items = job.get("review_items") or []
    missing = [item["id"] for item in review_items if not legacy_app.clean_spacing(answers.get(item["id"], ""))]
    if missing:
        return jsonify({"error": "Please clarify every flagged Hebrew segment before continuing."}), 400

    legacy_app.remember_clarifications(review_items, answers)
    transcript_source = job.get("final_transcript") or job.get("raw_transcript") or ""
    final_transcript = legacy_app.apply_clarifications(transcript_source, review_items, answers)
    final_transcript = finalize_transcript_for_mode(final_transcript, job.get("transliteration_mode", "auto"))
    update_job(
        job_id,
        review_items=[
            {**item, "clarification": legacy_app.clean_spacing(answers.get(item["id"], ""))}
            for item in review_items
        ],
        review_status="completed",
        final_transcript=final_transcript,
        status="running",
        progress=78,
        message="Clarifications saved. Writing the article in the speaker's voice.",
    )

    def continue_generation():
        try:
            current = get_job(job_id)
            if not current or current.get("status") == "canceled":
                return
            owner = fetch_user_by_id(current["user_id"])
            glossary_entries = legacy_app.matched_glossary_entries(final_transcript)
            voice_profile = analyze_voice(final_transcript)
            update_job(
                job_id,
                voice_profile=json.dumps(voice_profile),
                status="running",
                progress=84,
                message="Voice profile ready. Drafting the article.",
            )
            article = generate_article(job["rabbi_name"], job["topic"], final_transcript, glossary_entries, voice_profile)
            billing_state = "unlocked" if user_has_subscription(owner) else "locked"
            message = "Article ready." if billing_state == "unlocked" else "Article ready. Unlock it to view the transcript and exports."
            update_job(job_id, status="completed", progress=100, message=message, one_pager=article, edited_one_pager=article, billing_state=billing_state)
        except Exception as exc:
            update_job(job_id, status="failed", progress=100, message="Processing failed.", error=f"Processing failed: {exc}")

    threading.Thread(target=continue_generation, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>", methods=["PATCH"])
@require_auth
def update_job_payload(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if job.get("status") == "completed" and not job_is_unlocked(job, request.current_user):
        return jsonify({"error": "Unlock this completed article before editing its content or exports."}), 402
    payload = request.get_json(silent=True) or {}
    edited_topic = legacy_app.clean_spacing(payload.get("topic", job.get("topic", "")))
    edited_text = legacy_app.clean_spacing(
        payload.get("edited_one_pager", job.get("edited_one_pager") or job.get("one_pager") or "")
    )
    transliteration_mode = (payload.get("transliteration_mode", job.get("transliteration_mode", "auto")) or "auto").strip().lower()
    if not edited_topic:
        return jsonify({"error": "Topic is required."}), 400
    if not edited_text:
        return jsonify({"error": "Article text cannot be empty."}), 400
    if transliteration_mode not in {"auto", "manual-review-heavy", "hebrew"}:
        return jsonify({"error": "Translation rules mode is invalid."}), 400

    try:
        raw_line_spacing = str(payload.get("pdf_line_spacing", job.get("pdf_line_spacing", 1.0)) or "").strip().replace(",", ".")
        raw_font_size = str(payload.get("pdf_font_size", job.get("pdf_font_size", 12.0)) or "").strip().replace(",", ".")
        if not raw_line_spacing:
            raise ValueError("line spacing missing")
        if not raw_font_size:
            raise ValueError("font size missing")
        line_spacing = float(raw_line_spacing)
        font_size = float(raw_font_size)
        header_left_size = float(payload.get("pdf_header_left_size", job.get("pdf_header_left_size", 15.0)) or 15.0)
        header_right_size = float(payload.get("pdf_header_right_size", job.get("pdf_header_right_size", 14.0)) or 14.0)
        footer_size = float(payload.get("pdf_footer_size", job.get("pdf_footer_size", 10.0)) or 10.0)
        header_y = float(payload.get("pdf_header_y", job.get("pdf_header_y", 738.0)) or 738.0)
        footer_y = float(payload.get("pdf_footer_y", job.get("pdf_footer_y", 30.0)) or 30.0)
    except (TypeError, ValueError):
        return jsonify({"error": "Export settings are invalid."}), 400

    background_mode = (payload.get("pdf_background_mode", job.get("pdf_background_mode", "default")) or "default").strip().lower()
    if background_mode not in {"default", "blank", "custom"}:
        return jsonify({"error": "Background mode must be default, blank, or custom."}), 400
    header_left_align = (payload.get("pdf_header_left_align", job.get("pdf_header_left_align", "left")) or "left").strip().lower()
    header_right_align = (payload.get("pdf_header_right_align", job.get("pdf_header_right_align", "right")) or "right").strip().lower()
    footer_align = (payload.get("pdf_footer_align", job.get("pdf_footer_align", "center")) or "center").strip().lower()
    body_align = (payload.get("pdf_body_align", job.get("pdf_body_align", "left")) or "left").strip().lower()
    if header_left_align not in {"left", "center", "right"} or header_right_align not in {"left", "center", "right"} or footer_align not in {"left", "center", "right"} or body_align not in {"left", "center", "right", "justify"}:
        return jsonify({"error": "Alignment settings are invalid."}), 400

    if not 0.5 <= line_spacing <= 3.0:
        return jsonify({"error": "Body line spacing must be between 0.5 and 3.0."}), 400
    if font_size <= 0:
        return jsonify({"error": "Body font size must be greater than 0."}), 400

    update_job(
        job_id,
        topic=edited_topic,
        edited_one_pager=edited_text,
        transliteration_mode=transliteration_mode,
        pdf_line_spacing=line_spacing,
        pdf_font_size=font_size,
        pdf_background_mode=background_mode,
        pdf_header_left=legacy_app.clean_spacing(payload.get("pdf_header_left", job.get("pdf_header_left", DEFAULT_HEADER_LEFT))),
        pdf_header_right=legacy_app.clean_spacing(payload.get("pdf_header_right", job.get("pdf_header_right", DEFAULT_HEADER_RIGHT))),
        pdf_footer_text=legacy_app.clean_spacing(payload.get("pdf_footer_text", job.get("pdf_footer_text", DEFAULT_FOOTER_TEXT))),
        pdf_header_left_size=header_left_size,
        pdf_header_right_size=header_right_size,
        pdf_footer_size=footer_size,
        pdf_header_left_align=header_left_align,
        pdf_header_right_align=header_right_align,
        pdf_footer_align=footer_align,
        pdf_body_align=body_align,
        pdf_header_y=header_y,
        pdf_footer_y=footer_y,
    )
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
@require_auth
def cancel_job(job_id: str):
    ok = cancel_job_record(job_id, request.current_user["id"])
    if not ok:
        return jsonify({"error": "Job not found."}), 404
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
@require_auth
def delete_job(job_id: str):
    ok = delete_job_record(job_id, request.current_user["id"])
    if not ok:
        return jsonify({"error": "Job not found."}), 404
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/background", methods=["POST"])
@require_auth
def upload_background(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    background = request.files.get("background")
    if not background or not background.filename:
        return jsonify({"error": "Choose a background image to upload."}), 400
    extension = Path(background.filename).suffix.lower()
    if extension not in {".png", ".jpg", ".jpeg"}:
        return jsonify({"error": "Backgrounds must be .png, .jpg, or .jpeg."}), 400
    with tempfile.TemporaryDirectory() as temp_dir:
        raw_path = Path(temp_dir) / f"{job_id}_{legacy_app.safe_filename(background.filename)}"
        background.save(raw_path)
        final_path = ensure_jpeg_background(raw_path)
        stored_path = storage_put_bytes(final_path.read_bytes(), storage_key("backgrounds", f"{job_id}_{final_path.name}"))
    update_job(job_id, pdf_custom_background=stored_path)
    return jsonify({"ok": True})


@app.route("/api/jobs/<job_id>/pdf", methods=["GET"])
@require_auth
def download_pdf(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    try:
        ensure_job_content_access(job, request.current_user)
        return send_file(build_pdf_bytes(job), mimetype="application/pdf", as_attachment=True, download_name=legacy_app.pdf_filename(job))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/jobs/<job_id>/pdf/preview", methods=["GET"])
@require_auth
def preview_pdf(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    try:
        ensure_job_content_access(job, request.current_user)
        return send_file(build_pdf_bytes(job), mimetype="application/pdf", as_attachment=False, download_name=legacy_app.pdf_filename(job))
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/jobs/<job_id>/docx", methods=["GET"])
@require_auth
def download_docx(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    try:
        ensure_job_content_access(job, request.current_user)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 402
    return send_file(
        build_docx_bytes(job),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=legacy_app.docx_filename(job),
    )


@app.route("/api/jobs/<job_id>/png", methods=["GET"])
@require_auth
def download_png(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    try:
        ensure_job_content_access(job, request.current_user)
        png_name = f"{legacy_app.pdf_filename(job).rsplit('.', 1)[0]}.png"
        return send_file(
            build_png_bytes(job),
            mimetype="image/png",
            as_attachment=True,
            download_name=png_name,
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/jobs/<job_id>/transcript.txt", methods=["GET"])
@require_auth
def download_transcript_text(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    try:
        ensure_job_content_access(job, request.current_user)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 402
    transcript = legacy_app.clean_spacing(job.get("final_transcript") or job.get("raw_transcript") or "")
    if not transcript:
        return jsonify({"error": "No transcript is available for this job."}), 400
    buffer = BytesIO(transcript.encode("utf-8"))
    buffer.seek(0)
    return send_file(buffer, mimetype="text/plain", as_attachment=True, download_name=f"{legacy_app.pdf_filename(job).rsplit('.', 1)[0]}.transcript.txt")


@app.route("/api/jobs/<job_id>/article.txt", methods=["GET"])
@require_auth
def download_article_text(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    try:
        ensure_job_content_access(job, request.current_user)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 402
    article = legacy_app.clean_spacing(job.get("edited_one_pager") or job.get("one_pager") or "")
    if not article:
        return jsonify({"error": "No article is available for this job."}), 400
    buffer = BytesIO(article.encode("utf-8"))
    buffer.seek(0)
    return send_file(buffer, mimetype="text/plain", as_attachment=True, download_name=f"{legacy_app.pdf_filename(job).rsplit('.', 1)[0]}.article.txt")


@app.route("/api/billing/checkout", methods=["POST"])
@require_auth
def billing_checkout():
    if not STRIPE_ENABLED:
        return jsonify({"error": "Stripe is not configured yet."}), 503
    payload = request.get_json(silent=True) or {}
    checkout_kind = (payload.get("kind") or "").strip().lower()
    job_id = payload.get("job_id")
    customer_id = stripe_customer_for_user(request.current_user)

    if checkout_kind == "job_unlock":
        if not job_id:
            return jsonify({"error": "A job id is required for single unlock checkout."}), 400
        job = get_job(job_id, request.current_user["id"])
        if not job:
            return jsonify({"error": "Job not found."}), 404
        if job_is_unlocked(job, request.current_user):
            return jsonify({"error": "This job is already unlocked."}), 400
        session_obj = stripe.checkout.Session.create(
            mode="payment",
            customer=customer_id,
            success_url=f"{STRIPE_SUCCESS_URL}&job_id={job_id}",
            cancel_url=f"{STRIPE_CANCEL_URL}&job_id={job_id}",
            metadata={"kind": "job_unlock", "job_id": job_id, "user_id": request.current_user["id"]},
            line_items=[
                {
                    "price_data": {
                        "currency": STRIPE_CURRENCY,
                        "product_data": {"name": "Single Article Unlock"},
                        "unit_amount": SINGLE_UNLOCK_PRICE_CENTS,
                    },
                    "quantity": 1,
                }
            ],
        )
        update_job(job_id, stripe_checkout_session_id=session_obj["id"])
        return jsonify({"url": session_obj["url"]})

    if checkout_kind == "subscription":
        session_obj = stripe.checkout.Session.create(
            mode="subscription",
            customer=customer_id,
            success_url=STRIPE_SUCCESS_URL,
            cancel_url=STRIPE_CANCEL_URL,
            metadata={"kind": "subscription", "user_id": request.current_user["id"]},
            subscription_data={"metadata": {"kind": "subscription", "user_id": request.current_user["id"]}},
            line_items=[
                {
                    "price_data": {
                        "currency": STRIPE_CURRENCY,
                        "product_data": {"name": "Torah Center Unlimited"},
                        "unit_amount": MONTHLY_PLAN_PRICE_CENTS,
                        "recurring": {"interval": "month"},
                    },
                    "quantity": 1,
                }
            ],
        )
        return jsonify({"url": session_obj["url"]})

    return jsonify({"error": "Billing option is invalid."}), 400


@app.route("/api/billing/status", methods=["GET"])
@require_auth
def billing_status():
    return jsonify(
        {
            "subscription_active": user_has_subscription(request.current_user),
            "subscription_status": request.current_user.get("subscription_status"),
            "single_unlock_price_cents": SINGLE_UNLOCK_PRICE_CENTS,
            "monthly_price_cents": MONTHLY_PLAN_PRICE_CENTS,
            "stripe_enabled": STRIPE_ENABLED,
        }
    )


@app.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    if not STRIPE_ENABLED or not STRIPE_WEBHOOK_SECRET:
        return jsonify({"error": "Stripe webhook is not configured."}), 503
    payload = request.data
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        return jsonify({"error": f"Invalid webhook: {exc}"}), 400

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        mode = data.get("mode")
        metadata = data.get("metadata") or {}
        if mode == "payment" and metadata.get("kind") == "job_unlock":
            unlock_job(
                metadata.get("job_id", ""),
                payment_intent_id=data.get("payment_intent"),
                checkout_session_id=data.get("id"),
            )
        elif mode == "subscription":
            user = fetch_user_by_stripe_customer_id(data.get("customer"))
            if user:
                set_user_subscription(
                    user["id"],
                    status="active",
                    subscription_id=data.get("subscription"),
                )
    elif event_type in {"customer.subscription.updated", "customer.subscription.created"}:
        user = fetch_user_by_stripe_customer_id(data.get("customer")) or fetch_user_by_stripe_subscription_id(data.get("id"))
        if user:
            period_end = data.get("current_period_end")
            period_end_iso = datetime.utcfromtimestamp(period_end).replace(microsecond=0).isoformat() + "Z" if period_end else None
            set_user_subscription(
                user["id"],
                status=data.get("status", "active"),
                subscription_id=data.get("id"),
                current_period_end=period_end_iso,
            )
    elif event_type in {"customer.subscription.deleted", "invoice.payment_failed"}:
        customer_id = data.get("customer")
        subscription_id = data.get("subscription") or data.get("id")
        user = fetch_user_by_stripe_customer_id(customer_id) or fetch_user_by_stripe_subscription_id(subscription_id)
        if user:
            set_user_subscription(user["id"], status="inactive", subscription_id=None, current_period_end=None)

    return jsonify({"received": True})


@app.route("/api/jobs/<job_id>/share", methods=["GET"])
@require_auth
def job_share(job_id: str):
    job = get_job(job_id, request.current_user["id"])
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(
        {
            "id": job["id"],
            "topic": job["topic"],
            "rabbi_name": job["rabbi_name"],
            "status": job["status"],
            "edited_one_pager": job.get("edited_one_pager"),
            "created_at": job["created_at"],
        }
    )


if __name__ == "__main__":
    init_db()
    ensure_admin()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8010"))
    debug = os.getenv("APP_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)
