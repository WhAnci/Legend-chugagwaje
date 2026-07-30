#!/usr/bin/env bash
# grade-serverless-auth.sh — ALB Cognito 인증 포털 채점 스크립트
# 사용법: bash grade-serverless-auth.sh <비번호>

set -uo pipefail

BNUM="${1:-}"
if [[ -z "$BNUM" ]]; then
  echo "Usage: $0 <비번호>"
  exit 1
fi

# CloudShell: 프로파일 불필요 (콘솔 로그인 계정 자격증명 자동 사용)
# 로컬에서 특정 프로파일로 돌리려면:  PROFILE=lee bash grade-serverless-auth.sh 99
PROFILE="${PROFILE:-}"
REGION="${REGION:-ap-northeast-2}"
TOTAL=100
SCORE=0

PREFIX="wsc2026-auth"
POOL_NAME="${PREFIX}-pool-${BNUM}"
CLUSTER_NAME="${PREFIX}-cluster-${BNUM}"
SERVICE_NAME="${PREFIX}-service-${BNUM}"
ALB_NAME="${PREFIX}-alb-${BNUM}"
TG_NAME="${PREFIX}-tg-${BNUM}"
ALARM_NAME="${PREFIX}-5xx-${BNUM}"

if [[ -n "$PROFILE" ]]; then
  AWS="aws --profile $PROFILE --region $REGION"
else
  AWS="aws --region $REGION"
fi

pass() { echo "✅  [+${2}pt] $1"; SCORE=$((SCORE + $2)); }
fail() { echo "❌  [ +0pt] $1 — $2"; }

echo ""
echo "══════════════════════════════════════════════════"
echo "  WSC2026 ALB Cognito 인증 포털 채점 (비번호: ${BNUM})"
echo "══════════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────────────────
# SECTION 1: 인프라 (40점)
# ──────────────────────────────────────────────────────
echo "▶ [인프라] Cognito User Pool ──────────────────────"

POOL_ID=$($AWS cognito-idp list-user-pools --max-results 60 2>/dev/null \
  | jq -r ".UserPools[] | select(.Name==\"$POOL_NAME\") | .Id" 2>/dev/null | head -1)

if [[ -n "$POOL_ID" ]]; then
  # Hosted UI 도메인 확인
  POOL_DOMAIN=$($AWS cognito-idp describe-user-pool \
    --user-pool-id "$POOL_ID" 2>/dev/null \
    | jq -r '.UserPool.Domain // empty' 2>/dev/null)
  if [[ -n "$POOL_DOMAIN" ]]; then
    pass "Cognito User Pool + Hosted UI 도메인" 8
  else
    pass "Cognito User Pool 존재 (도메인 없음)" 4
    fail "Cognito Hosted UI 도메인" "도메인 미설정"
  fi
else
  fail "Cognito User Pool" "'$POOL_NAME' 없음"
  POOL_ID=""
fi

echo ""
echo "▶ [인프라] Cognito App Client ─────────────────────"

if [[ -n "${POOL_ID:-}" ]]; then
  CLIENTS=$($AWS cognito-idp list-user-pool-clients \
    --user-pool-id "$POOL_ID" 2>/dev/null \
    | jq -r '.UserPoolClients[]?.ClientId' 2>/dev/null)
  CLIENT_ID=$(echo "$CLIENTS" | head -1)

  if [[ -n "$CLIENT_ID" ]]; then
    CLIENT_DETAIL=$($AWS cognito-idp describe-user-pool-client \
      --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" 2>/dev/null)

    HAS_CODE=$(echo "$CLIENT_DETAIL" | jq -r '.UserPoolClient.AllowedOAuthFlows[]?' 2>/dev/null | grep -c "code" || true)
    HAS_CB=$(echo "$CLIENT_DETAIL" | jq -r '.UserPoolClient.CallbackURLs[]?' 2>/dev/null | grep -c "oauth2/idpresponse" || true)

    if [[ "$HAS_CODE" -ge 1 && "$HAS_CB" -ge 1 ]]; then
      pass "App Client (authorization_code + callback URL 설정)" 6
    elif [[ "$HAS_CB" -ge 1 ]]; then
      fail "App Client OAuth Flow" "authorization_code grant 없음"
    elif [[ "$HAS_CODE" -ge 1 ]]; then
      fail "App Client callback URL" "/oauth2/idpresponse 없음"
    else
      fail "App Client" "authorization_code 및 callback URL 모두 없음"
    fi
  else
    fail "App Client" "User Pool에 클라이언트 없음"
    CLIENT_ID=""
  fi
else
  fail "App Client" "User Pool 없어 확인 불가"
fi

echo ""
echo "▶ [인프라] ALB ─────────────────────────────────────"

ALB_ARN=$($AWS elbv2 describe-load-balancers 2>/dev/null \
  | jq -r ".LoadBalancers[] | select(.LoadBalancerName==\"$ALB_NAME\") | .LoadBalancerArn" 2>/dev/null | head -1)
ALB_DNS=$($AWS elbv2 describe-load-balancers 2>/dev/null \
  | jq -r ".LoadBalancers[] | select(.LoadBalancerName==\"$ALB_NAME\") | .DNSName" 2>/dev/null | head -1)

if [[ -n "$ALB_ARN" ]]; then
  # HTTPS 리스너 확인
  HTTPS_LISTENER=$($AWS elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" 2>/dev/null \
    | jq -r '.Listeners[] | select(.Port==443) | .ListenerArn' 2>/dev/null | head -1)

  if [[ -n "$HTTPS_LISTENER" ]]; then
    pass "ALB + HTTPS:443 리스너 존재" 6

    # authenticate-cognito action 확인
    AUTH_ACTION=$($AWS elbv2 describe-listeners --load-balancer-arn "$ALB_ARN" 2>/dev/null \
      | jq -r '.Listeners[].DefaultActions[].Type' 2>/dev/null | grep -c "authenticate-cognito" || true)
    if [[ "$AUTH_ACTION" -ge 1 ]]; then
      pass "HTTPS 리스너 authenticate-cognito action 설정" 8
    else
      fail "authenticate-cognito action" "HTTPS 리스너에 Cognito 인증 액션 없음"
    fi
  else
    fail "ALB HTTPS 리스너" "port 443 리스너 없음"
    fail "authenticate-cognito action" "HTTPS 리스너 없어 확인 불가"
  fi
else
  fail "ALB" "'$ALB_NAME' 없음"
  fail "ALB HTTPS 리스너" "ALB 없어 확인 불가"
  fail "authenticate-cognito action" "ALB 없어 확인 불가"
  ALB_DNS=""
fi

echo ""
echo "▶ [인프라] ECS Fargate ────────────────────────────"

CLUSTER_ARN=$($AWS ecs describe-clusters --clusters "$CLUSTER_NAME" 2>/dev/null \
  | jq -r ".clusters[] | select(.status==\"ACTIVE\") | .clusterArn" 2>/dev/null | head -1)

if [[ -n "$CLUSTER_ARN" ]]; then
  SVC_DETAIL=$($AWS ecs describe-services \
    --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" 2>/dev/null \
    | jq -r '.services[0]' 2>/dev/null)
  SVC_STATUS=$(echo "$SVC_DETAIL" | jq -r '.status // empty' 2>/dev/null)
  DESIRED=$(echo "$SVC_DETAIL" | jq -r '.desiredCount // 0' 2>/dev/null)

  if [[ "$SVC_STATUS" == "ACTIVE" && "$DESIRED" -ge 2 ]]; then
    pass "ECS Fargate 서비스 ACTIVE (desired: $DESIRED)" 6
  elif [[ "$SVC_STATUS" == "ACTIVE" ]]; then
    fail "ECS desired count" "desired ${DESIRED} (최소 2 필요)"
  else
    fail "ECS 서비스" "'$SERVICE_NAME' ACTIVE 아님 (상태: ${SVC_STATUS:-없음})"
  fi

  # ECS SG 격리 확인
  ECS_SG_ID=$($AWS ec2 describe-security-groups 2>/dev/null \
    | jq -r ".SecurityGroups[] | select(.GroupName | contains(\"${PREFIX}-ecs-sg-${BNUM}\")) | .GroupId" 2>/dev/null | head -1)

  if [[ -n "$ECS_SG_ID" ]]; then
    OPEN_INBOUND=$($AWS ec2 describe-security-groups --group-ids "$ECS_SG_ID" 2>/dev/null \
      | jq '[.SecurityGroups[0].IpPermissions[]?.IpRanges[]? | select(.CidrIp=="0.0.0.0/0")] | length' 2>/dev/null || echo 1)
    if [[ "$OPEN_INBOUND" -eq 0 ]]; then
      pass "ECS SG inbound: 0.0.0.0/0 없음 (ALB only)" 3
    else
      fail "ECS SG 격리" "0.0.0.0/0 인바운드 규칙 존재"
    fi
  else
    fail "ECS SG" "ECS 보안 그룹 없음"
  fi
else
  fail "ECS 클러스터" "'$CLUSTER_NAME' 없거나 ACTIVE 아님"
  fail "ECS 서비스" "클러스터 없어 확인 불가"
  fail "ECS SG 격리" "클러스터 없어 확인 불가"
fi

echo ""
echo "▶ [인프라] CloudWatch Alarm ───────────────────────"

ALARM_STATE=$($AWS cloudwatch describe-alarms --alarm-names "$ALARM_NAME" 2>/dev/null \
  | jq -r '.MetricAlarms[0].StateValue // empty' 2>/dev/null)

if [[ -n "$ALARM_STATE" ]]; then
  pass "ALB 5xx 알람 존재 (상태: $ALARM_STATE)" 3
else
  fail "CW Alarm" "'$ALARM_NAME' 없음"
fi

echo ""
echo "══════════════════════════════════════════════════"
echo "▶ [동작] 테스트 ───────────────────────────────────"
echo ""

if [[ -z "${ALB_DNS:-}" ]]; then
  echo "⚠️  ALB가 없어 동작 테스트를 건너뜁니다."
  echo ""
  echo "══════════════════════════════════════════════════"
  printf "  최종 점수: %d / %d 점\n" "$SCORE" "$TOTAL"
  echo "══════════════════════════════════════════════════"
  exit 0
fi

echo "  ALB DNS: $ALB_DNS"
echo ""

# ──────────────────────────────────────────────────────
# SECTION 2: 동작 테스트 (60점)
# ──────────────────────────────────────────────────────
echo "▶ [동작] 테스트 1 — HTTP → HTTPS 리다이렉트 ────────"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  "http://${ALB_DNS}/" --max-time 10 2>/dev/null || echo "000")
REDIRECT_LOC=$(curl -s -o /dev/null -w "%{redirect_url}" \
  "http://${ALB_DNS}/" --max-time 10 2>/dev/null || echo "")

if [[ "$HTTP_CODE" =~ ^30[12]$ ]] && echo "$REDIRECT_LOC" | grep -qi "https"; then
  pass "HTTP:80 → HTTPS 301/302 리다이렉트" 10
else
  fail "HTTP → HTTPS 리다이렉트" "응답코드: $HTTP_CODE, Location: ${REDIRECT_LOC:-없음}"
fi

echo ""
echo "▶ [동작] 테스트 2 — 미인증 접근 → Cognito redirect ─"
HTTPS_CODE=$(curl -sk -o /dev/null -w "%{http_code}" \
  "https://${ALB_DNS}/" --max-time 15 2>/dev/null || echo "000")
HTTPS_LOC=$(curl -sk -o /dev/null -w "%{redirect_url}" \
  "https://${ALB_DNS}/" --max-time 15 2>/dev/null || echo "")

if [[ "$HTTPS_CODE" =~ ^30[12]$ ]] && echo "$HTTPS_LOC" | grep -qi "amazoncognito.com"; then
  pass "미인증 HTTPS 접근 → Cognito 302 리다이렉트" 15
elif [[ "$HTTPS_CODE" =~ ^30[12]$ ]]; then
  fail "Cognito 리다이렉트" "302이지만 Location이 Cognito 아님: $HTTPS_LOC"
else
  fail "미인증 접근 차단" "예상 302, 실제: $HTTPS_CODE"
fi

echo ""
echo "▶ [동작] 테스트 3 — ECS 서비스 안정성 ──────────────"
if [[ -n "${CLUSTER_ARN:-}" ]]; then
  SVC_RUNNING=$($AWS ecs describe-services \
    --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" 2>/dev/null \
    | jq '.services[0].runningCount // 0' 2>/dev/null)
  SVC_DESIRED=$($AWS ecs describe-services \
    --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" 2>/dev/null \
    | jq '.services[0].desiredCount // 0' 2>/dev/null)

  if [[ "$SVC_RUNNING" -ge "$SVC_DESIRED" && "$SVC_DESIRED" -ge 2 ]]; then
    pass "ECS running($SVC_RUNNING) ≥ desired($SVC_DESIRED)" 10
  else
    fail "ECS 서비스 안정성" "running=${SVC_RUNNING}, desired=${SVC_DESIRED}"
  fi
else
  fail "ECS 서비스 안정성" "클러스터 없음"
fi

echo ""
echo "▶ [동작] 테스트 4 — ECS 직접 접근 차단 ─────────────"
if [[ -n "${CLUSTER_ARN:-}" ]]; then
  TASK_ARN=$($AWS ecs list-tasks --cluster "$CLUSTER_NAME" \
    --service-name "$SERVICE_NAME" 2>/dev/null \
    | jq -r '.taskArns[0] // empty' 2>/dev/null)

  if [[ -n "$TASK_ARN" ]]; then
    # ENI에서 공인 IP 조회
    ENI_ID=$($AWS ecs describe-tasks --cluster "$CLUSTER_NAME" --tasks "$TASK_ARN" 2>/dev/null \
      | jq -r '.tasks[0].attachments[0].details[] | select(.name=="networkInterfaceId") | .value' 2>/dev/null | head -1)

    PUBLIC_IP=""
    if [[ -n "$ENI_ID" ]]; then
      PUBLIC_IP=$($AWS ec2 describe-network-interfaces --network-interface-ids "$ENI_ID" 2>/dev/null \
        | jq -r '.NetworkInterfaces[0].Association.PublicIp // empty' 2>/dev/null | head -1)
    fi

    if [[ -n "$PUBLIC_IP" ]]; then
      DIRECT_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://${PUBLIC_IP}:80/" --connect-timeout 6 --max-time 8 2>/dev/null)
      if [[ "$DIRECT_CODE" == "000" || -z "$DIRECT_CODE" ]]; then
        pass "ECS task 공인IP($PUBLIC_IP) 직접 접근 차단 (SG 거부)" 15
      else
        fail "ECS 직접 접근 차단" "공인IP로 접근 가능 (응답: $DIRECT_CODE) — ECS SG 확인 필요"
      fi
    else
      fail "ECS 직접 접근 차단" "공인IP 조회 실패 (ENI: ${ENI_ID:-없음})"
    fi
  else
    fail "ECS 직접 접근 차단" "실행 중인 task 없음"
  fi
else
  fail "ECS 직접 접근 차단" "클러스터 없음"
fi

echo ""
echo "▶ [동작] 테스트 5 — App Client callback URL 일치 ───"
if [[ -n "${POOL_ID:-}" && -n "${CLIENT_ID:-}" ]]; then
  CB_URLS=$($AWS cognito-idp describe-user-pool-client \
    --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" 2>/dev/null \
    | jq -r '.UserPoolClient.CallbackURLs[]?' 2>/dev/null)

  if echo "$CB_URLS" | grep -qi "$ALB_DNS"; then
    pass "App Client callback URL에 ALB DNS 포함" 10
  else
    fail "App Client callback URL" "callback URL에 ALB DNS($ALB_DNS) 없음"
    echo "      현재 callback URLs:"
    echo "$CB_URLS" | sed 's/^/      - /'
  fi
else
  fail "App Client callback URL" "User Pool 또는 Client 없어 확인 불가"
fi

echo ""
echo "══════════════════════════════════════════════════"
printf "  최종 점수: %d / %d 점\n" "$SCORE" "$TOTAL"
echo "══════════════════════════════════════════════════"
echo ""
