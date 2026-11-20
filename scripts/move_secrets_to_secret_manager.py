#!/usr/bin/env python3
"""Move dojo-zen's secret env vars from plain Cloud Run env values into Secret Manager (DAN-402 follow-up).

Dry run by default: prints the plan with names only. ``--apply`` does it:

1. For each secret env var still stored as a plain value, create Secret Manager secret
   ``dojo-zen-<name>`` (if missing) and add the current value as a new version. The value is
   piped from the live service config to ``gcloud secrets versions add --data-file=-``;
   it is never printed or written to the repo.
2. Grant the service's runtime account ``roles/secretmanager.secretAccessor`` on each secret.
3. Swap every env entry to a ``secretKeyRef`` (version ``latest``) in ONE
   ``gcloud run services replace``, so there is no revision where a value is missing
   (REQUIRE_AUTH is on: a revision without DOJO_ACCESS_TOKEN refuses every private request).

The app needs no change: Cloud Run still exposes each secret as the same env var.
Rollback: ``gcloud run services update-traffic dojo-zen --to-revisions <previous>=100``.

Usage:
    uv run python scripts/move_secrets_to_secret_manager.py            # plan only
    uv run python scripts/move_secrets_to_secret_manager.py --apply    # needs Daniel's yes
"""

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional

GCLOUD = os.path.expanduser("~/google-cloud-sdk/bin/gcloud")
PROJECT = "herrington-ai-site"
REGION = "us-central1"
SERVICE = "dojo-zen"

SECRET_ENV = [
    "DOJO_ACCESS_TOKEN",
    "CRON_SECRET",
    "DOJO_PASSWORD",
    "SMTP_PASS",
    "GEMINI_API_KEY",
]


def secret_id(env_name: str) -> str:
    return f"{SERVICE}-{env_name.lower().replace('_', '-')}"


def plan(service: Dict, names: List[str] = SECRET_ENV) -> List[str]:
    """Env names in ``names`` that the live service still holds as plain values."""
    env = service["spec"]["template"]["spec"]["containers"][0].get("env", [])
    return [e["name"] for e in env if e["name"] in names and "value" in e and "valueFrom" not in e]


def to_secret_refs(service: Dict, names: List[str]) -> Dict:
    """A copy of the service spec with each named env entry switched to a Secret Manager reference."""
    out = copy.deepcopy(service)
    tmpl = out["spec"]["template"]
    for e in tmpl["spec"]["containers"][0].get("env", []):
        if e["name"] in names:
            e.pop("value", None)
            e["valueFrom"] = {"secretKeyRef": {"name": secret_id(e["name"]), "key": "latest"}}
    # Let Cloud Run name the new revision; drop read-only status fields.
    tmpl.get("metadata", {}).pop("name", None)
    out.pop("status", None)
    return out


def _gcloud(*args: str, data: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([GCLOUD, *args, "--project", PROJECT], input=data, text=True,
                          capture_output=True, check=check)


def _service() -> Dict:
    return json.loads(_gcloud("run", "services", "describe", SERVICE, "--region", REGION,
                              "--format", "json").stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="make the changes (default: plan only)")
    args = parser.parse_args()

    service = _service()
    names = plan(service)
    account = service["spec"]["template"]["spec"].get("serviceAccountName", "(default compute)")
    print(f"Service {SERVICE}, runtime account {account}")
    if not names:
        print("Nothing to move: every listed secret is already a Secret Manager reference.")
        return 0
    for n in names:
        print(f"  {n} -> secret {secret_id(n)} (version latest)")
    if not args.apply:
        print("Dry run. Re-run with --apply to make these changes.")
        return 0

    env = {e["name"]: e.get("value", "") for e in service["spec"]["template"]["spec"]["containers"][0]["env"]}
    for n in names:
        sid = secret_id(n)
        if _gcloud("secrets", "describe", sid, check=False).returncode != 0:
            _gcloud("secrets", "create", sid, "--replication-policy", "automatic")
        _gcloud("secrets", "versions", "add", sid, "--data-file=-", data=env[n])
        _gcloud("secrets", "add-iam-policy-binding", sid, "--member", f"serviceAccount:{account}",
                "--role", "roles/secretmanager.secretAccessor")
        print(f"  stored {n} in {sid}")

    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:  # mode 0600; holds no secret values after the swap
            json.dump(to_secret_refs(service, names), f)
        _gcloud("run", "services", "replace", path, "--region", REGION)
    finally:
        os.unlink(path)

    left = plan(_service())
    print("Done." if not left else f"Still plain: {left}")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
