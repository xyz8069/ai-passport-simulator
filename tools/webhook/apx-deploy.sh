#!/usr/bin/env bash
# Auto-deploy for ai-passport-simulator: pull Gitee main, rebuild if changed.
# Triggered by the webhook receiver; safe to run manually.
set -uo pipefail

exec 9>/tmp/apx-deploy.lock
if ! flock -n 9; then
  echo "[$(date '+%F %T')] deploy skipped: another deploy is running"
  exit 0
fi

REPO=/opt/ai-passport-simulator
GITEE_URL=https://gitee.com/xyz8069/ai-passport-simulator.git

cd "$REPO" || exit 1

# Ensure the pull remote exists (server pulls from Gitee; it is reachable).
git remote add origin "$GITEE_URL" 2>/dev/null \
  || git remote set-url origin "$GITEE_URL"
if ! git fetch origin main --quiet; then
  echo "[$(date '+%F %T')] fetch failed from $GITEE_URL"
  exit 1
fi

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" = "$REMOTE" ]; then
  echo "[$(date '+%F %T')] up-to-date at $LOCAL"
  exit 0
fi

echo "[$(date '+%F %T')] deploying $LOCAL -> $REMOTE"
git reset --hard origin/main || exit 1

if docker compose build \
     --build-arg APT_MIRROR=mirrors.aliyun.com \
     --build-arg QEMU_SRC_URL=http://192.168.0.1:8001/qemu-src-febae18-bundle.tar.gz \
     --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
 && docker compose up -d; then
  echo "[$(date '+%F %T')] containers restarted, health check..."
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 5
    if curl -sf http://127.0.0.1:8000/api/health > /dev/null; then
      echo "[$(date '+%F %T')] deploy OK at $REMOTE (healthy)"
      exit 0
    fi
  done
  echo "[$(date '+%F %T')] deploy finished but health check FAILED at $REMOTE"
  exit 1
fi

echo "[$(date '+%F %T')] build FAILED for $REMOTE"
exit 1
