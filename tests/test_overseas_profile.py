from argparse import Namespace

import pytest

from cms_publisher.cli import execute
from cms_publisher.errors import PublisherError
from cms_publisher.models import Article
from cms_publisher.channels import cms_payload
from cms_publisher.profiles import PROFILES, display_title, expected_title, source_key, validate_period


def test_overseas_is_explicit_manual_profile():
    profile = PROFILES["overseas"]
    assert profile.parent_id == "4109e605-ccf8-81f8-9094-c5a0023b36b6"
    assert profile.category_slug == "overseas-stories"
    assert source_key(profile, "2026-11-14") == "overseas-stories:2026-11-14"
    assert expected_title(profile, "2026-11-14") == ("2026-11-14｜海外创业志｜", False)
    assert display_title(profile, "2026-11-14", "2026-11-14｜海外创业志｜Mihai 的反差创业") == "Mihai 的反差创业"
    with pytest.raises(PublisherError):
        validate_period(profile, "2026-11-14-extra")


def test_overseas_requires_period_and_stays_disabled_without_explicit_flag(monkeypatch):
    with pytest.raises(PublisherError, match="explicit --period"):
        execute(Namespace(env_file=None, command="dry-run", profile="overseas", period=None))
    monkeypatch.delenv("CMS_OVERSEAS_ENABLED", raising=False)
    from cms_publisher.config import publishing_enabled
    assert publishing_enabled("overseas") is False


def test_overseas_cms_payload_uses_unique_release_date():
    profile = PROFILES["overseas"]
    article = Article(
        "overseas", "2026-11-14", source_key(profile, "2026-11-14"),
        "11111111-1111-4111-8111-111111111111", "notion title",
        "Mihai 的反差创业", "人物与故事", "# Mihai 的反差创业\n\n正文\n", "a" * 64,
    )
    payload = cms_payload(article, profile)
    assert (payload["source"], payload["externalId"], payload["categorySlug"]) == (
        "overseas-stories", "2026-11-14", "overseas-stories")
