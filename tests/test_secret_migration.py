"""Plan/transform logic for scripts/move_secrets_to_secret_manager.py (no gcloud calls)."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "move_secrets_to_secret_manager.py"
spec = importlib.util.spec_from_file_location("move_secrets", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _service():
    return {
        "spec": {"template": {
            "metadata": {"name": "dojo-zen-00032-qx8"},
            "spec": {"serviceAccountName": "sa@test", "containers": [{"env": [
                {"name": "DOJO_ACCESS_TOKEN", "value": "tok-value"},
                {"name": "CRON_SECRET", "valueFrom": {"secretKeyRef": {"name": "dojo-zen-cron-secret", "key": "latest"}}},
                {"name": "REQUIRE_AUTH", "value": "true"},
                {"name": "GEMINI_API_KEY", "value": "gem-value"},
            ]}]},
        }},
        "status": {"url": "https://x"},
    }


def test_plan_lists_only_plain_secret_values():
    assert mod.plan(_service()) == ["DOJO_ACCESS_TOKEN", "GEMINI_API_KEY"]


def test_swap_is_one_spec_with_refs_and_no_values():
    out = mod.to_secret_refs(_service(), ["DOJO_ACCESS_TOKEN", "GEMINI_API_KEY"])
    env = {e["name"]: e for e in out["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["DOJO_ACCESS_TOKEN"] == {"name": "DOJO_ACCESS_TOKEN", "valueFrom": {
        "secretKeyRef": {"name": "dojo-zen-dojo-access-token", "key": "latest"}}}
    assert env["REQUIRE_AUTH"]["value"] == "true"  # non-secret config untouched
    text = json.dumps(out)
    assert "tok-value" not in text and "gem-value" not in text
    assert "status" not in out and "name" not in out["spec"]["template"]["metadata"]
    assert mod.plan(out) == []


def test_original_is_not_mutated():
    svc = _service()
    mod.to_secret_refs(svc, ["DOJO_ACCESS_TOKEN"])
    assert svc["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] == "tok-value"
