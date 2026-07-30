#!/usr/bin/env bash
# grade-route53-failover.sh — Route53 페일오버 고가용성 DNS 채점 스크립트
# 사용법: bash grade-route53-failover.sh <비번호>
# CloudShell: 프로파일 불필요. 로컬: PROFILE=lee bash grade-route53-failover.sh 99

set -uo pipefail

BNUM="${1:-}"
if [[ -z "$BNUM" ]]; then
  echo "Usage: $0 <비번호>"
  exit 1
fi

PROFILE="${PROFILE:-}"
REGION="${REGION:-ap-northeast-2}"
TOTAL=100
SCORE=0

PREFIX="wsc2026-fo"
ZONE_NAME="${PREFIX}-${BNUM}.lab"
RECORD_NAME="app.${PREFIX}-${BNUM}.lab"
PRIMARY_NAME="${PREFIX}-primary-${BNUM}"
SECONDARY_NAME="${PREFIX}-secondary-${BNUM}"

if [[ -n "$PROFILE" ]]; then
  AWS="aws --profile $PROFILE --region $REGION"
else
  AWS="aws --region $REGION"
fi

pass() { echo "✅  [+${2}pt] $1"; SCORE=$((SCORE + $2)); }
fail() { echo "❌  [ +0pt] $1 — $2"; }

echo ""
echo "══════════════════════════════════════════════════"
echo "  WSC2026 Route53 페일오버 채점 (비번호: ${BNUM})"
echo "══════════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────────────────
# SECTION 1: 인프라 (40점)
# ──────────────────────────────────────────────────────
echo "▶ [인프라] EC2 Primary / Secondary ────────────────"

PRIMARY_JSON=$($AWS ec2 describe-instances \
  --filters "Name=tag:Name,Values=$PRIMARY_NAME" "Name=instance-state-name,Values=running,stopped" \
  2>/dev/null | jq -r '[.Reservations[].Instances[]] | sort_by(.LaunchTime) | last' 2>/dev/null)
SECONDARY_JSON=$($AWS ec2 describe-instances \
  --filters "Name=tag:Name,Values=$SECONDARY_NAME" "Name=instance-state-name,Values=running,stopped" \
  2>/dev/null | jq -r '[.Reservations[].Instances[]] | sort_by(.LaunchTime) | last' 2>/dev/null)

PRIMARY_ID=$(echo "$PRIMARY_JSON" | jq -r '.InstanceId // empty' 2>/dev/null)
SECONDARY_ID=$(echo "$SECONDARY_JSON" | jq -r '.InstanceId // empty' 2>/dev/null)
PRIMARY_AZ=$(echo "$PRIMARY_JSON" | jq -r '.Placement.AvailabilityZone // empty' 2>/dev/null)
SECONDARY_AZ=$(echo "$SECONDARY_JSON" | jq -r '.Placement.AvailabilityZone // empty' 2>/dev/null)
PRIMARY_TYPE=$(echo "$PRIMARY_JSON" | jq -r '.InstanceType // empty' 2>/dev/null)

if [[ -n "$PRIMARY_ID" && -n "$SECONDARY_ID" ]]; then
  pass "EC2 Primary / Secondary 인스턴스 존재" 8

  if [[ -n "$PRIMARY_AZ" && -n "$SECONDARY_AZ" && "$PRIMARY_AZ" != "$SECONDARY_AZ" ]]; then
    pass "서로 다른 AZ 배치 ($PRIMARY_AZ / $SECONDARY_AZ)" 6
  else
    fail "AZ 분산" "두 인스턴스가 같은 AZ ($PRIMARY_AZ)"
  fi
else
  fail "EC2 인스턴스" "Primary 또는 Secondary 인스턴스 없음"
  fail "AZ 분산" "인스턴스 없어 확인 불가"
fi

echo ""
echo "▶ [인프라] EIP 고정 IP ────────────────────────────"

PRIMARY_EIP=$($AWS ec2 describe-addresses \
  --filters "Name=instance-id,Values=${PRIMARY_ID:-none}" 2>/dev/null \
  | jq -r '.Addresses[0].PublicIp // empty' 2>/dev/null)
SECONDARY_EIP=$($AWS ec2 describe-addresses \
  --filters "Name=instance-id,Values=${SECONDARY_ID:-none}" 2>/dev/null \
  | jq -r '.Addresses[0].PublicIp // empty' 2>/dev/null)

if [[ -n "$PRIMARY_EIP" && -n "$SECONDARY_EIP" ]]; then
  pass "EIP 2개 연결 (P: $PRIMARY_EIP / S: $SECONDARY_EIP)" 6
else
  fail "EIP" "Primary 또는 Secondary EIP 미연결"
fi

echo ""
echo "▶ [인프라] Route53 호스팅 영역 ────────────────────"

ZONE_ID=$($AWS route53 list-hosted-zones 2>/dev/null \
  | jq -r ".HostedZones[] | select(.Name==\"${ZONE_NAME}.\") | .Id" 2>/dev/null | head -1 | sed 's|/hostedzone/||')

if [[ -n "$ZONE_ID" ]]; then
  pass "Route53 퍼블릭 호스팅 영역 존재" 8

  # 페일오버 레코드 확인
  RECORDS=$($AWS route53 list-resource-record-sets --hosted-zone-id "$ZONE_ID" 2>/dev/null)
  PRI_REC=$(echo "$RECORDS" | jq -r "[.ResourceRecordSets[] | select(.Name==\"${RECORD_NAME}.\" and .Failover==\"PRIMARY\")] | length" 2>/dev/null || echo 0)
  SEC_REC=$(echo "$RECORDS" | jq -r "[.ResourceRecordSets[] | select(.Name==\"${RECORD_NAME}.\" and .Failover==\"SECONDARY\")] | length" 2>/dev/null || echo 0)

  if [[ "$PRI_REC" -ge 1 && "$SEC_REC" -ge 1 ]]; then
    pass "페일오버 레코드 PRIMARY + SECONDARY 구성" 8
  else
    fail "페일오버 레코드" "PRIMARY=${PRI_REC}, SECONDARY=${SEC_REC} (각 1개 필요)"
  fi

  # Primary 레코드에 헬스체크 연결 확인
  HC_LINKED=$(echo "$RECORDS" | jq -r "[.ResourceRecordSets[] | select(.Name==\"${RECORD_NAME}.\" and .Failover==\"PRIMARY\" and .HealthCheckId)] | length" 2>/dev/null || echo 0)
  if [[ "$HC_LINKED" -ge 1 ]]; then
    pass "Primary 레코드에 헬스체크 연결" 4
  else
    fail "헬스체크 연결" "Primary 레코드에 HealthCheckId 없음"
  fi
else
  fail "Route53 호스팅 영역" "'$ZONE_NAME' 없음"
  fail "페일오버 레코드" "호스팅 영역 없어 확인 불가"
  fail "헬스체크 연결" "호스팅 영역 없어 확인 불가"
fi

echo ""
echo "══════════════════════════════════════════════════"
echo "▶ [동작] 테스트 ───────────────────────────────────"
echo ""

if [[ -z "$ZONE_ID" || -z "$PRIMARY_EIP" || -z "$SECONDARY_EIP" ]]; then
  echo "⚠️  필수 리소스(호스팅 영역/EIP)가 없어 동작 테스트를 건너뜁니다."
  echo ""
  echo "══════════════════════════════════════════════════"
  printf "  최종 점수: %d / %d 점\n" "$SCORE" "$TOTAL"
  echo "══════════════════════════════════════════════════"
  exit 0
fi

# 호스팅 영역 네임서버 1개 확보
NS=$($AWS route53 get-hosted-zone --id "$ZONE_ID" 2>/dev/null \
  | jq -r '.DelegationSet.NameServers[0] // empty' 2>/dev/null)
echo "  질의 네임서버: ${NS:-없음}"
echo "  Primary EIP: $PRIMARY_EIP / Secondary EIP: $SECONDARY_EIP"
echo ""

# ──────────────────────────────────────────────────────
# SECTION 2: 동작 테스트 (60점)
# ──────────────────────────────────────────────────────

echo "▶ [동작] 테스트 1 — Primary 웹 응답 (PRIMARY) ──────"
P_BODY=$(curl -s "http://${PRIMARY_EIP}/" --max-time 8 2>/dev/null || echo "")
if echo "$P_BODY" | grep -qi "PRIMARY"; then
  pass "Primary 서버가 PRIMARY 응답 반환" 10
else
  fail "Primary 웹 응답" "PRIMARY 문자열 없음 (응답: ${P_BODY:0:40})"
fi

echo ""
echo "▶ [동작] 테스트 2 — Secondary 웹 응답 (SECONDARY) ──"
S_BODY=$(curl -s "http://${SECONDARY_EIP}/" --max-time 8 2>/dev/null || echo "")
if echo "$S_BODY" | grep -qi "SECONDARY"; then
  pass "Secondary 서버가 SECONDARY 응답 반환" 10
else
  fail "Secondary 웹 응답" "SECONDARY 문자열 없음 (응답: ${S_BODY:0:40})"
fi

echo ""
echo "▶ [동작] 테스트 3 — 정상 시 Primary로 DNS 응답 ─────"
DNS_NORMAL=""
if [[ -n "$NS" ]]; then
  DNS_NORMAL=$(dig "@${NS}" "$RECORD_NAME" +short 2>/dev/null | head -1)
fi
if [[ "$DNS_NORMAL" == "$PRIMARY_EIP" ]]; then
  pass "정상 상태에서 DNS가 Primary EIP 반환" 15
else
  fail "정상 DNS 응답" "예상 $PRIMARY_EIP, 실제 ${DNS_NORMAL:-없음} (헬스체크 안정화 대기 필요할 수 있음)"
fi

echo ""
echo "▶ [동작] 테스트 4 — 장애 주입 → 자동 페일오버 ──────"
if [[ -z "$PRIMARY_ID" ]]; then
  fail "페일오버 전환" "Primary 인스턴스 ID 없음"
else
  echo "  Primary 인스턴스($PRIMARY_ID) 중지 → 헬스체크 실패 유도..."
  $AWS ec2 stop-instances --instance-ids "$PRIMARY_ID" > /dev/null 2>&1

  FAILOVER_OK=0
  # 헬스체크(10초×3) + 전파 대기, 최대 약 4분 폴링
  for i in 1 2 3 4 5 6 7 8; do
    sleep 30
    DNS_FAIL=$(dig "@${NS}" "$RECORD_NAME" +short 2>/dev/null | head -1)
    if [[ "$DNS_FAIL" == "$SECONDARY_EIP" ]]; then
      FAILOVER_OK=1
      break
    fi
    echo "  대기 $i/8 (현재 DNS 응답: ${DNS_FAIL:-없음})..."
  done

  if [[ "$FAILOVER_OK" -eq 1 ]]; then
    pass "Primary 장애 시 DNS가 Secondary EIP로 자동 전환" 20
  else
    fail "페일오버 전환" "Secondary로 전환 안 됨 (마지막 응답: ${DNS_FAIL:-없음})"
  fi

  echo "  Primary 인스턴스 재시작(복구)..."
  $AWS ec2 start-instances --instance-ids "$PRIMARY_ID" > /dev/null 2>&1
fi

echo ""
echo "▶ [동작] 테스트 5 — 헬스체크 상태 조회 가능 ────────"
HC_ID=$($AWS route53 list-resource-record-sets --hosted-zone-id "$ZONE_ID" 2>/dev/null \
  | jq -r "[.ResourceRecordSets[] | select(.Name==\"${RECORD_NAME}.\" and .Failover==\"PRIMARY\")][0].HealthCheckId // empty" 2>/dev/null)
if [[ -n "$HC_ID" ]]; then
  HC_STATUS=$($AWS route53 get-health-check-status --health-check-id "$HC_ID" 2>/dev/null \
    | jq -r '.HealthCheckObservations | length' 2>/dev/null || echo 0)
  if [[ "$HC_STATUS" -ge 1 ]]; then
    pass "헬스체크 관측 데이터 조회 가능 (체커 ${HC_STATUS}곳)" 5
  else
    fail "헬스체크 상태" "관측 데이터 없음"
  fi
else
  fail "헬스체크 상태" "헬스체크 ID 조회 실패"
fi

echo ""
echo "══════════════════════════════════════════════════"
printf "  최종 점수: %d / %d 점\n" "$SCORE" "$TOTAL"
echo "══════════════════════════════════════════════════"
echo ""
echo "  ※ 테스트 4에서 Primary를 재시작했습니다. 잠시 후 헬스체크가"
echo "    복구되며 DNS가 다시 Primary로 돌아옵니다."
echo ""
