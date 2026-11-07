from __future__ import annotations

import sqlite3

import httpx
import pytest

from cms_publisher.channels import cms_payload, create_cms_draft
from cms_publisher.errors import PublisherError, StateConflict, UnknownDelivery
from cms_publisher.models import Article
from cms_publisher.profiles import PROFILES, default_period, scheduled_periods, source_key
from cms_publisher.runner import run
from cms_publisher.state import StateStore


TOKEN = "t" * 40


def item(profile="producthunt", period="2026-11-05"):
    p = PROFILES[profile]
    return Article(profile, period, source_key(p, period), "11111111-1111-4111-8111-111111111111",
                   "notion title", "CMS article", "summary", "# CMS article\n\ntext\n", "a" * 64)


def test_cms_payload_exact_compatibility():
    ph = cms_payload(item(), PROFILES["producthunt"])
    assert ph == {"source": "producthunt", "externalId": "2026-11-05",
                  "categorySlug": "producthunt", "title": "CMS article",
                  "summary": "summary", "bodyMarkdown": "# CMS article\n\ntext\n"}
    monthly = cms_payload(item("trustmrr", "monthly:2026-11"), PROFILES["trustmrr"])
    assert monthly["source"] == "trustmrr-monthly"
    assert monthly["externalId"] == "2026-11"
    assert monthly["categorySlug"] == "TrustMRR"


def test_read_before_write_and_idempotent_success(tmp_path):
    article = item()
    store = StateStore(tmp_path / "state.sqlite")
    store.save_snapshot(article)
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.method)
        if request.method == "GET":
            assert request.url.params["source"] == "producthunt"
            return httpx.Response(404, json={"found": False})
        assert request.method == "POST"
        return httpx.Response(201, json={"status": "draft", "duplicate": False, "id": "new-cms-id"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert create_cms_draft(article, PROFILES["producthunt"], store, token=TOKEN, client=client) == "created"
    assert calls == ["GET", "POST"]
    assert store.delivery_state(article.source_key) == "SUCCESS"


def test_existing_cms_is_marked_success_without_post(tmp_path):
    article = item()
    store = StateStore(tmp_path / "state.sqlite")
    store.save_snapshot(article)

    def handler(request: httpx.Request):
        assert request.method == "GET", "Never POST an existing CMS article"
        return httpx.Response(200, json={"found": True, "sourceKey": article.source_key,
                                         "status": "draft", "id": "existing-id"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert create_cms_draft(article, PROFILES["producthunt"], store, token=TOKEN, client=client) == "already_exists"
    assert store.delivery_state(article.source_key) == "SUCCESS"


def test_ambiguous_post_is_unknown_and_cannot_retry(tmp_path):
    article = item()
    store = StateStore(tmp_path / "state.sqlite")
    store.save_snapshot(article)

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(404, json={"found": False})
        raise httpx.ReadTimeout("could have reached CMS")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UnknownDelivery):
            create_cms_draft(article, PROFILES["producthunt"], store, token=TOKEN, client=client)
    assert store.delivery_state(article.source_key) == "UNKNOWN"
    with pytest.raises(StateConflict, match="not PENDING"):
        store.reserve(article.source_key)


def test_failed_preflight_keeps_pending_and_never_posts(tmp_path):
    article = item()
    store = StateStore(tmp_path / "state.sqlite")
    store.save_snapshot(article)

    def handler(request: httpx.Request):
        assert request.method == "GET"
        raise httpx.ReadTimeout("lookup failed")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PublisherError, match="lookup failed"):
            create_cms_draft(article, PROFILES["producthunt"], store, token=TOKEN, client=client)
    assert store.delivery_state(article.source_key) == "PENDING"


def test_disabled_profile_never_loads_notion(tmp_path, monkeypatch):
    monkeypatch.delenv("CMS_ENABLE_DRAFTS", raising=False)
    monkeypatch.setenv("CMS_PRODUCTHUNT_ENABLED", "true")
    with pytest.raises(PublisherError, match="disabled"):
        run(PROFILES["producthunt"], "2026-11-05", StateStore(tmp_path / "state.sqlite"))


def test_import_old_cms_success_unknown_and_pending(tmp_path):
    legacy_path = tmp_path / "old.sqlite"
    old = sqlite3.connect(legacy_path)
    old.executescript("""
        CREATE TABLE articles(source_key TEXT PRIMARY KEY,profile TEXT,period TEXT,
            notion_page_id TEXT,notion_title TEXT,title TEXT,summary TEXT,
            body_markdown TEXT,content_sha256 TEXT,content_version INTEGER,snapshotted_at TEXT);
        CREATE TABLE deliveries(source_key TEXT,channel TEXT,state TEXT,
            receipt TEXT,detail TEXT,updated_at TEXT);
    """)
    for i, state in enumerate(("SUCCESS", "UNKNOWN", "PENDING"), start=5):
        a = item(period=f"2026-11-0{i}")
        old.execute("INSERT INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (a.source_key, a.profile, a.period, a.notion_page_id, a.notion_title,
                     a.title, a.summary, a.body_markdown, a.content_sha256, 1, "old-time"))
        old.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?)",
                    (a.source_key, "cms", state, "cms-receipt" if state == "SUCCESS" else None,
                     "old", "old-time"))
        old.execute("INSERT INTO deliveries VALUES (?,?,?,?,?,?)",
                    (a.source_key, "wechat", "SUCCESS", "wechat-media", "old", "old-time"))
    old.commit()
    old.close()

    store = StateStore(tmp_path / "new.sqlite")
    assert store.import_text_state(legacy_path) == 3
    assert {row["state"] for row in store.rows()} == {"SUCCESS", "UNKNOWN", "PENDING"}
    assert store.import_text_state(legacy_path) == 0
    assert len(store.rows()) == 3  # WeChat delivery records were not imported.
    store.save_snapshot(item(period="2026-11-05"))  # exact old snapshot is accepted
    with pytest.raises(StateConflict, match="snapshot differs"):
        different = item(period="2026-11-05")
        store.save_snapshot(Article(**{**different.__dict__, "content_sha256": "b" * 64}))


def test_rearm_requires_manual_evidence(tmp_path):
    a = item()
    store = StateStore(tmp_path / "state.sqlite")
    store.save_snapshot(a)
    store.mark(a.source_key, "UNKNOWN")
    with pytest.raises(StateConflict):
        store.rearm_verified_absent(a.source_key, "short")
    store.rearm_verified_absent(a.source_key, "Manual CMS sourceKey check, absent on 2026-11-07")
    assert store.delivery_state(a.source_key) == "PENDING"


def test_old_legacy_placeholder_can_be_hydrated_without_replay(tmp_path):
    a = item()
    store = StateStore(tmp_path / "state.sqlite")
    store.initialize()
    with store.connect() as db:
        db.execute("""
            INSERT INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (a.source_key, a.profile, a.period, "legacy", "legacy", "legacy",
              "legacy", "legacy", "legacy", 0, "old-time"))
        db.execute("INSERT INTO deliveries(source_key,state,updated_at) VALUES (?,?,?)",
                   (a.source_key, "SUCCESS", "old-time"))
        db.commit()
    store.save_snapshot(a)
    assert store.delivery_state(a.source_key) == "SUCCESS"
    with store.connect() as db:
        row = db.execute("SELECT content_sha256 FROM articles WHERE source_key=?", (a.source_key,)).fetchone()
    assert row["content_sha256"] == a.content_sha256


def test_first_monday_schedules_weekly_and_monthly():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 11, 2, 4, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert scheduled_periods(PROFILES["trustmrr"], now) == (
        "weekly:2026-11-02", "monthly:2026-11")
    assert default_period(PROFILES["producthunt"], now) == "2026-10-31"
