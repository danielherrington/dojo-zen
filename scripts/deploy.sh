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
gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --region "$REGION" \
  --project "$PROJECT_ID" \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 2 \
  --env-vars-file .env.yaml

# Get Cloud Run Service URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')
echo "✓ Cloud Run Service URL: $SERVICE_URL"

# 3. Deploy Firebase Hosting target
echo ""
echo "🌐 Step 3: Deploying Firebase Hosting site '$SITE_ID'..."
firebase deploy --only "hosting:$SITE_TARGET" --project "$PROJECT_ID"

PUBLIC_URL="https://$SITE_ID.web.app"
echo "✓ Public Web Portal URL: $PUBLIC_URL"

# 4. Provision Cloud Scheduler Jobs
echo ""
echo "⏰ Step 4: Configuring Cloud Scheduler Jobs..."

CRON_SECRET="dojo-zen-cron-7e83bc14"

# Job A: Check urgent alerts every 10 minutes (8am to 5pm on weekdays)
# Or */10 * * * * for 24/7 monitoring
if gcloud scheduler jobs describe dojo-check-alerts --location "$REGION" --project "$PROJECT_ID" &>/dev/null; then
  echo "Updating existing scheduler job 'dojo-check-alerts'..."
  gcloud scheduler jobs update http dojo-check-alerts \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "*/10 * * * *" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/check-alerts?secret=$CRON_SECRET" \
    --http-method POST
else
  echo "Creating scheduler job 'dojo-check-alerts'..."
  gcloud scheduler jobs create http dojo-check-alerts \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "*/10 * * * *" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/check-alerts?secret=$CRON_SECRET" \
    --http-method POST
fi

# Job B: Daily morning digest at 7:00 AM Eastern Time
if gcloud scheduler jobs describe dojo-daily-digest --location "$REGION" --project "$PROJECT_ID" &>/dev/null; then
  echo "Updating existing scheduler job 'dojo-daily-digest'..."
  gcloud scheduler jobs update http dojo-daily-digest \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "0 7 * * *" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/daily-digest?secret=$CRON_SECRET" \
    --http-method POST
else
  echo "Creating scheduler job 'dojo-daily-digest'..."
  gcloud scheduler jobs create http dojo-daily-digest \
    --location "$REGION" \
    --project "$PROJECT_ID" \
    --schedule "0 7 * * *" \
    --time-zone "America/New_York" \
    --uri "$SERVICE_URL/api/cron/daily-digest?secret=$CRON_SECRET" \
    --http-method POST
fi

echo ""
echo "============================================================"
echo "🎉 Dojo Zen is live and running 24/7 on Firebase!"
echo "👉 Web Portal: $PUBLIC_URL"
echo "👉 Direct API: $SERVICE_URL"
echo "============================================================"
