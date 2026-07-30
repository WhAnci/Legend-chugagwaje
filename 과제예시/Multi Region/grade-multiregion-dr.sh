#!/usr/bin/env bash
# grade-multiregion-dr.sh — 멀티리전 DR (Route53 페일오버) 채점 스크립트
# 사용법: ZONE_NAME=<비번호>.gongju.click PROFILE=lee bash grade-multiregion-dr.sh <비번호>
# CloudShell(선수 계정): ZONE_NAME=01.gongju.click bash grade-multiregion-dr.sh 01
# 동작 검증은 위임된 실 도메인(app.<비번호>.gongju.click)에 외부 curl로 수행한다.

set -uo pipefail

BNUM="${1:-}"
if [[ -z "$BNUM" ]]; then
  echo "Usage: ZONE_NAME=<비번호>.gongju.click $0 <비번호>"
  exit 1
fi

PROFILE="${PROFILE:-}"
REGION_P="ap-northeast-2"
REGION_S="ap-northeast-1"
TOTAL=100
SCORE=0

PREFIX="wsc2026-dr"
ZONE_NAME="${ZONE_NAME:-${BNUM}.gongju.click}"
RECORD_NAME="app.${ZONE_NAME}"
APP_URL="http://${RECORD_NAME}"

VPC_P_NAME="${PREFIX}-vpc-p-${BNUM}"
VPC_S_NAME="${PREFIX}-vpc-s-${BNUM}"
ALB_P_NAME="${PREFIX}-alb-p-${BNUM}"
ALB_S_NAME="${PREFIX}-alb-s-${BNUM}"
TG_P_NAME="${PREFIX}-tg-p-${BNUM}"
TG_S_NAME="${PREFIX}-tg-s-${BNUM}"
ASG_P_NAME="${PREFIX}-asg-p-${BNUM}"
ASG_S_NAME="${PREFIX}-asg-s-${BNUM}"

PF=""
[[ -n "$PROFILE" ]] && PF="--profile $PROFILE"
AWS_P="aws $PF --region $REGION_P"
AWS_S="aws $PF --region $REGION_S"
AWS_G="aws $PF"

pass() { echo "✅  [+${2}pt] $1"; SCORE=$((SCORE + $2)); }
fail() { echo "❌  [ +0pt] $1 — $2"; }

echo ""
echo "══════════════════════════════════════════════════"
echo "  WSC2026 멀티리전 DR 채점 (비번호: ${BNUM})"
echo "  도메인: ${RECORD_NAME}"
echo "══════════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────────────────
# SECTION 1: 인프라 (40점)
# ──────────────────────────────────────────────────────
echo "▶ [인프라] 네트워크(VPC) 양 리전 ───────────────────"

VPC_P_ID=$($AWS_P ec2 describe-vpcs --filters "Name=tag:Name,Values=$VPC_P_NAME" 2>/dev/null | jq -r '.Vpcs[0].VpcId // empty' 2>/dev/null)
VPC_S_ID=$($AWS_S ec2 describe-vpcs --filters "Name=tag:Name,Values=$VPC_S_NAME" 2>/dev/null | jq -r '.Vpcs[0].VpcId // empty' 2>/dev/null)

if [[ -n "$VPC_P_ID" && -n "$VPC_S_ID" ]]; then
  # 각 VPC에 서로 다른 AZ 서브넷 2개 + IGW
  P_AZS=$($AWS_P ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_P_ID" 2>/dev/null | jq -r '[.Subnets[].AvailabilityZone] | unique | length' 2>/dev/null || echo 0)
  S_AZS=$($AWS_S ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_S_ID" 2>/dev/null | jq -r '[.Subnets[].AvailabilityZone] | unique | length' 2>/dev/null || echo 0)
  P_IGW=$($AWS_P ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=$VPC_P_ID" 2>/dev/null | jq -r '.InternetGateways | length' 2>/dev/null || echo 0)
  S_IGW=$($AWS_S ec2 describe-internet-gateways --filters "Name=attachment.vpc-id,Values=$VPC_S_ID" 2>/dev/null | jq -r '.InternetGateways | length' 2>/dev/null || echo 0)
  if [[ "$P_AZS" -ge 2 && "$S_AZS" -ge 2 && "$P_IGW" -ge 1 && "$S_IGW" -ge 1 ]]; then
    pass "양 리전 VPC + 다중 AZ 서브넷 + IGW" 10
  else
    fail "VPC 구성" "서브넷 AZ(서울:$P_AZS,도쿄:$S_AZS) / IGW(서울:$P_IGW,도쿄:$S_IGW)"
  fi
else
  fail "VPC" "서울 또는 도쿄 VPC 없음"
fi

echo ""
echo "▶ [인프라] 애플리케이션 계층(ASG) 양 리전 ──────────"

ASG_P=$($AWS_P autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_P_NAME" 2>/dev/null | jq -r '.AutoScalingGroups[0]' 2>/dev/null)
ASG_P_DES=$(echo "$ASG_P" | jq -r '.DesiredCapacity // 0' 2>/dev/null)
ASG_P_HC=$(echo "$ASG_P" | jq -r '.HealthCheckType // empty' 2>/dev/null)
ASG_S=$($AWS_S autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$ASG_S_NAME" 2>/dev/null | jq -r '.AutoScalingGroups[0]' 2>/dev/null)
ASG_S_DES=$(echo "$ASG_S" | jq -r '.DesiredCapacity // 0' 2>/dev/null)
ASG_S_HC=$(echo "$ASG_S" | jq -r '.HealthCheckType // empty' 2>/dev/null)

if [[ "$ASG_P_DES" -ge 2 && "$ASG_P_HC" == "ELB" ]]; then
  pass "서울 ASG desired≥2 + ELB 헬스체크" 5
else
  fail "서울 ASG" "desired=${ASG_P_DES}, hc=${ASG_P_HC:-없음}"
fi
if [[ "$ASG_S_DES" -ge 2 && "$ASG_S_HC" == "ELB" ]]; then
  pass "도쿄 ASG desired≥2 + ELB 헬스체크" 5
else
  fail "도쿄 ASG" "desired=${ASG_S_DES}, hc=${ASG_S_HC:-없음}"
fi

echo ""
echo "▶ [인프라] ALB + Target Group 양 리전 ──────────────"

ALB_P_ARN=$($AWS_P elbv2 describe-load-balancers 2>/dev/null | jq -r ".LoadBalancers[] | select(.LoadBalancerName==\"$ALB_P_NAME\") | .LoadBalancerArn" 2>/dev/null | head -1)
ALB_P_DNS=$($AWS_P elbv2 describe-load-balancers 2>/dev/null | jq -r ".LoadBalancers[] | select(.LoadBalancerName==\"$ALB_P_NAME\") | .DNSName" 2>/dev/null | head -1)
ALB_S_ARN=$($AWS_S elbv2 describe-load-balancers 2>/dev/null | jq -r ".LoadBalancers[] | select(.LoadBalancerName==\"$ALB_S_NAME\") | .LoadBalancerArn" 2>/dev/null | head -1)
ALB_S_DNS=$($AWS_S elbv2 describe-load-balancers 2>/dev/null | jq -r ".LoadBalancers[] | select(.LoadBalancerName==\"$ALB_S_NAME\") | .DNSName" 2>/dev/null | head -1)

P_TG_PATH=""; S_TG_PATH=""
[[ -n "$ALB_P_ARN" ]] && P_TG_PATH=$($AWS_P elbv2 describe-target-groups --load-balancer-arn "$ALB_P_ARN" 2>/dev/null | jq -r ".TargetGroups[] | select(.TargetGroupName==\"$TG_P_NAME\") | .HealthCheckPath" 2>/dev/null | head -1)
[[ -n "$ALB_S_ARN" ]] && S_TG_PATH=$($AWS_S elbv2 describe-target-groups --load-balancer-arn "$ALB_S_ARN" 2>/dev/null | jq -r ".TargetGroups[] | select(.TargetGroupName==\"$TG_S_NAME\") | .HealthCheckPath" 2>/dev/null | head -1)

if [[ "$P_TG_PATH" == "/health" && "$S_TG_PATH" == "/health" ]]; then
  pass "양 리전 ALB + Target Group(/health)" 10
else
  fail "ALB/TG" "서울 path=${P_TG_PATH:-없음}, 도쿄 path=${S_TG_PATH:-없음}"
fi

echo ""
echo "▶ [인프라] Route53 영역 + 페일오버 + 헬스체크 ──────"

ZONE_ID=$($AWS_G route53 list-hosted-zones 2>/dev/null | jq -r ".HostedZones[] | select(.Name==\"${ZONE_NAME}.\") | .Id" 2>/dev/null | head -1 | sed 's|/hostedzone/||')
if [[ -n "$ZONE_ID" ]]; then
  RECS=$($AWS_G route53 list-resource-record-sets --hosted-zone-id "$ZONE_ID" 2>/dev/null)
  PRI=$(echo "$RECS" | jq -r "[.ResourceRecordSets[] | select(.Name==\"${RECORD_NAME}.\" and .Failover==\"PRIMARY\")] | length" 2>/dev/null || echo 0)
  SEC=$(echo "$RECS" | jq -r "[.ResourceRecordSets[] | select(.Name==\"${RECORD_NAME}.\" and .Failover==\"SECONDARY\")] | length" 2>/dev/null || echo 0)
  HC_ID=$(echo "$RECS" | jq -r "[.ResourceRecordSets[] | select(.Name==\"${RECORD_NAME}.\" and .Failover==\"PRIMARY\")][0].HealthCheckId // empty" 2>/dev/null)
  if [[ "$PRI" -ge 1 && "$SEC" -ge 1 && -n "$HC_ID" ]]; then
    pass "페일오버 레코드(P+S) + Primary 헬스체크 연결" 10
  else
    fail "페일오버 레코드" "PRIMARY=${PRI}, SECONDARY=${SEC}, HC=${HC_ID:-없음}"
  fi
else
  fail "Route53 영역" "'$ZONE_NAME' 없음"
  ZONE_ID=""; HC_ID=""
fi

echo ""
echo "══════════════════════════════════════════════════"
echo "▶ [동작] 외부 접속 테스트 (app.${ZONE_NAME}) ──────"
echo ""

# 위임/전파 확인: app.<zone> 가 공인 DNS로 풀리는지
RESOLVED=$(dig +short "$RECORD_NAME" 2>/dev/null | head -1)
if [[ -z "$RESOLVED" ]]; then
  echo "⚠️  app.${ZONE_NAME} 가 공용 DNS로 조회되지 않습니다."
  echo "    → NS 위임이 완료되지 않았거나 전파 대기 중입니다. (동작 테스트 0점)"
  echo ""
  printf "  최종 점수: %d / %d 점\n" "$SCORE" "$TOTAL"
  exit 0
fi
echo "  app.${ZONE_NAME} → $RESOLVED (공용 DNS 조회 성공)"
echo ""

# curl 헬퍼: 캐시 회피 위해 매번 새 조회
fetch_region() {
  local body; body=$(curl -s "$APP_URL/" --max-time 15 2>/dev/null || echo "")
  if echo "$body" | grep -q "ap-northeast-2"; then echo "SEOUL"
  elif echo "$body" | grep -q "ap-northeast-1"; then echo "TOKYO"
  else echo "?"; fi
}

echo "▶ [동작] 테스트 1 — 외부 curl 정상 접속(서울) ──────"
R1=$(fetch_region)
if [[ "$R1" == "SEOUL" ]]; then
  pass "app 도메인 외부 접속 → 서울(Primary) 응답" 20
else
  fail "정상 접속" "서울 응답 아님 (현재: $R1, 헬스체크 안정화 필요할 수 있음)"
fi

echo ""
echo "▶ [동작] 테스트 2 — 서울 리전 장애 → 도쿄 페일오버 ──"
echo "  서울 ASG를 0으로 → 서울 ALB 5xx 유도..."
$AWS_P autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG_P_NAME" \
  --min-size 0 --desired-capacity 0 > /dev/null 2>&1

FO=0
for i in $(seq 1 12); do
  sleep 30
  R=$(fetch_region)
  if [[ "$R" == "TOKYO" ]]; then FO=1; break; fi
  echo "  대기 $i/12 (현재 응답: $R)..."
done

if [[ "$FO" -eq 1 ]]; then
  pass "서울 장애 시 외부 curl → 도쿄(Standby)로 페일오버" 30
else
  fail "페일오버" "도쿄로 전환 안 됨 (DNS TTL/헬스체크 대기 초과)"
fi

echo "  서울 ASG 복구 (min 2 / desired 2)..."
$AWS_P autoscaling update-auto-scaling-group --auto-scaling-group-name "$ASG_P_NAME" \
  --min-size 2 --desired-capacity 2 > /dev/null 2>&1

echo ""
echo "▶ [동작] 테스트 3 — 헬스체크 관측 조회 ────────────"
if [[ -n "${HC_ID:-}" ]]; then
  OBS=$($AWS_G route53 get-health-check-status --health-check-id "$HC_ID" 2>/dev/null | jq -r '.HealthCheckObservations | length' 2>/dev/null || echo 0)
  if [[ "$OBS" -ge 1 ]]; then
    pass "헬스체크 관측 데이터 조회 가능 (${OBS}곳)" 10
  else
    fail "헬스체크 관측" "데이터 없음"
  fi
else
  fail "헬스체크 관측" "헬스체크 ID 없음"
fi

echo ""
echo "══════════════════════════════════════════════════"
printf "  최종 점수: %d / %d 점\n" "$SCORE" "$TOTAL"
echo "══════════════════════════════════════════════════"
echo "  ※ 테스트 2에서 서울 ASG를 0→2로 복구했습니다. 잠시 후 정상화됩니다."
echo ""
