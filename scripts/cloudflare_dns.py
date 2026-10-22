#!/usr/bin/env python3
"""Cloudflare DNS management utility for herrington.ai and subdomains.

Reads API token from ~/.cloudflare/token or CLOUDFLARE_API_TOKEN environment variable.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request
import urllib.error

TOKEN_PATH = Path.home() / ".cloudflare" / "token"


def get_token() -> str:
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if token:
        return token.strip()
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text().strip()
    print("Error: No Cloudflare API token found in environment or ~/.cloudflare/token", file=sys.stderr)
    sys.exit(1)


def api_request(method: str, path: str, data: dict = None) -> dict:
    token = get_token()
    url = f"https://api.cloudflare.com/client/v4{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"Cloudflare API Error ({e.code}): {err_body}", file=sys.stderr)
        sys.exit(1)


def get_zone_id(zone_name: str = "herrington.ai") -> str:
    res = api_request("GET", f"/zones?name={zone_name}")
    results = res.get("result", [])
    if not results:
        print(f"Error: Zone {zone_name} not found in account", file=sys.stderr)
        sys.exit(1)
    return results[0]["id"]


def list_records(zone_id: str):
    res = api_request("GET", f"/zones/{zone_id}/dns_records?per_page=100")
    return res.get("result", [])


def upsert_record(subdomain: str, content: str, record_type: str = "CNAME", proxied: bool = False, comment: str = None, zone_name: str = "herrington.ai") -> dict:
    zone_id = get_zone_id(zone_name)
    full_name = f"{subdomain}.{zone_name}" if subdomain != "@" and not subdomain.endswith(f".{zone_name}") else (zone_name if subdomain == "@" else subdomain)

    # Check if record already exists
    existing = api_request("GET", f"/zones/{zone_id}/dns_records?name={full_name}&type={record_type}").get("result", [])
    payload = {
        "type": record_type,
        "name": full_name,
        "content": content,
        "ttl": 1,
        "proxied": proxied,
        "comment": comment or f"Automated record by Antigravity",
    }

    if existing:
        rec_id = existing[0]["id"]
        res = api_request("PUT", f"/zones/{zone_id}/dns_records/{rec_id}", payload)
        print(f"✓ Updated {record_type} {full_name} -> {content} (proxied={proxied})")
        return res
    else:
        res = api_request("POST", f"/zones/{zone_id}/dns_records", payload)
        print(f"✓ Created {record_type} {full_name} -> {content} (proxied={proxied})")
        return res


def main():
    parser = argparse.ArgumentParser(description="Manage Cloudflare DNS records for herrington.ai")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # List command
    list_p = subparsers.add_parser("list", help="List DNS records")
    list_p.add_argument("--zone", default="herrington.ai")

    # Upsert command
    upsert_p = subparsers.add_parser("upsert", help="Create or update a DNS record")
    upsert_p.add_argument("subdomain", help="Subdomain prefix (e.g. 'dojo' or '@')")
    upsert_p.add_argument("content", help="Target IP or hostname (e.g. 'ghs.googlehosted.com')")
    upsert_p.add_argument("--type", default="CNAME", help="Record type (A, CNAME, etc.)")
    upsert_p.add_argument("--proxied", action="store_true", help="Enable Cloudflare proxy (orange cloud)")
    upsert_p.add_argument("--comment", default=None, help="Record comment")
    upsert_p.add_argument("--zone", default="herrington.ai")

    args = parser.parse_args()

    if args.command == "list":
        zone_id = get_zone_id(args.zone)
        records = list_records(zone_id)
        print(f"\nDNS Records for {args.zone}:")
        print(f"{'Type':<6} {'Name':<35} {'Content':<40} {'Proxied'}")
        print("-" * 88)
        for r in records:
            print(f"{r['type']:<6} {r['name']:<35} {r['content']:<40} {r.get('proxied', False)}")

    elif args.command == "upsert":
        upsert_record(
            subdomain=args.subdomain,
            content=args.content,
            record_type=args.type,
            proxied=args.proxied,
            comment=args.comment,
            zone_name=args.zone,
        )


if __name__ == "__main__":
    main()
