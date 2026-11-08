"""The GitHub Actions entrypoint must agree with overseas serial issue IDs."""

from pathlib import Path


def test_dispatch_accepts_serial_overseas_issue():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/cms-cron.yml").read_text()
    assert "Overseas story issue number (e.g. 01)" in workflow
    assert '"$MANUAL_PERIOD" =~ ^(0[1-9]|[1-9][0-9]{1,4})$' in workflow
    assert "Overseas manual dispatch requires YYYY-MM-DD" not in workflow


def test_daily_overseas_schedule_is_0730_and_other_profiles_unchanged():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/cms-cron.yml").read_text()
    assert "- cron: '30 7 * * *'" in workflow
    assert "'30 7 * * *') profile=overseas ;;" in workflow
    assert "'05 9 * * *'" not in workflow
    for original_schedule in ("'15 8 * * *'", "'15 19 * * *'", "'15 4 * * 1'"):
        assert original_schedule in workflow
