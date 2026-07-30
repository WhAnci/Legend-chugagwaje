#!/usr/bin/env bash
# grade-ecr-gate.sh — 제7과제 컨테이너 이미지 승격 게이트 채점 (5점, 12항목)
# 사용법: bash grade-ecr-gate.sh
#         PROFILE=lee bash grade-ecr-gate.sh   (로컬 검증용)
set -uo pipefail

PROFILE="${PROFILE:-}"
PF=""; [[ -n "$PROFILE" ]] && PF="--profile $PROFILE"

# 리전은 고정하지 않는다. REGION 이 주어지면 그 리전을, 아니면 리포지토리가 있는 리전을 찾아 쓴다.
find_region() {
  local CANDS R
  CANDS=$(aws $PF ec2 describe-regions --query 'Regions[].RegionName' --output text 2>/dev/null)
  [[ -z "$CANDS" ]] && CANDS="ap-northeast-2 ap-northeast-1 us-west-2 us-east-1 ap-southeast-1 eu-west-1"
  for R in $CANDS; do
    if aws $PF --region "$R" ecr describe-repositories \
         --query "repositories[?starts_with(repositoryName, 'wsc2026-shopd-')] | [0].repositoryName" \
         --output text 2>/dev/null | grep -q '^wsc2026-shopd-'; then
      echo "$R"; return
    fi
  done
}

REGION="${REGION:-$(find_region)}"
REGION="${REGION:-ap-northeast-2}"
AWS="aws $PF --region $REGION"
echo "  대상 리전: $REGION"

# 총점 5점. 항목 배점은 0.1점 단위이므로 내부적으로는 10배 정수로 누적한다.
SCORE10=0; TOTAL="5.0"
pass() { printf "✅  [+%.1fpt] %s\n" "$(echo "$2" | awk '{print $1/10}')" "$1"; SCORE10=$((SCORE10 + $2)); }
fail() { echo "❌  [ +0.0pt] $1 — $2"; }

echo "── 제7과제 채점 시작 ─────────────────────────────"
echo

# ══ I1. ECR 리포지토리 ════════════════════════════════
REPO=$($AWS ecr describe-repositories \
  --query "repositories[?starts_with(repositoryName, 'wsc2026-shopd-')].repositoryName | [0]" \
  --output text 2>/dev/null)
MUT=""; URI=""
if [[ -n "$REPO" && "$REPO" != "None" ]]; then
  MUT=$($AWS ecr describe-repositories --repository-names "$REPO" \
    --query 'repositories[0].imageTagMutability' --output text 2>/dev/null)
  URI=$($AWS ecr describe-repositories --repository-names "$REPO" \
    --query 'repositories[0].repositoryUri' --output text 2>/dev/null)
fi
if [[ -n "$REPO" && "$REPO" != "None" && "$MUT" == "MUTABLE" ]]; then
  pass "I1. ECR 리포지토리 (MUTABLE 태그)" 3
else
  fail "I1. ECR 리포지토리" "repo=${REPO:-없음} mutability=${MUT:-none}"
fi

# ══ I2. 레지스트리 강화 스캔 + 푸시 시 스캔 ═════════════
SCANJSON=$($AWS ecr get-registry-scanning-configuration --output json 2>/dev/null)
SC=$(python3 - "${SCANJSON:-{\}}" "${REPO:-none}" <<'EOF'
import sys, json
cfg = json.loads(sys.argv[1] or "{}").get("scanningConfiguration", {})
repo = sys.argv[2]
if cfg.get("scanType") != "ENHANCED":
    print("NG|scanType=" + str(cfg.get("scanType")))
    raise SystemExit
for rule in cfg.get("rules", []):
    if rule.get("scanFrequency") != "SCAN_ON_PUSH":
        continue
    for f in rule.get("repositoryFilters", []):
        pat = f.get("filter", "")
        # WILDCARD 필터가 리포지토리에 매치되는지
        if pat == "*" or pat.rstrip("*") == "" or repo.startswith(pat.rstrip("*")):
            print("OK|")
            raise SystemExit
print("NG|SCAN_ON_PUSH 규칙이 해당 리포지토리에 적용되지 않음")
EOF
)
if [[ "${SC%%|*}" == "OK" ]]; then
  pass "I2. 레지스트리 강화 스캔 + 푸시 시 스캔" 5
else
  fail "I2. 레지스트리 스캔 설정" "${SC#*|}"
fi

# ══ I3. v1 / v2 이미지 존재 ════════════════════════════
DIG_V1=""; DIG_V2=""
if [[ -n "$REPO" && "$REPO" != "None" ]]; then
  DIG_V1=$($AWS ecr describe-images --repository-name "$REPO" --image-ids imageTag=v1 \
    --query 'imageDetails[0].imageDigest' --output text 2>/dev/null)
  DIG_V2=$($AWS ecr describe-images --repository-name "$REPO" --image-ids imageTag=v2 \
    --query 'imageDetails[0].imageDigest' --output text 2>/dev/null)
fi
if [[ -n "$DIG_V1" && "$DIG_V1" != "None" && -n "$DIG_V2" && "$DIG_V2" != "None" ]]; then
  pass "I3. v1 / v2 이미지 푸시 완료" 3
else
  fail "I3. 이미지 푸시" "v1=${DIG_V1:0:20} v2=${DIG_V2:0:20}"
fi

# ══ I4. 스캔 완료 + 정확히 한 쪽만 CRITICAL ═════════════
crit_of() {   # $1 = digest → CRITICAL 개수, 조회 실패 시 "?"
  local D="$1" OUT
  # 스캔 결과는 페이지네이션되므로 첫 페이지 값만 취한다.
  OUT=$($AWS ecr describe-image-scan-findings --repository-name "$REPO" \
        --image-id imageDigest="$D" --max-items 1 \
        --query 'imageScanFindings.findingSeverityCounts.CRITICAL' --output text 2>/dev/null | head -1)
  if [[ -z "$OUT" ]]; then echo "?"; elif [[ "$OUT" == "None" ]]; then echo "0"; else echo "$OUT"; fi
}
C1="?"; C2="?"
[[ -n "$DIG_V1" && "$DIG_V1" != "None" ]] && C1=$(crit_of "$DIG_V1")
[[ -n "$DIG_V2" && "$DIG_V2" != "None" ]] && C2=$(crit_of "$DIG_V2")

CLEAN_DIGEST=""; VULN_DIGEST=""; CLEAN_TAG=""
if [[ "$C1" =~ ^[0-9]+$ && "$C2" =~ ^[0-9]+$ ]]; then
  if [[ "$C1" -gt 0 && "$C2" -eq 0 ]]; then
    VULN_DIGEST="$DIG_V1"; CLEAN_DIGEST="$DIG_V2"; CLEAN_TAG="v2"
  elif [[ "$C2" -gt 0 && "$C1" -eq 0 ]]; then
    VULN_DIGEST="$DIG_V2"; CLEAN_DIGEST="$DIG_V1"; CLEAN_TAG="v1"
  fi
fi
if [[ -n "$CLEAN_DIGEST" ]]; then
  pass "I4. 두 이미지 스캔 완료 — 정확히 한 쪽만 CRITICAL 보유" 4
else
  fail "I4. 스캔 결과" "CRITICAL v1=${C1} v2=${C2} (스캔 미완료이거나 판정 불가)"
fi

# ══ I5. Lambda 함수 ═══════════════════════════════════
FN=$($AWS lambda list-functions \
  --query "Functions[?starts_with(FunctionName, 'wsc2026-gate-')].FunctionName | [0]" \
  --output text 2>/dev/null)
RT=""; HD=""; TO=""; ENV_REPO=""; ENV_TAG=""; ROLE=""
if [[ -n "$FN" && "$FN" != "None" ]]; then
  CFG=$($AWS lambda get-function-configuration --function-name "$FN" --output json 2>/dev/null)
  RT=$(echo "$CFG" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("Runtime",""))' 2>/dev/null)
  HD=$(echo "$CFG" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("Handler",""))' 2>/dev/null)
  TO=$(echo "$CFG" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("Timeout",0))' 2>/dev/null)
  ROLE=$(echo "$CFG" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("Role",""))' 2>/dev/null)
  ENV_REPO=$(echo "$CFG" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("Environment",{}).get("Variables",{}).get("REPO",""))' 2>/dev/null)
  ENV_TAG=$(echo "$CFG" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("Environment",{}).get("Variables",{}).get("PROMOTE_TAG",""))' 2>/dev/null)
fi
if [[ "$RT" == "python3.12" && "$HD" == "gate.handler" && "${TO:-0}" -ge 30 \
      && "$ENV_REPO" == "$REPO" && "$ENV_TAG" == "prod" ]]; then
  pass "I5. 게이트 Lambda (런타임·핸들러·타임아웃·환경변수)" 4
else
  fail "I5. 게이트 Lambda" "runtime=${RT:-none} handler=${HD:-none} timeout=${TO:-0} REPO=${ENV_REPO:-none} PROMOTE_TAG=${ENV_TAG:-none}"
fi

# ══ I6. Lambda 실행 역할 — 과도한 관리형 정책 금지 ══════
ROLE_NAME="${ROLE##*/}"
BAD=""
if [[ -n "$ROLE_NAME" ]]; then
  BAD=$(aws $PF iam list-attached-role-policies --role-name "$ROLE_NAME" \
    --query "AttachedPolicies[?PolicyName=='AdministratorAccess' || contains(PolicyName, 'FullAccess')].PolicyName" \
    --output text 2>/dev/null)
fi
if [[ -n "$ROLE_NAME" && ( -z "$BAD" || "$BAD" == "None" ) ]]; then
  pass "I6. Lambda 실행 역할에 과도한 관리형 정책 없음" 3
else
  fail "I6. Lambda 실행 역할" "부착된 과도 정책=${BAD:-역할 조회 실패}"
fi

# ══ I7. EventBridge 규칙 — 스캔 이벤트 → Lambda ═════════
RULE=$($AWS events list-rules \
  --query "Rules[?starts_with(Name, 'wsc2026-gate-rule-')].Name | [0]" --output text 2>/dev/null)
RSTATE=""; RPATTERN=""; RTARGET=""
if [[ -n "$RULE" && "$RULE" != "None" ]]; then
  RSTATE=$($AWS events describe-rule --name "$RULE" --query 'State' --output text 2>/dev/null)
  RPATTERN=$($AWS events describe-rule --name "$RULE" --query 'EventPattern' --output text 2>/dev/null)
  RTARGET=$($AWS events list-targets-by-rule --rule "$RULE" --query 'Targets[].Arn' --output text 2>/dev/null)
fi
PAT_OK=$(python3 - "${RPATTERN:-}" <<'EOF'
import sys, json
raw = sys.argv[1]
if not raw or raw == "None":
    print("NG|이벤트 패턴 없음 (스케줄 규칙은 인정하지 않음)"); raise SystemExit
try:
    p = json.loads(raw)
except Exception:
    print("NG|이벤트 패턴 파싱 실패"); raise SystemExit
src = p.get("source", [])
dt = " ".join(p.get("detail-type", [])).lower()
# 이미지 스캔 완료 이벤트: ECR 스캔(aws.ecr) 또는 Inspector 강화 스캔(aws.inspector2)
ok = ("aws.inspector2" in src and "scan" in dt) or \
     ("aws.ecr" in src and "scan" in dt)
print("OK|" if ok else "NG|스캔 완료 이벤트 패턴이 아님: source=%s detail-type=%s" % (src, dt))
EOF
)
if [[ "$RSTATE" == "ENABLED" && "${PAT_OK%%|*}" == "OK" && "$RTARGET" == *":function:${FN}"* ]]; then
  pass "I7. EventBridge 규칙 (스캔 완료 이벤트 → 게이트 Lambda, ENABLED)" 5
else
  fail "I7. EventBridge 규칙" "state=${RSTATE:-none} pattern=${PAT_OK#*|} target=${RTARGET:-none}"
fi

echo
echo "── 동작 검증 ──────────────────────────────────────"

# ══ B1. prod 태그가 CLEAN 다이제스트에만 ════════════════
PROD_DIGEST=""
if [[ -n "$REPO" && "$REPO" != "None" ]]; then
  PROD_DIGEST=$($AWS ecr describe-images --repository-name "$REPO" --image-ids imageTag=prod \
    --query 'imageDetails[0].imageDigest' --output text 2>/dev/null)
fi
if [[ -n "$CLEAN_DIGEST" && "$PROD_DIGEST" == "$CLEAN_DIGEST" ]]; then
  pass "B1. prod 태그가 CRITICAL 0건 이미지에 부여됨" 6
else
  fail "B1. prod 태그 부여" "prod 태그 대상이 통과 이미지가 아님"
fi

# ══ B2. 취약 이미지에는 prod 태그 없음 ══════════════════
if [[ -n "$VULN_DIGEST" && "$PROD_DIGEST" != "$VULN_DIGEST" ]]; then
  pass "B2. CRITICAL 보유 이미지는 승격되지 않음" 5
else
  fail "B2. 취약 이미지 차단" "취약 이미지가 승격되었거나 판정 불가"
fi

# ══ B3. 게이트 로그 — 두 이미지 모두 자동 판정한 근거 ════
LOGGRP="/aws/lambda/${FN}"
PROMOTE_HIT=0; BLOCK_HIT=0
if [[ -n "$FN" && "$FN" != "None" ]]; then
  EVENTS=$($AWS logs filter-log-events --log-group-name "$LOGGRP" \
    --filter-pattern '"decision"' --limit 200 \
    --query 'events[].message' --output text 2>/dev/null)
  if [[ -n "$CLEAN_DIGEST" ]]; then
    echo "$EVENTS" | grep -q "$CLEAN_DIGEST" && echo "$EVENTS" | grep -q "PROMOTE" && PROMOTE_HIT=1
  fi
  if [[ -n "$VULN_DIGEST" ]]; then
    echo "$EVENTS" | grep -q "$VULN_DIGEST" && echo "$EVENTS" | grep -q "BLOCK" && BLOCK_HIT=1
  fi
fi
if [[ "$PROMOTE_HIT" == "1" && "$BLOCK_HIT" == "1" ]]; then
  pass "B3. 게이트가 두 이미지를 자동 판정한 로그 근거 존재" 6
else
  fail "B3. 자동 판정 근거" "승격 판정 로그=${PROMOTE_HIT} 차단 판정 로그=${BLOCK_HIT} (수동 태깅은 인정하지 않음)"
fi

# ══ B4. ECS 서비스가 :prod 를 참조 ══════════════════════
CLUSTER=$($AWS ecs list-clusters --query "clusterArns[?contains(@, 'wsc2026-gate-cluster-')] | [0]" --output text 2>/dev/null)
SVC=""; TDIMG=""; TASK_IP=""
if [[ -n "$CLUSTER" && "$CLUSTER" != "None" ]]; then
  SVC=$($AWS ecs list-services --cluster "$CLUSTER" --query "serviceArns[0]" --output text 2>/dev/null)
fi
if [[ -n "$SVC" && "$SVC" != "None" ]]; then
  TD=$($AWS ecs describe-services --cluster "$CLUSTER" --services "$SVC" \
    --query 'services[0].taskDefinition' --output text 2>/dev/null)
  TDIMG=$($AWS ecs describe-task-definition --task-definition "$TD" \
    --query 'taskDefinition.containerDefinitions[0].image' --output text 2>/dev/null)
fi
if [[ "$TDIMG" == *":prod" ]]; then
  pass "B4. ECS 태스크 정의가 :prod 태그를 참조" 3
else
  fail "B4. :prod 참조" "image=${TDIMG:-none}"
fi

# ══ B5. 실행 중인 태스크 /version → 승격된 빌드 ══════════
if [[ -n "$CLUSTER" && "$CLUSTER" != "None" && -n "$SVC" && "$SVC" != "None" ]]; then
  TARN=$($AWS ecs list-tasks --cluster "$CLUSTER" --service-name "$SVC" --desired-status RUNNING \
    --query 'taskArns[0]' --output text 2>/dev/null)
  if [[ -n "$TARN" && "$TARN" != "None" ]]; then
    ENI=$($AWS ecs describe-tasks --cluster "$CLUSTER" --tasks "$TARN" \
      --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value | [0]" --output text 2>/dev/null)
    TASK_IP=$($AWS ec2 describe-network-interfaces --network-interface-ids "$ENI" \
      --query 'NetworkInterfaces[0].Association.PublicIp' --output text 2>/dev/null)
  fi
fi
BUILD=""
if [[ -n "$TASK_IP" && "$TASK_IP" != "None" ]]; then
  BUILD=$(curl -s --max-time 10 "http://${TASK_IP}:8080/version" 2>/dev/null \
    | python3 -c 'import sys,json;print(json.load(sys.stdin).get("build",""))' 2>/dev/null)
fi
if [[ -n "$BUILD" && "$BUILD" == "$CLEAN_TAG" ]]; then
  pass "B5. 실행 중인 태스크가 승격된 이미지로 서비스 중 (/version → ${BUILD})" 3
else
  fail "B5. 실행 이미지 확인" "build=${BUILD:-응답없음} (기대=${CLEAN_TAG:-판정불가})"
fi

printf "\n  최종 점수: %.1f / %s 점\n" "$(echo "$SCORE10" | awk '{print $1/10}')" "$TOTAL"
