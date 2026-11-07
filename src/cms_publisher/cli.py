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
from .state import StateStore


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Fail-closed Notion to CMS draft publisher")
    root.add_argument("--env-file")
    root.add_argument("--state")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("preview", "dry-run", "run", "backfill"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--profile", required=True, choices=sorted(PROFILES))
        cmd.add_argument("--period")
        if name == "backfill":
            cmd.add_argument("--authorize", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("--profile", choices=sorted(PROFILES))
    health = commands.add_parser("health")
    health.add_argument("--profile", choices=sorted(PROFILES))
    old = commands.add_parser("import-from-text-state")
    old.add_argument("--file", required=True)
    old.add_argument("--authorize", action="store_true")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--source-key", required=True)
    reconcile.add_argument("--result", choices=("SUCCESS", "ABSENT"), required=True)
    reconcile.add_argument("--evidence")
    reconcile.add_argument("--confirm-absent", action="store_true")
    reconcile.add_argument("--authorize", action="store_true")
    return root


def _period(args: argparse.Namespace):
    profile = get_profile(args.profile)
    period = args.period or default_period(profile)
    return profile, validate_period(profile, period)


def execute(args: argparse.Namespace) -> dict[str, object]:
    config.load_environment(args.env_file)
    store = StateStore(args.state or config.state_path())
    if args.command in {"preview", "dry-run", "run", "backfill"}:
        profile, period = _period(args)
    if args.command in {"preview", "dry-run"}:
        article = read_article(profile, period)
        return {**article.public_summary(), "status": "DRY_RUN_OK",
                "schedule": profile.schedule, "external_write": False}
    if args.command in {"run", "backfill"}:
        if args.command == "backfill" and not args.authorize:
            raise PublisherError("Backfill requires --authorize")
        periods = (period,) if args.period is not None or profile.name != "trustmrr" else scheduled_periods(profile)
        outcomes: list[dict[str, object]] = []
        failures = 0
        for item_period in periods:
            try:
                result = run(profile, item_period, store)
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
    if args.command == "status":
        return {"status": "OK", "deliveries": store.rows(args.profile)}
    if args.command == "health":
        rows = store.rows(args.profile)
        counts = {key: sum(item["state"] == key for item in rows)
                  for key in ("SUCCESS", "PENDING", "INTENT", "UNKNOWN", "FAILED")}
        return {"status": "ACTION_REQUIRED" if any(counts[x] for x in ("INTENT", "UNKNOWN", "FAILED")) else "OK",
                "counts": counts, "total": len(rows)}
    if args.command == "import-from-text-state":
        if not args.authorize:
            raise PublisherError("Import requires --authorize")
        imported = store.import_text_state(args.file)
        return {"status": "IMPORTED", "cms_rows": imported, "external_write": False}
    if args.command == "reconcile":
        if not args.authorize:
            raise PublisherError("Reconciliation requires --authorize")
        current = store.delivery_state(args.source_key)
        if current not in {"UNKNOWN", "INTENT", "FAILED", "PENDING"}:
            raise PublisherError(f"CMS state {current} cannot be reconciled")
        found = lookup_cms_delivery(
            args.source_key,
            token=config.required("CMS_INGEST_TOKEN", minimum=32),
            url=os.getenv("CMS_INGEST_URL", CMS_INGEST_URL).strip(),
        )
        if args.result == "SUCCESS":
            if found is None:
                raise PublisherError("CMS sourceKey not found; cannot mark SUCCESS")
            store.mark(args.source_key, "SUCCESS", receipt=found["receipt"],
                       detail="verified by CMS sourceKey lookup: " + found["status"])
            return {"status": "RECONCILED", "source_key": args.source_key,
                    "cms": "SUCCESS", "external_write": False}
        if found is not None:
            raise PublisherError("CMS sourceKey already exists; reconcile as SUCCESS")
        if not args.confirm_absent or not args.evidence:
            raise PublisherError("ABSENT requires --confirm-absent and --evidence")
        store.rearm_verified_absent(args.source_key, args.evidence)
        return {"status": "REARMED", "source_key": args.source_key,
                "cms": "PENDING", "external_write": False}
    raise PublisherError("Unknown command")


def main() -> None:
    try:
        result = execute(parser().parse_args())
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        if result.get("status") in {"FAILED", "ACTION_REQUIRED"}:
            raise SystemExit(2)
    except SkipArticle as exc:
        print(json.dumps({"status": "SKIPPED", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(0)
    except (PublisherError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
