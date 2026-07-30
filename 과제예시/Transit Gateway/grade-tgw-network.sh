#!/usr/bin/env bash
# grade-tgw-network.sh — Transit Gateway 멀티-VPC (중앙집중 Egress + 격리) 채점
# 사용법: PROFILE=lee bash grade-tgw-network.sh <비번호>
# 동작 검증은 SSM Run Command 로 private 인스턴스에서 직접 수행한다.

set -uo pipefail

BNUM="${1:-}"
if [[ -z "$BNUM" ]]; then echo "Usage: $0 <비번호>"; exit 1; fi

PROFILE="${PROFILE:-}"
REGION="${REGION:-ap-northeast-3}"
TOTAL=100
SCORE=0
PREFIX="wsc2026-net"

PF=""
[[ -n "$PROFILE" ]] && PF="--profile $PROFILE"
AWS="aws $PF --region $REGION"

pass() { echo "✅  [+${2}pt] $1"; SCORE=$((SCORE + $2)); }
fail() { echo "❌  [ +0pt] $1 — $2"; }

# SSM 명령 실행 후 표준출력 반환 (실패 시 빈 문자열)
ssm_run() {
  local iid="$1" cmd="$2"
  local cid
  cid=$($AWS ssm send-command --instance-ids "$iid" \
    --document-name "AWS-RunShellScript" \
    --parameters "commands=[\"$cmd\"]" \
    --query 'Command.CommandId' --output text 2>/dev/null)
  [[ -z "$cid" || "$cid" == "None" ]] && { echo ""; return; }
  for _ in $(seq 1 20); do
    sleep 5
    local st
    st=$($AWS ssm get-command-invocation --command-id "$cid" --instance-id "$iid" --query 'Status' --output text 2>/dev/null)
    if [[ "$st" == "Success" || "$st" == "Failed" || "$st" == "TimedOut" ]]; then
      $AWS ssm get-command-invocation --command-id "$cid" --instance-id "$iid" --query 'StandardOutputContent' --output text 2>/dev/null
      return
    fi
  done
  echo ""
}

echo ""
echo "══════════════════════════════════════════════════"
echo "  WSC2026 Transit Gateway 멀티-VPC 채점 (비번호: ${BNUM})"
echo "══════════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────────────────
# SECTION 1: 인프라 (40점)
# ──────────────────────────────────────────────────────
echo "▶ [인프라] VPC 3계층 (egress/app/db) ───────────────"
vpc_id() { $AWS ec2 describe-vpcs --filters "Name=tag:Name,Values=${PREFIX}-vpc-$1-${BNUM}" --query 'Vpcs[0].VpcId' --output text 2>/dev/null; }
VPC_E=$(vpc_id egress); VPC_A=$(vpc_id app); VPC_D=$(vpc_id db)
if [[ "$VPC_E" != "None" && "$VPC_A" != "None" && "$VPC_D" != "None" && -n "$VPC_E$VPC_A$VPC_D" ]]; then
  pass "VPC 3개 (egress/app/db) 존재" 8
else
  fail "VPC 3계층" "egress=$VPC_E app=$VPC_A db=$VPC_D"
fi

echo ""
echo "▶ [인프라] 중앙집중 Egress (IGW + NAT) ─────────────"
NAT=$($AWS ec2 describe-nat-gateways --filter "Name=tag:Name,Values=${PREFIX}-nat-${BNUM}" "Name=state,Values=available" --query 'NatGateways[0].NatGatewayId' --output text 2>/dev/null)
IGW=$($AWS ec2 describe-internet-gateways --filters "Name=tag:Name,Values=${PREFIX}-igw-${BNUM}" --query 'InternetGateways[0].InternetGatewayId' --output text 2>/dev/null)
if [[ "$NAT" != "None" && -n "$NAT" && "$IGW" != "None" && -n "$IGW" ]]; then
  pass "Egress VPC에 NAT GW + IGW" 8
else
  fail "Egress NAT/IGW" "nat=$NAT igw=$IGW"
fi

echo ""
echo "▶ [인프라] Transit Gateway + Attachment 3개 ────────"
TGW=$($AWS ec2 describe-transit-gateways --filters "Name=tag:Name,Values=${PREFIX}-tgw-${BNUM}" "Name=state,Values=available" --query 'TransitGateways[0].TransitGatewayId' --output text 2>/dev/null)
if [[ "$TGW" != "None" && -n "$TGW" ]]; then
  ATT=$($AWS ec2 describe-transit-gateway-vpc-attachments --filters "Name=transit-gateway-id,Values=$TGW" "Name=state,Values=available" --query 'TransitGatewayVpcAttachments | length(@)' --output text 2>/dev/null)
  if [[ "$ATT" -ge 3 ]]; then
    pass "Transit Gateway + VPC Attachment 3개" 12
  else
    fail "TGW Attachment" "attachment ${ATT}개 (3 필요)"
  fi
else
  fail "Transit Gateway" "TGW 없음"
fi

echo ""
echo "▶ [인프라] TGW 라우트 테이블 3개 (세그멘테이션) ─────"
if [[ "$TGW" != "None" && -n "$TGW" ]]; then
  RTC=$($AWS ec2 describe-transit-gateway-route-tables --filters "Name=transit-gateway-id,Values=$TGW" "Name=state,Values=available" --query 'TransitGatewayRouteTables | length(@)' --output text 2>/dev/null)
  if [[ "$RTC" -ge 3 ]]; then
    pass "TGW 라우트 테이블 3개 (app/db/egress 분리)" 12
  else
    fail "TGW 라우트 테이블" "${RTC}개 (3 필요)"
  fi
else
  fail "TGW 라우트 테이블" "TGW 없음"
fi

echo ""
echo "══════════════════════════════════════════════════"
echo "▶ [동작] SSM Run Command 검증 (60점) ───────────────"
echo ""

APP_ID=$($AWS ec2 describe-instances --filters "Name=tag:Name,Values=${PREFIX}-app-ec2-${BNUM}" "Name=instance-state-name,Values=running" --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null)
DB_ID=$($AWS ec2 describe-instances --filters "Name=tag:Name,Values=${PREFIX}-db-ec2-${BNUM}" "Name=instance-state-name,Values=running" --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null)
DB_IP=$($AWS ec2 describe-instances --filters "Name=tag:Name,Values=${PREFIX}-db-ec2-${BNUM}" "Name=instance-state-name,Values=running" --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text 2>/dev/null)
APP_IP=$($AWS ec2 describe-instances --filters "Name=tag:Name,Values=${PREFIX}-app-ec2-${BNUM}" "Name=instance-state-name,Values=running" --query 'Reservations[0].Instances[0].PrivateIpAddress' --output text 2>/dev/null)

if [[ "$APP_ID" == "None" || -z "$APP_ID" || "$DB_ID" == "None" || -z "$DB_ID" ]]; then
  echo "⚠️  App/DB 인스턴스를 찾을 수 없어 동작 테스트를 건너뜁니다."
  printf "  최종 점수: %d / %d 점\n" "$SCORE" "$TOTAL"
  exit 0
fi
echo "  App: $APP_ID ($APP_IP) / DB: $DB_ID ($DB_IP)"
echo ""

echo "▶ [동작] 테스트 1 — App → 인터넷 (중앙 Egress) ─────"
OUT=$(ssm_run "$APP_ID" "curl -s --max-time 8 https://checkip.amazonaws.com || echo NORESP")
if [[ -n "$OUT" && "$OUT" != *"NORESP"* && "$OUT" =~ [0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ ]]; then
  pass "App → 인터넷 성공 (공인IP: $(echo "$OUT" | tr -d '[:space:]'))" 20
else
  fail "App → 인터넷" "응답 없음 (out=${OUT:-비어있음})"
fi

echo ""
echo "▶ [동작] 테스트 2 — App → DB (사설 통신) ───────────"
OUT=$(ssm_run "$APP_ID" "curl -s --max-time 8 http://${DB_IP}/ || echo NORESP")
if echo "$OUT" | grep -q "DB-OK"; then
  pass "App → DB 사설 통신 성공 (DB-OK)" 20
else
  fail "App → DB" "DB-OK 없음 (out=${OUT:0:40})"
fi

echo ""
echo "▶ [동작] 테스트 3 — DB → 인터넷 차단 확인 ──────────"
OUT=$(ssm_run "$DB_ID" "curl -s --max-time 8 https://checkip.amazonaws.com && echo REACHED || echo BLOCKED")
if echo "$OUT" | grep -q "BLOCKED" && ! echo "$OUT" | grep -q "REACHED"; then
  pass "DB → 인터넷 차단됨 (격리 정상)" 20
else
  fail "DB 인터넷 차단" "DB가 인터넷에 도달함 (out=${OUT:0:40})"
fi

echo ""
echo "══════════════════════════════════════════════════"
printf "  최종 점수: %d / %d 점\n" "$SCORE" "$TOTAL"
echo "══════════════════════════════════════════════════"
echo ""
