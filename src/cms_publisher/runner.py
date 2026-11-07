from __future__ import annotations

from dataclasses import dataclass
import os

import httpx

from . import config
from .channels import CMS_INGEST_URL, create_cms_draft
from .errors import PublisherError
from .models import Article
from .notion import NotionReader, load_article
from .profiles import Profile


@dataclass(frozen=True)
class RunResult:
    article: Article
    outcome: str


def read_article(profile: Profile, period: str, *, transport: httpx.BaseTransport | None = None) -> Article:
    reader = NotionReader(config.required("NOTION_API_KEY"), transport=transport)
    try:
        return load_article(reader, profile, period)
    finally:
        reader.close()


def run(profile: Profile, period: str, *, article: Article | None = None) -> RunResult:
    if not config.publishing_enabled(profile.name):
        raise PublisherError(f"CMS profile {profile.name} is disabled")
    article = article or read_article(profile, period)
    outcome = create_cms_draft(
        article, profile,
        token=config.required("CMS_INGEST_TOKEN", minimum=32),
        url=os.getenv("CMS_INGEST_URL", CMS_INGEST_URL).strip(),
    )
    return RunResult(article, outcome)
