#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$ROOT_DIR/dist}"
PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
VERSION="${RELEASE_VERSION:-$(date +%Y%m%d-%H%M%S)}"
BACKEND_ARCHIVE_PATH="$OUTPUT_DIR/e-help-backend-${VERSION}.tar"
FRONTEND_ARCHIVE_PATH="$OUTPUT_DIR/e-help-frontend-${VERSION}.tar"
NGINX_ARCHIVE_PATH="$OUTPUT_DIR/e-help-nginx-${VERSION}.tar.gz"

mkdir -p "$OUTPUT_DIR"

cd "$ROOT_DIR"

NGINX_DOCKERFILE="$(mktemp)"
trap 'rm -f "$NGINX_DOCKERFILE"' EXIT
printf 'FROM nginx:1.27-alpine\n' > "$NGINX_DOCKERFILE"

echo "Building images for ${PLATFORM}..."
docker buildx build \
  --platform "$PLATFORM" \
  --pull=false \
  --provenance=false \
  --sbom=false \
  --file backend/Dockerfile \
  --tag e-help-backend:release \
  --output "type=docker,dest=${BACKEND_ARCHIVE_PATH}" \
  backend

docker buildx build \
  --platform "$PLATFORM" \
  --pull=false \
  --provenance=false \
  --sbom=false \
  --file frontend/Dockerfile \
  --tag e-help-frontend:release \
  --output "type=docker,dest=${FRONTEND_ARCHIVE_PATH}" \
  frontend

docker buildx build \
  --platform "$PLATFORM" \
  --pull=false \
  --provenance=false \
  --sbom=false \
  --file "$NGINX_DOCKERFILE" \
  --tag nginx:1.27-alpine \
  --output "type=docker,dest=${NGINX_ARCHIVE_PATH%.gz}" \
  /tmp

gzip -f "$BACKEND_ARCHIVE_PATH"
gzip -f "$FRONTEND_ARCHIVE_PATH"
gzip -f "${NGINX_ARCHIVE_PATH%.gz}"

cp docker-compose.deploy.yml "$OUTPUT_DIR/docker-compose.yml"
cp nginx/nginx.conf "$OUTPUT_DIR/nginx.conf"
cp backend/.env.example "$OUTPUT_DIR/backend.env.example"

cat <<EOF

Release package created:
  $OUTPUT_DIR/
  - $(basename "${BACKEND_ARCHIVE_PATH}.gz")
  - $(basename "${FRONTEND_ARCHIVE_PATH}.gz")
  - $(basename "$NGINX_ARCHIVE_PATH")
  - docker-compose.yml
  - nginx.conf
  - backend.env.example

Target platform: $PLATFORM
EOF
