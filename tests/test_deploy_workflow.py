"""DAN-478: a deploy that can't run must fail visibly, never pass as a green skip."""

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy.yml"


def _step(src: str, name: str) -> str:
    start = src.index(f"- name: {name}")
    nxt = src.find("\n      - name:", start + 1)
    return src[start:nxt if nxt != -1 else len(src)]


def test_missing_wif_config_fails_the_job():
    check = _step(WORKFLOW.read_text(), "Check for Workload Identity config")
    assert "exit 1" in check and "::error" in check
    assert "has_creds" not in WORKFLOW.read_text()  # no step may quietly skip on missing config


def test_deploy_stays_keyless_and_gated():
    src = WORKFLOW.read_text()
    assert "workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}" in src
    assert not re.search(r"credentials_json|GCP_SA_KEY", src)
    assert "environment: production" in src and "needs: test" in src


def test_deploy_never_touches_scheduler_or_the_cron_secret():
    src = WORKFLOW.read_text()
    assert "gcloud scheduler" not in src and "secrets.CRON_SECRET" not in src
