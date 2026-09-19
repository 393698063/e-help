#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$ROOT_DIR/dist}"
PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
VERSION="${RELEASE_VERSION:-$(date +%Y%m%d-%H%M%S)}"
ARCHIVE_PATH="$OUTPUT_DIR/e-help-images-${VERSION}.tar.gz"

mkdir -p "$OUTPUT_DIR"

cd "$ROOT_DIR"

echo "Building images for ${PLATFORM}..."
docker build \
  --platform "$PLATFORM" \
  --pull=false \
  --provenance=false \
  --sbom=false \
  --file backend/Dockerfile \
  --tag e-help-backend:release \
  backend

docker build \
  --platform "$PLATFORM" \
  --pull=false \
  --provenance=false \
  --sbom=false \
  --file frontend/Dockerfile \
  --tag e-help-frontend:release \
  frontend

echo "Pulling Nginx image for ${PLATFORM}..."
docker pull --platform "$PLATFORM" nginx:1.27-alpine

docker image inspect \
  e-help-backend:release \
  e-help-frontend:release \
  nginx:1.27-alpine >/dev/null

echo "Exporting images to ${ARCHIVE_PATH}..."
docker save \
  e-help-backend:release \
  e-help-frontend:release \
  nginx:1.27-alpine \
  | gzip > "$ARCHIVE_PATH"

cp docker-compose.deploy.yml "$OUTPUT_DIR/docker-compose.yml"
cp nginx/nginx.conf "$OUTPUT_DIR/nginx.conf"
cp backend/.env.example "$OUTPUT_DIR/backend.env.example"

cat <<EOF

Release package created:
  $OUTPUT_DIR/
  - $(basename "$ARCHIVE_PATH")
  - docker-compose.yml
  - nginx.conf
  - backend.env.example

Target platform: $PLATFORM
EOF
