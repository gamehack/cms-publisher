from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from .errors import PublisherError, UnknownDelivery
from .models import Article
from .profiles import Profile


CMS_INGEST_URL = "https://cms.qjai.org/api/ingest"
MAX_CMS_BODY = 220_000
USER_AGENT = "QJAI-CMS-Publisher/1.0 (+https://cms.qjai.org/)"


def safe_json(response: httpx.Response) -> dict[str, Any]:
    if len(response.content) > 8192:
        raise PublisherError("CMS response exceeds safety limit")
    try:
        value = response.json()
    except ValueError:
        raise PublisherError("CMS returned invalid JSON") from None
    if not isinstance(value, dict):
        raise PublisherError("CMS returned a non-object")
    return value


def cms_payload(article: Article, profile: Profile) -> dict[str, str]:
    if not article.body_markdown.strip() or len(article.body_markdown) > 180_000:
        raise PublisherError("CMS Markdown body is missing or too large")
    source, external_id = article.source_key.split(":", 1)
    payload = {
        "source": source,
        "externalId": external_id,
        "categorySlug": profile.category_slug,
        "title": article.title,
        "summary": article.summary,
        "bodyMarkdown": article.body_markdown,
    }
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_CMS_BODY:
        raise PublisherError("CMS request body exceeds safety limit")
    return payload


def validate_cms_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "cms.qjai.org" or
            parsed.path != "/api/ingest" or parsed.query or parsed.fragment):
        raise PublisherError("CMS destination must be https://cms.qjai.org/api/ingest")


def lookup_cms_delivery(source_key: str, *, token: str, url: str = CMS_INGEST_URL,
                        client: httpx.Client | None = None) -> dict[str, str] | None:
    """Read-only authoritative sourceKey lookup, including before first POST."""
    if len(token) < 32:
        raise PublisherError("CMS ingest token is missing or invalid")
    validate_cms_url(url)
    if source_key.count(":") != 1:
        raise PublisherError("Invalid CMS sourceKey")
    source, external_id = source_key.split(":", 1)
    if not source or not external_id:
        raise PublisherError("Invalid CMS sourceKey")
    session = client or httpx.Client(timeout=25, follow_redirects=False)
    try:
        try:
            response = session.get(
                url,
                params={"source": source, "externalId": external_id},
                headers={"Authorization": "Bearer " + token,
                         "Accept": "application/json", "User-Agent": USER_AGENT},
            )
        except httpx.HTTPError:
            raise PublisherError("CMS read-only lookup failed") from None
        data = safe_json(response)
        if response.status_code == 404 and data.get("found") is False:
            return None
        if (response.status_code != 200 or data.get("found") is not True or
                data.get("sourceKey") != source_key):
            raise PublisherError("CMS lookup returned unexpected result")
        return {"receipt": str(data.get("id", "")), "status": str(data.get("status", ""))}
    finally:
        if client is None:
            session.close()


def create_cms_draft(article: Article, profile: Profile, *, token: str,
                     url: str = CMS_INGEST_URL,
                     client: httpx.Client | None = None) -> str:
    """Stateless CMS draft delivery; the CMS unique sourceKey is authoritative.

    A fresh GitHub-hosted runner starts each invocation. We check the CMS by
    sourceKey before a POST, and the CMS DB unique constraint prevents a second
    row even after a timeout or a concurrent invocation.
    """
    if len(token) < 32:
        raise PublisherError("CMS ingest token is missing or invalid")
    validate_cms_url(url)
    payload = cms_payload(article, profile)
    session = client or httpx.Client(timeout=25, follow_redirects=False)
    try:
        # Fail closed if lookup errors. The CMS database is the durable
        # source of truth, including drafts created by qjai-text-publisher.
        found = lookup_cms_delivery(article.source_key, token=token, url=url, client=session)
        if found is not None:
            return "already_exists"
        try:
            response = session.post(
                url,
                json=payload,
                headers={"Authorization": "Bearer " + token,
                         "Accept": "application/json", "User-Agent": USER_AGENT},
            )
            data = safe_json(response)
            created = (response.status_code == 201 and data.get("status") == "draft" and
                       data.get("duplicate") is False)
            duplicate = response.status_code == 200 and data.get("duplicate") is True
            if not (created or duplicate):
                raise RuntimeError("Unconfirmed CMS acknowledgement")
        except Exception:
            # The POST may already have committed. Check once by sourceKey,
            # but never send a second POST in the same invocation.
            try:
                confirmed = lookup_cms_delivery(article.source_key, token=token,
                                                url=url, client=session)
            except PublisherError:
                confirmed = None
            if confirmed is not None:
                return "confirmed_after_error"
            raise UnknownDelivery(
                "CMS POST outcome unknown; no immediate retry. Inspect CMS by sourceKey. "
                "Future scheduled runs rely on CMS database uniqueness."
            ) from None
        return "duplicate" if duplicate else "created"
    finally:
        if client is None:
            session.close()
