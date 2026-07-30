#!/usr/bin/env bash
# shopd v1 / v2 이미지를 ECR 로 빌드·푸시한다.
# 사용법: REGION=ap-northeast-2 REPO=shopd bash build-push.sh
set -euo pipefail

PROFILE="${PROFILE:-}"
REGION="${REGION:-ap-northeast-2}"
REPO="${REPO:-shopd}"
PF=""; [[ -n "$PROFILE" ]] && PF="--profile $PROFILE"

cd "$(dirname "$0")"

ACCT=$(aws $PF --region "$REGION" sts get-caller-identity --query Account --output text)
BASE="${ACCT}.dkr.ecr.${REGION}.amazonaws.com/${REPO}"

aws $PF --region "$REGION" ecr get-login-password \
  | docker login --username AWS --password-stdin "${ACCT}.dkr.ecr.${REGION}.amazonaws.com"

# provenance/SBOM 첨부를 끄고 단일 플랫폼 이미지로 푸시한다.
# (첨부가 붙으면 태그가 매니페스트 인덱스를 가리켜 취약점 스캔 대상에서 빠진다)
for V in v1 v2; do
  echo "── building ${BASE}:${V}"
  docker buildx build --platform linux/amd64 \
    --provenance=false --sbom=false \
    -f "Dockerfile.${V}" -t "${BASE}:${V}" --push .
done

echo
echo "푸시 완료:"
echo "  ${BASE}:v1"
echo "  ${BASE}:v2"
