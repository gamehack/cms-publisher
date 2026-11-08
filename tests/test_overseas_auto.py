from argparse import Namespace
from types import SimpleNamespace

import pytest

from cms_publisher import cli
from cms_publisher.errors import PublisherError, SkipArticle
from cms_publisher.notion import NotionReader


def test_issue_scan_is_contiguous_and_rejects_duplicates():
    reader = object.__new__(NotionReader)
    reader.children = lambda _: [
        {"type": "child_page", "child_page": {"title": "M7 编辑备注｜02"}},
        {"type": "child_page", "child_page": {"title": "海外创业志 02|第二期"}},
        {"type": "child_page", "child_page": {"title": "海外创业志 01|第一期"}},
    ]
    assert reader.overseas_issues("x") == ("01", "02")
    reader.children = lambda _: [
        {"type": "child_page", "child_page": {"title": "海外创业志 01|A"}},
        {"type": "child_page", "child_page": {"title": "海外创业志 01|B"}},
    ]
    with pytest.raises(PublisherError, match="Duplicate"):
        reader.overseas_issues("x")
    reader.children = lambda _: [
        {"type": "child_page", "child_page": {"title": "海外创业志 01|A"}},
        {"type": "child_page", "child_page": {"title": "海外创业志 03|C"}},
    ]
    with pytest.raises(PublisherError, match="gap"):
        reader.overseas_issues("x")


def test_auto_delivers_only_first_missing_issue(monkeypatch):
    monkeypatch.setenv("NOTION_API_KEY", "placeholder")
    monkeypatch.setenv("CMS_INGEST_TOKEN", "t" * 32)
    class Reader:
        def __init__(self, *_): pass
        def overseas_issues(self, *_): return ("01", "02", "03")
        def close(self): pass
    monkeypatch.setattr(cli, "NotionReader", Reader)
    monkeypatch.setattr(cli, "lookup_cms_delivery", lambda key, **_: {"id": 16} if key.endswith(":01") else None)
    called = []
    monkeypatch.setattr(cli, "run", lambda _, period: (called.append(period), SimpleNamespace(outcome="created"))[1])
    result = cli.execute(Namespace(command="run", env_file=None, profile="overseas", period=None, auto=True))
    assert result["status"] == "COMPLETED"
    assert called == ["02"]


def test_auto_waits_for_human_ready(monkeypatch):
    monkeypatch.setenv("NOTION_API_KEY", "placeholder")
    monkeypatch.setenv("CMS_INGEST_TOKEN", "t" * 32)
    class Reader:
        def __init__(self, *_): pass
        def overseas_issues(self, *_): return ("01", "02", "03")
        def close(self): pass
    monkeypatch.setattr(cli, "NotionReader", Reader)
    monkeypatch.setattr(cli, "lookup_cms_delivery", lambda key, **_: {"id": 16} if key.endswith(":01") else None)
    monkeypatch.setattr(cli, "run", lambda *_: (_ for _ in ()).throw(SkipArticle("Notion page is not READY")))
    result = cli.execute(Namespace(command="run", env_file=None, profile="overseas", period=None, auto=True))
    assert result["status"] == "SKIPPED" and result["reports"][0]["period"] == "02"
