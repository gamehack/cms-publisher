from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3

from .errors import StateConflict
from .models import Article


SCHEMA_VERSION = 1
STATES = {"PENDING", "INTENT", "SUCCESS", "UNKNOWN", "FAILED"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        created = not self.path.exists()
        db = sqlite3.connect(self.path, timeout=30)
        if created and self.path.exists():
            os.chmod(self.path, 0o600)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS articles (
                    source_key TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    period TEXT NOT NULL,
                    notion_page_id TEXT NOT NULL,
                    notion_title TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    body_markdown TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    content_version INTEGER NOT NULL,
                    snapshotted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    source_key TEXT PRIMARY KEY REFERENCES articles(source_key) ON DELETE RESTRICT,
                    state TEXT NOT NULL CHECK(state IN ('PENDING','INTENT','SUCCESS','UNKNOWN','FAILED')),
                    receipt TEXT,
                    detail TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS manual_resolutions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL REFERENCES articles(source_key),
                    prior_state TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
            """)
            row = db.execute("SELECT version FROM schema_meta").fetchone()
            if row is None:
                db.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif row[0] != SCHEMA_VERSION:
                raise StateConflict(f"Unsupported state schema version: {row[0]}")
            db.commit()

    def save_snapshot(self, article: Article) -> None:
        self.initialize()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute(
                "SELECT content_sha256,notion_page_id FROM articles WHERE source_key=?",
                (article.source_key,),
            ).fetchone()
            if found:
                if found["content_sha256"] == "legacy" and found["notion_page_id"] == "legacy":
                    # Older qjai-text-publisher import-history inserted a
                    # metadata placeholder. The CMS delivery status remains
                    # untouched, so SUCCESS/UNKNOWN can never be replayed.
                    db.execute("""
                        UPDATE articles SET profile=?,period=?,notion_page_id=?,notion_title=?,
                            title=?,summary=?,body_markdown=?,content_sha256=?,
                            content_version=?,snapshotted_at=? WHERE source_key=?
                    """, (article.profile, article.period, article.notion_page_id,
                          article.notion_title, article.title, article.summary,
                          article.body_markdown, article.content_sha256, article.version,
                          utcnow(), article.source_key))
                    db.commit()
                    return
                if (found["content_sha256"] != article.content_sha256 or
                        found["notion_page_id"] != article.notion_page_id):
                    raise StateConflict("Existing sourceKey snapshot differs; refusing overwrite")
                db.commit()
                return
            db.execute("""
                INSERT INTO articles(source_key,profile,period,notion_page_id,notion_title,
                    title,summary,body_markdown,content_sha256,content_version,snapshotted_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (article.source_key, article.profile, article.period, article.notion_page_id,
                  article.notion_title, article.title, article.summary, article.body_markdown,
                  article.content_sha256, article.version, utcnow()))
            db.execute("INSERT INTO deliveries(source_key,state,updated_at) VALUES (?,?,?)",
                       (article.source_key, "PENDING", utcnow()))
            db.commit()

    def delivery_state(self, source_key: str) -> str | None:
        self.initialize()
        with self.connect() as db:
            row = db.execute("SELECT state FROM deliveries WHERE source_key=?", (source_key,)).fetchone()
            return str(row[0]) if row else None

    def reserve(self, source_key: str) -> None:
        self.initialize()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE deliveries SET state='INTENT',updated_at=? WHERE source_key=? AND state='PENDING'",
                (utcnow(), source_key),
            ).rowcount
            if changed != 1:
                raise StateConflict("CMS delivery is not PENDING; reconciliation required")
            db.commit()

    def mark(self, source_key: str, state: str, *, receipt: str | None = None,
             detail: str | None = None) -> None:
        if state not in STATES:
            raise ValueError("Invalid delivery state")
        self.initialize()
        with self.connect() as db:
            changed = db.execute("""
                UPDATE deliveries SET state=?,receipt=?,detail=?,updated_at=? WHERE source_key=?
            """, (state, receipt, detail, utcnow(), source_key)).rowcount
            if changed != 1:
                raise StateConflict("Missing CMS delivery row")
            db.commit()

    def rearm_verified_absent(self, source_key: str, evidence: str) -> None:
        if (not isinstance(evidence, str) or not 20 <= len(evidence.strip()) <= 500 or
                any(c in evidence for c in "\r\n")):
            raise StateConflict("Rearm requires 20-500 characters of single-line evidence")
        self.initialize()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM deliveries WHERE source_key=?", (source_key,)).fetchone()
            if not row or row["state"] not in {"UNKNOWN", "INTENT"}:
                raise StateConflict("Only UNKNOWN or INTENT may be manually rearmed")
            db.execute("""
                INSERT INTO manual_resolutions(source_key,prior_state,decision,evidence,recorded_at)
                VALUES (?,?,?,?,?)
            """, (source_key, row["state"], "VERIFIED_ABSENT", evidence.strip(), utcnow()))
            db.execute("""
                UPDATE deliveries SET state='PENDING',receipt=NULL,detail=?,updated_at=?
                WHERE source_key=?
            """, ("manual verified-absent; refer to manual_resolutions", utcnow(), source_key))
            db.commit()

    def import_text_state(self, old_path: str | Path) -> int:
        """One-way import of all old CMS states, without touching WeChat data.

        An already existing target row must match exactly; never overwrite a
        new-run state with stale history. Run before first live CMS dispatch.
        """
        try:
            source = Path(old_path).resolve(strict=True)
        except (OSError, RuntimeError):
            raise StateConflict("Legacy state SQLite file is not accessible") from None
        if Path(old_path).is_symlink() or not source.is_file() or source == self.path.resolve():
            raise StateConflict("Legacy state must be a distinct regular SQLite file")
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old:
            old.row_factory = sqlite3.Row
            rows = old.execute("""
                SELECT a.source_key,a.profile,a.period,a.notion_page_id,a.notion_title,
                       a.title,a.summary,a.body_markdown,a.content_sha256,
                       a.content_version,a.snapshotted_at,d.state,d.receipt,d.detail,d.updated_at
                FROM articles a JOIN deliveries d USING(source_key)
                WHERE d.channel='cms' ORDER BY a.source_key
            """).fetchall()
        self.initialize()
        imported = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in rows:
                old_row = dict(row)
                if old_row["state"] not in STATES:
                    raise StateConflict("Invalid old CMS state")
                existing = db.execute("SELECT * FROM deliveries WHERE source_key=?",
                                      (old_row["source_key"],)).fetchone()
                if existing:
                    if (existing["state"] != old_row["state"] or
                            existing["receipt"] != old_row["receipt"]):
                        raise StateConflict("Target CMS state exists and differs; refusing import")
                    article_row = db.execute(
                        "SELECT content_sha256,notion_page_id FROM articles WHERE source_key=?",
                        (old_row["source_key"],),
                    ).fetchone()
                    if (article_row["content_sha256"] != old_row["content_sha256"] or
                            article_row["notion_page_id"] != old_row["notion_page_id"]):
                        raise StateConflict("Target CMS snapshot differs from old state")
                    continue
                db.execute("""
                    INSERT INTO articles(source_key,profile,period,notion_page_id,notion_title,
                        title,summary,body_markdown,content_sha256,content_version,snapshotted_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, tuple(old_row[k] for k in (
                    "source_key", "profile", "period", "notion_page_id", "notion_title",
                    "title", "summary", "body_markdown", "content_sha256",
                    "content_version", "snapshotted_at")))
                db.execute("""
                    INSERT INTO deliveries(source_key,state,receipt,detail,updated_at)
                    VALUES (?,?,?,?,?)
                """, tuple(old_row[k] for k in (
                    "source_key", "state", "receipt", "detail", "updated_at")))
                imported += 1
            db.commit()
        return imported

    def rows(self, profile: str | None = None) -> list[dict[str, object]]:
        self.initialize()
        sql = """
            SELECT a.source_key,a.profile,a.period,a.content_sha256,d.state,d.updated_at
            FROM articles a JOIN deliveries d USING(source_key)
        """
        args: tuple[object, ...] = ()
        if profile:
            sql += " WHERE a.profile=?"
            args = (profile,)
        sql += " ORDER BY a.source_key"
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]
