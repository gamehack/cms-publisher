from __future__ import annotations

import argparse
import json
import os
import sys

from . import config
from .channels import CMS_INGEST_URL, lookup_cms_delivery
from .errors import PublisherError, SkipArticle
from .profiles import PROFILES, default_period, get_profile, scheduled_periods, validate_period
from .runner import read_article, run


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Stateless Notion to CMS draft publisher")
    root.add_argument("--env-file")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("preview", "dry-run", "run", "backfill"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--profile", required=True, choices=sorted(PROFILES))
        cmd.add_argument("--period")
        if name == "backfill":
            cmd.add_argument("--authorize", action="store_true")
    lookup = commands.add_parser("lookup")
    lookup.add_argument("--source-key", required=True)
    return root


def execute(args: argparse.Namespace) -> dict[str, object]:
    config.load_environment(args.env_file)
    if args.command == "lookup":
        found = lookup_cms_delivery(
            args.source_key,
            token=config.required("CMS_INGEST_TOKEN", minimum=32),
            url=os.getenv("CMS_INGEST_URL", CMS_INGEST_URL).strip(),
        )
        return {"status": "FOUND" if found else "ABSENT", "source_key": args.source_key,
                "cms": found, "external_write": False}

    profile = get_profile(args.profile)
    if profile.name == "overseas" and not args.period:
        raise PublisherError("Overseas stories require explicit --period; no automatic date selection")
    period = validate_period(profile, args.period or default_period(profile))
    if args.command in {"preview", "dry-run"}:
        article = read_article(profile, period)
        return {**article.public_summary(), "status": "DRY_RUN_OK",
                "schedule": profile.schedule, "external_write": False}

    if args.command == "backfill" and not args.authorize:
        raise PublisherError("Backfill requires --authorize")
    periods = ((period,) if args.period is not None or profile.name != "trustmrr"
               else scheduled_periods(profile))
    outcomes: list[dict[str, object]] = []
    failures = 0
    for item_period in periods:
        try:
            result = run(profile, item_period)
            outcomes.append({"period": item_period, "source_key": result.article.source_key,
                             "status": "COMPLETED", "cms": result.outcome})
        except SkipArticle as exc:
            outcomes.append({"period": item_period, "status": "SKIPPED", "reason": str(exc)})
        except PublisherError as exc:
            failures += 1
            outcomes.append({"period": item_period, "status": "FAILED", "reason": str(exc)})
    complete = any(item["status"] == "COMPLETED" for item in outcomes)
    status = "FAILED" if failures else ("COMPLETED" if complete else "SKIPPED")
    return {"profile": profile.name, "status": status, "reports": outcomes}


def main() -> None:
    try:
        result = execute(parser().parse_args())
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if result.get("status") == "FAILED":
            raise SystemExit(2)
    except SkipArticle as exc:
        print(json.dumps({"status": "SKIPPED", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(0)
    except (PublisherError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
