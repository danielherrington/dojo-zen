#!/usr/bin/env bash
set -e

PROJECT_ID="herrington-ai-site"
REGION="us-central1"
SERVICE_NAME="dojo-zen"
SITE_TARGET="dojo"
SITE_ID="herrington-dojo"

echo "============================================================"
echo "🚀 Deploying Dojo Zen to Firebase & Cloud Run"
echo "Project: $PROJECT_ID | Region: $REGION"
echo "============================================================"

# 1. First, seed Firestore if needed
echo ""
echo "📦 Step 1: Ensuring Firestore has latest classroom data..."
PYTHONPATH=src uv run python -m dojo seed-firestore

# 2. Deploy Cloud Run Service
echo ""
echo "☁️ Step 2: Building & Deploying Cloud Run Service '$SERVICE_NAME'..."
# No env flags: the live service keeps its config (REQUIRE_AUTH, Secret Manager references).
# --env-vars-file would REPLACE every env var and could silently turn auth off. Change config
# with `gcloud run services update --update-env-vars` / --update-secrets instead.

gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 2

# Get Cloud Run Service URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')
echo "✓ Cloud Run Service URL: $SERVICE_URL"

# Ensure Cloudflare DNS record exists
if [ -f "$HOME/.cloudflare/token" ] || [ -n "$CLOUDFLARE_API_TOKEN" ]; then
  echo ""
  echo "🌐 Ensuring Cloudflare DNS record for dojo.herrington.ai..."
  python3 scripts/cloudflare_dns.py upsert dojo ghs.googlehosted.com --comment "ClassDojo DojoZen on Cloud Run" || true
fi

# 3. Deploy Firebase Hosting target
echo ""
echo "🌐 Step 3: Deploying Firebase Hosting site '$SITE_ID'..."
firebase deploy --only "hosting:$SITE_TARGET" --project "$PROJECT_ID"

PUBLIC_URL="https://$SITE_ID.web.app"
echo "✓ Public Web Portal URL: $PUBLIC_URL"

# 4. Provision Cloud Scheduler Jobs
echo ""
echo "⏰ Step 4: Configuring Cloud Scheduler Jobs..."

# CRON_SECRET must match the value set on the dojo-zen service. Never commit it: read it from the
# environment, or from the macOS Keychain item created during rotation (service "dojo-zen").
CRON_SECRET="${CRON_SECRET:-$(security find-generic-password -s dojo-zen -a CRON_SECRET -w 2>/dev/null || true)}"
if [ -z "$CRON_SECRET" ]; then
  echo "❌ CRON_SECRET is not set (export it or add it to the Keychain); refusing to configure Cloud Scheduler." >&2
  exit 1
fi

# Job A: Check urgent alerts every 10 minutes (8am to 5pm on weekdays)
# Or */10 * * * * for 24/7 monitoring
if gcloud scheduler jobs describe dojo-check-alerts --location "$REGION" --project "$PROJECT_ID" &>/dev/null; then
  echo "Updating existing scheduler job 'dojo-check-alerts'..."
  gcloud scheduler jobs update http dojo-check-alerts \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "*/10 * * * *" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/check-alerts" \
    --update-headers "Authorization=Bearer $CRON_SECRET" \
    --http-method POST
else
  echo "Creating scheduler job 'dojo-check-alerts'..."
  gcloud scheduler jobs create http dojo-check-alerts \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "*/10 * * * *" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/check-alerts" \
    --headers "Authorization=Bearer $CRON_SECRET" \
    --http-method POST
fi

# Job B: Weekday morning digest (Mon–Fri at 7:00 AM Eastern Time; no Saturday or Sunday morning emails)
if gcloud scheduler jobs describe dojo-daily-digest --location "$REGION" --project "$PROJECT_ID" &>/dev/null; then
  echo "Updating existing scheduler job 'dojo-daily-digest' (Mon-Fri 7:00 AM ET)..."
  gcloud scheduler jobs update http dojo-daily-digest \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "0 7 * * 1-5" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/daily-digest" \
    --update-headers "Authorization=Bearer $CRON_SECRET" \
    --http-method POST
else
  echo "Creating scheduler job 'dojo-daily-digest' (Mon-Fri 7:00 AM ET)..."
  gcloud scheduler jobs create http dojo-daily-digest \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "0 7 * * 1-5" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/daily-digest" \
    --headers "Authorization=Bearer $CRON_SECRET" \
    --http-method POST
fi

# Job C: Sunday evening week-ahead digest (Sunday at 6:00 PM Eastern Time)
if gcloud scheduler jobs describe dojo-sunday-digest --location "$REGION" --project "$PROJECT_ID" &>/dev/null; then
  echo "Updating existing scheduler job 'dojo-sunday-digest' (Sun 6:00 PM ET)..."
  gcloud scheduler jobs update http dojo-sunday-digest \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "0 18 * * 0" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/daily-digest" \
    --update-headers "Authorization=Bearer $CRON_SECRET" \
    --http-method POST
else
  echo "Creating scheduler job 'dojo-sunday-digest' (Sun 6:00 PM ET)..."
  gcloud scheduler jobs create http dojo-sunday-digest \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "0 18 * * 0" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/daily-digest" \
    --headers "Authorization=Bearer $CRON_SECRET" \
    --http-method POST
fi

echo ""
echo "============================================================"
echo "🎉 Dojo Zen is live and running 24/7 on Firebase!"
echo "👉 Web Portal: $PUBLIC_URL"
echo "👉 Direct API: $SERVICE_URL"
echo "============================================================"
