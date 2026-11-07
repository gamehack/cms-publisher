#!/usr/bin/env bash
# Immutable command allowlist; this entrypoint is invoked by GitHub Actions via SSH.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo 'Usage: run-on-server.sh <x|producthunt|trustmrr> <dry-run|publish>' >&2
  exit 2
fi
case "$1" in
  x|producthunt|trustmrr) profile="$1" ;;
  *) echo 'Unknown profile' >&2; exit 2 ;;
esac
case "$2" in
  dry-run) command=dry-run ;;
  publish) command=run ;;
  *) echo 'Unknown operation' >&2; exit 2 ;;
esac

exec /usr/bin/flock -n /var/lib/cms-publisher/dispatch.lock \
  /opt/cms-publisher/.venv/bin/cms-publisher \
  --env-file /opt/cms-publisher/.env \
  "$command" --profile "$profile"
