#!/usr/bin/env bash
# Выкатывает образ с указанным SHA: миграции → перезапуск процессов → проверка версии.
# Лежит на сервере в /srv/vas рядом с docker-compose.yml и .env.
set -euo pipefail

SHA="${1:?usage: release.sh <image-sha>}"
REGISTRY="${REGISTRY:-registry.gitlab.com/you/vpn-access-service}"
export VAS_IMAGE="$REGISTRY:$SHA" BUILD_SHA="$SHA"

current="$(cat current_sha 2>/dev/null || true)"

docker compose pull schema api jobs cron
docker compose run --rm schema                       # упадёт — дальше не идём, старый код работает
docker compose up -d --no-deps --wait api jobs cron  # --wait ждёт healthcheck api

for _ in $(seq 1 10); do
  if curl -fsS http://127.0.0.1:8000/version | grep -q "\"$SHA\""; then
    [ -n "$current" ] && [ "$current" != "$SHA" ] && echo "$current" > previous_sha
    echo "$SHA" > current_sha
    echo "released $SHA"
    exit 0
  fi
  sleep 3
done
echo "api did not report version $SHA" >&2
exit 1
