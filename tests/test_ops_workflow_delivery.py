from pathlib import Path


def test_each_cms_execution_writes_one_result_file_for_ops_oidc():
    workflow=(Path(__file__).resolve().parents[1]/".github/workflows/cms-cron.yml").read_text()
    result_file='> "$RUNNER_TEMP/cms-result.json"'
    assert workflow.count(result_file)==3
    assert 'python -m cms_publisher run --profile overseas --auto '+result_file in workflow
    assert 'python -m cms_publisher "$MODE" --profile "$PROFILE" --period "$MANUAL_PERIOD" '+result_file in workflow
    assert 'python -m cms_publisher "$MODE" --profile "$PROFILE" '+result_file in workflow
    assert 'python -m cms_publisher.ops_proof --result-file "$RUNNER_TEMP/cms-result.json"' in workflow


def test_oidc_evidence_only_after_authoritative_cms_readback():
    workflow=(Path(__file__).resolve().parents[1]/".github/workflows/cms-cron.yml").read_text()
    assert "steps.choose.outputs.mode == 'run'" in workflow
    assert 'id-token: write' in workflow
    proof=(Path(__file__).resolve().parents[1]/"src/cms_publisher/ops_proof.py").read_text()
    assert "lookup_cms_delivery(item[\"metadata\"][\"source_key\"]" in proof
    assert 'if not actual or actual.get("status") not in ("draft","published")' in proof
