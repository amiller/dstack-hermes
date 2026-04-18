#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

docker compose down -v 2>/dev/null || true
docker compose up -d continuwuity

echo "waiting for continuwuity at 127.0.0.1:16167..."
for _ in $(seq 1 60); do
    curl -fsS http://127.0.0.1:16167/_matrix/client/versions > /dev/null 2>&1 && break
    sleep 1
done

TOKEN=$(docker compose logs continuwuity 2>&1 \
    | sed 's/\x1b\[[0-9;]*m//g' \
    | grep -oE 'using the registration token [A-Za-z0-9]+' \
    | tail -1 | awk '{print $NF}')

if [ -z "$TOKEN" ]; then
    echo "ERROR: bootstrap token not found in continuwuity logs"
    docker compose logs continuwuity | tail -20
    exit 1
fi
echo "bootstrap token: $TOKEN"

BOOTSTRAP_TOKEN=$TOKEN docker compose run --rm repro
