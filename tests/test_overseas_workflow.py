"""The GitHub Actions entrypoint must agree with overseas serial issue IDs."""

from pathlib import Path


def test_dispatch_accepts_serial_overseas_issue():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/cms-cron.yml").read_text()
    assert "Overseas story issue number (e.g. 01)" in workflow
    assert '"$MANUAL_PERIOD" =~ ^(0[1-9]|[1-9][0-9]{1,4})$' in workflow
    assert "Overseas manual dispatch requires YYYY-MM-DD" not in workflow
