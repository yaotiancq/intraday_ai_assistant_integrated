#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

IMAGE_NAME="intraday-ai-assistant-integrated:latest"
ARCHIVE_BASENAME="intraday_ai_assistant.tar"
ARCHIVE_GZ="${ARCHIVE_BASENAME}.gz"

SSH_KEY="${SSH_KEY:-$PROJECT_ROOT/ssh-key-2026-05-11.key}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
REMOTE_HOST="${REMOTE_HOST:-129.213.119.79}"
REMOTE_HOME="${REMOTE_HOME:-/home/ubuntu}"
REMOTE_PROJECT_DIR="${REMOTE_PROJECT_DIR:-$REMOTE_HOME/intraday_ai_assistant_integrated}"

if [[ ! -f "$SSH_KEY" ]]; then
  echo "SSH key not found: $SSH_KEY" >&2
  exit 1
fi

echo "[local] Building Docker image..."
docker compose build

echo "[local] Saving image archive..."
rm -f "$ARCHIVE_BASENAME" "$ARCHIVE_GZ"
docker save -o "$ARCHIVE_BASENAME" "$IMAGE_NAME"
gzip -f "$ARCHIVE_BASENAME"

echo "[local] Uploading image archive to $REMOTE_USER@$REMOTE_HOST..."
scp -i "$SSH_KEY" "$ARCHIVE_GZ" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_HOME/"

echo "[remote] Loading image and restarting services..."
ssh -i "$SSH_KEY" "$REMOTE_USER@$REMOTE_HOST" \
  "REMOTE_PROJECT_DIR='$REMOTE_PROJECT_DIR' ARCHIVE_GZ='$ARCHIVE_GZ' ARCHIVE_BASENAME='$ARCHIVE_BASENAME' bash -s" <<'EOF'
set -euo pipefail

cd "$HOME"
rm -f "$ARCHIVE_BASENAME"
gunzip -f "$ARCHIVE_GZ"
docker load -i "$ARCHIVE_BASENAME"

cd "$REMOTE_PROJECT_DIR"
docker compose down
docker compose up -d
EOF

echo "[remote] Deployment complete. Showing monitor logs; press Ctrl+C to stop following."
ssh -i "$SSH_KEY" "$REMOTE_USER@$REMOTE_HOST" \
  "cd '$REMOTE_PROJECT_DIR' && docker compose logs -f monitor"
