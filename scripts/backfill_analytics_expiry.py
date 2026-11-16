"""Backfill the Firestore TTL field ``expireAt`` on analytics docs written before DAN-205.

Rows written from b2ece8e (DAN-200) until DAN-205 shipped have no ``expireAt``, so a TTL policy would
never delete them. This sets ``expireAt`` from each doc's own timestamp:

    dojo_email_sends   sent_at    + ANALYTICS_RETENTION_DAYS (default 90)
    dojo_email_events  created_at + ANALYTICS_RETENTION_DAYS (default 90)
    dojo_usage         day        + USAGE_RETENTION_DAYS     (default 400)

Dry run by default (reads only). Pass --apply to write. Needs Application Default Credentials for
the project; it only adds a field, and deletion happens later through the TTL policy.

    uv run python scripts/backfill_analytics_expiry.py            # dry run
    uv run python scripts/backfill_analytics_expiry.py --apply    # write expireAt
"""

import argparse
import os

from dojo.firestore_db import FirestoreDojoDatabase


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write expireAt (default: dry run)")
    parser.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT", "herrington-ai-site"))
    args = parser.parse_args()

    counts = FirestoreDojoDatabase(project=args.project).backfill_analytics_expiry(apply=args.apply)
    verb = "Updated" if args.apply else "Would update"
    for collection, n in counts.items():
        print(f"{verb} {n} doc(s) in {collection}")


if __name__ == "__main__":
    main()
