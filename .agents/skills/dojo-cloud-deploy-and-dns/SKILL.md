---
name: dojo-cloud-deploy-and-dns
description: >-
  Deploys Dojo Zen to Google Cloud Run (dojo-zen in us-central1, project herrington-ai-site),
  configures Firebase Hosting rewrites (herrington-dojo.web.app / dojo.herrington.ai),
  manages Cloud Scheduler jobs, and automates Cloudflare DNS records via scripts/cloudflare_dns.py.
  Use when deploying updates, troubleshooting domain/DNS/SSL issues, or managing cron jobs.
---

# Cloud Run Deployment, Firebase Hosting & Cloudflare DNS Skill

## Production Infrastructure Summary

- **GCP Project**: `herrington-ai-site`
- **Cloud Run Service**: `dojo-zen` (`us-central1`)
  - Direct URL: `https://dojo-zen-647998166555.us-central1.run.app`
- **Firebase Hosting Site**: `herrington-dojo` (target `dojo` in [`firebase.json`](../../firebase.json) and [`.firebaserc`](../../.firebaserc))
  - Firebase URL: `https://herrington-dojo.web.app`
  - Custom Domain: `https://dojo.herrington.ai`
- **Cloud Scheduler Jobs** (`us-central1`, timezone `America/New_York`):
  - `dojo-check-alerts`: Runs `*/10 * * * *` → `POST /api/cron/check-alerts?secret=...`
  - `dojo-daily-digest`: Runs `0 7 * * *` → `POST /api/cron/daily-digest?secret=...`

## Deploying to Cloud Run

1. **Check or create `.env.yaml`** (if running in a fresh Cloud Project where `.env.yaml` is gitignored, retrieve current env vars from Cloud Run first or use `--update-env-vars`):
   ```bash
   # In a Cloud Project without local .env.yaml, deploy without overwriting existing env vars:
   gcloud run deploy dojo-zen \
     --source . \
     --region us-central1 \
     --project herrington-ai-site \
     --allow-unauthenticated \
     --memory 512Mi \
     --cpu 1 \
     --min-instances 0 \
     --max-instances 2
   ```
   *(Note: `gcloud run deploy` preserves all existing environment variables on the Cloud Run service unless `--env-vars-file` or `--set-env-vars` is passed.)*

2. **Full Deploy Script (Local with `.env.yaml`)**:
   ```bash
   ./scripts/deploy.sh
   ```

## Cloudflare DNS Automation (`scripts/cloudflare_dns.py`)

`herrington.ai` DNS is hosted on Cloudflare. Whenever creating or verifying a subdomain on `herrington.ai` backed by Firebase Hosting (`ghs.googlehosted.com`) or Cloud Run:
- Keep `proxied=false` (DNS-only) so Google-managed SSL certificates validate cleanly.
- Token is read from `CLOUDFLARE_API_TOKEN` env var or `~/.cloudflare/token`.

```bash
# List all DNS records for herrington.ai
python3 scripts/cloudflare_dns.py list

# Ensure dojo.herrington.ai points to Firebase Hosting
python3 scripts/cloudflare_dns.py upsert dojo ghs.googlehosted.com --comment "ClassDojo DojoZen on Cloud Run"
```
