from __future__ import annotations

import httpx
import pytest

from cms_publisher.channels import cms_payload, create_cms_draft, validate_cms_url
from cms_publisher.errors import PublisherError, UnknownDelivery
from cms_publisher.models import Article
from cms_publisher.profiles import PROFILES, default_period, scheduled_periods, source_key
from cms_publisher.runner import run


TOKEN = "t" * 40


def item(profile="producthunt", period="2026-11-05"):
    p = PROFILES[profile]
    return Article(profile, period, source_key(p, period), "11111111-1111-4111-8111-111111111111",
                   "notion title", "CMS article", "summary", "# CMS article\n\ntext\n", "a" * 64)


def test_cms_payload_preserves_source_key_mapping():
    ph = cms_payload(item(), PROFILES["producthunt"])
    assert ph == {"source": "producthunt", "externalId": "2026-11-05",
                  "categorySlug": "producthunt", "title": "CMS article",
                  "summary": "summary", "bodyMarkdown": "# CMS article\n\ntext\n"}
    monthly = cms_payload(item("trustmrr", "monthly:2026-11"), PROFILES["trustmrr"])
    assert monthly["source"] == "trustmrr-monthly"
    assert monthly["externalId"] == "2026-11"
    assert monthly["categorySlug"] == "TrustMRR"


def test_read_before_write_and_second_run_is_stateless_duplicate_safe():
    article = item()
    calls = []
    exists = False

    def handler(request: httpx.Request):
        nonlocal exists
        calls.append(request.method)
        if request.method == "GET":
            assert request.url.params["source"] == "producthunt"
            return (httpx.Response(200, json={"found": True, "sourceKey": article.source_key,
                                             "status": "draft", "id": "created"}) if exists
                    else httpx.Response(404, json={"found": False}))
        assert request.method == "POST"
        exists = True
        return httpx.Response(201, json={"status": "draft", "duplicate": False, "id": "created"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert create_cms_draft(article, PROFILES["producthunt"], token=TOKEN, client=client) == "created"
        assert create_cms_draft(article, PROFILES["producthunt"], token=TOKEN, client=client) == "already_exists"
    assert calls == ["GET", "POST", "GET"]


def test_existing_cms_created_by_old_project_never_posts():
    article = item()

    def handler(request: httpx.Request):
        assert request.method == "GET", "Must never POST an existing CMS article"
        return httpx.Response(200, json={"found": True, "sourceKey": article.source_key,
                                         "status": "draft", "id": "old-publisher-id"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert create_cms_draft(article, PROFILES["producthunt"], token=TOKEN, client=client) == "already_exists"


def test_ambiguous_post_is_checked_by_source_key_once():
    article = item()
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.method)
        if request.method == "GET" and len(calls) == 1:
            return httpx.Response(404, json={"found": False})
        if request.method == "POST":
            raise httpx.ReadTimeout("POST committed but response was lost")
        return httpx.Response(200, json={"found": True, "sourceKey": article.source_key,
                                         "status": "draft", "id": "committed"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert create_cms_draft(article, PROFILES["producthunt"], token=TOKEN, client=client) == "confirmed_after_error"
    assert calls == ["GET", "POST", "GET"]


def test_ambiguous_post_unconfirmed_fails_without_immediate_replay():
    article = item()
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(404, json={"found": False})
        raise httpx.ReadTimeout("unknown POST outcome")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UnknownDelivery, match="unknown"):
            create_cms_draft(article, PROFILES["producthunt"], token=TOKEN, client=client)
    assert calls == ["GET", "POST", "GET"]


def test_failed_preflight_never_posts():
    article = item()

    def handler(request: httpx.Request):
        assert request.method == "GET"
        raise httpx.ReadTimeout("lookup failed")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PublisherError, match="lookup failed"):
            create_cms_draft(article, PROFILES["producthunt"], token=TOKEN, client=client)


def test_rejects_redirected_cms_destination():
    for bad in ("http://cms.qjai.org/api/ingest", "https://cms.qjai.org.evil.com/api/ingest",
                "https://cms.qjai.org/api/ingest?next=evil", "https://cms.qjai.org:444/api/ingest"):
        with pytest.raises(PublisherError):
            validate_cms_url(bad)


def test_disabled_profile_never_loads_notion(monkeypatch):
    monkeypatch.delenv("CMS_ENABLE_DRAFTS", raising=False)
    monkeypatch.setenv("CMS_PRODUCTHUNT_ENABLED", "true")
    with pytest.raises(PublisherError, match="disabled"):
        run(PROFILES["producthunt"], "2026-11-05")


def test_first_monday_schedules_weekly_and_monthly():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime(2026, 11, 2, 4, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert scheduled_periods(PROFILES["trustmrr"], now) == (
        "weekly:2026-11-02", "monthly:2026-11")
    assert default_period(PROFILES["producthunt"], now) == "2026-10-31"
