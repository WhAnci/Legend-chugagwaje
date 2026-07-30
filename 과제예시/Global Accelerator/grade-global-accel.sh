#!/usr/bin/env bash
# grade-global-accel.sh — 제6과제 글로벌 저지연 매치메이킹 네트워크 채점 (5점, 19항목)
# 사용법: bash grade-global-accel.sh
#         PROFILE=lee bash grade-global-accel.sh   (로컬 검증용)
#
# 리전을 하드코딩하지 않는다. Global Accelerator 의 엔드포인트 그룹에서 두 리전을 읽어와
# 그 리전들의 VPC / EC2 / NLB 를 검사한다.
set -uo pipefail

PROFILE="${PROFILE:-}"
PF=""; [[ -n "$PROFILE" ]] && PF="--profile $PROFILE"

# Global Accelerator 는 글로벌 서비스이며 API 엔드포인트는 us-west-2 에 있다.
GA="aws $PF --region us-west-2"

# 총점 5점. 항목 배점은 0.1점 단위이므로 내부적으로는 10배 정수로 누적한다.
SCORE10=0; TOTAL="5.0"
pass() { printf "✅  [+%.1fpt] %s\n" "$(echo "$2" | awk '{print $1/10}')" "$1"; SCORE10=$((SCORE10 + $2)); }
fail() { echo "❌  [ +0.0pt] $1 — $2"; }

echo "── 제6과제 채점 시작 ─────────────────────────────"
echo

##############################################
# Global Accelerator — 여기서 대상 리전을 알아낸다
##############################################

# 이름이 맞는 accelerator 가 여러 개일 수 있으므로, 엔드포인트 그룹이 구성된 것을 고른다.
# ACCEL 환경변수로 특정 accelerator 이름을 지정할 수도 있다.
ACCEL="${ACCEL:-}"
FILTER="starts_with(Name, 'wsc2026-ga-')"
[[ -n "$ACCEL" ]] && FILTER="Name=='${ACCEL}'"

ACC_ARN=""
for A in $($GA globalaccelerator list-accelerators \
             --query "Accelerators[?${FILTER}].AcceleratorArn" --output text 2>/dev/null); do
  L=$($GA globalaccelerator list-listeners --accelerator-arn "$A" \
        --query 'Listeners[0].ListenerArn' --output text 2>/dev/null)
  [[ -z "$L" || "$L" == "None" ]] && continue
  NEG=$($GA globalaccelerator list-endpoint-groups --listener-arn "$L" \
          --query 'length(EndpointGroups)' --output text 2>/dev/null)
  if [[ "${NEG:-0}" -ge 1 ]]; then ACC_ARN="$A"; break; fi
done

ACC_ENABLED=""; STATIC_IPS=""; NIP=0
if [[ -n "$ACC_ARN" && "$ACC_ARN" != "None" ]]; then
  ACC_ENABLED=$($GA globalaccelerator describe-accelerator --accelerator-arn "$ACC_ARN" \
    --query 'Accelerator.Enabled' --output text 2>/dev/null)
  STATIC_IPS=$($GA globalaccelerator describe-accelerator --accelerator-arn "$ACC_ARN" \
    --query 'Accelerator.IpSets[0].IpAddresses' --output text 2>/dev/null)
  NIP=$(echo "$STATIC_IPS" | tr '\t' '\n' | grep -c '[0-9]')
fi

LARN=""; EG_JSON=""; REGIONS=""
if [[ -n "$ACC_ARN" && "$ACC_ARN" != "None" ]]; then
  LARN=$($GA globalaccelerator list-listeners --accelerator-arn "$ACC_ARN" \
    --query 'Listeners[0].ListenerArn' --output text 2>/dev/null)
  if [[ -n "$LARN" && "$LARN" != "None" ]]; then
    EG_JSON=$($GA globalaccelerator list-endpoint-groups --listener-arn "$LARN" --output json 2>/dev/null)
    REGIONS=$(echo "$EG_JSON" | python3 -c \
      'import sys,json;print(" ".join(g["EndpointGroupRegion"] for g in json.load(sys.stdin).get("EndpointGroups",[])))' 2>/dev/null)
  fi
fi

if [[ -z "$REGIONS" ]]; then
  echo "  ⚠️  Global Accelerator(wsc2026-ga-*) 를 찾을 수 없어 대상 리전을 특정할 수 없습니다."
  printf "\n  최종 점수: 0.0 / %s 점\n" "$TOTAL"
  exit 0
fi
echo "  대상 리전: $REGIONS"
echo

##############################################
# 리전별 인프라 — GA 에서 읽은 리전을 그대로 검사
##############################################

NLB_ARNS=""   # "region=arn" 목록
NODE_IDS=""   # "region=i-..." 목록

lookup() {   # $1=목록  $2=리전  → 값
  echo "$1" | tr ' ' '\n' | grep "^$2=" | head -1 | cut -d= -f2-
}

IDX=1
for R in $REGIONS; do
  CLI="aws $PF --region $R"
  echo "── [$R]"

  # VPC — CIDR 은 리전마다 다를 수 있으므로 이름 규칙으로 찾는다.
  VID=$($CLI ec2 describe-vpcs --filters "Name=tag:Name,Values=wsc2026-ga-vpc-*" \
        --query 'Vpcs[0].VpcId' --output text 2>/dev/null)
  CIDR=$($CLI ec2 describe-vpcs --vpc-ids "${VID:-none}" \
        --query 'Vpcs[0].CidrBlock' --output text 2>/dev/null)
  if [[ -n "$VID" && "$VID" != "None" ]]; then
    pass "I${IDX}. [$R] VPC ($CIDR)" 2
  else
    fail "I${IDX}. [$R] VPC" "wsc2026-ga-vpc-* 이름의 VPC 없음"
  fi
  IDX=$((IDX + 1))

  # 퍼블릭 서브넷 2AZ — IGW 직행 라우트를 가진 서브넷이 서로 다른 2개 AZ 에 있는지
  NAZ=0
  if [[ -n "$VID" && "$VID" != "None" ]]; then
    SUBS=$($CLI ec2 describe-route-tables --filters "Name=vpc-id,Values=$VID" \
      --query 'RouteTables[?Routes[?GatewayId!=`null` && starts_with(GatewayId, `igw-`) && DestinationCidrBlock==`0.0.0.0/0`]].Associations[].SubnetId' \
      --output text 2>/dev/null | tr '\t' '\n' | grep '^subnet-')
    if [[ -n "$SUBS" ]]; then
      NAZ=$($CLI ec2 describe-subnets --subnet-ids $SUBS \
        --query 'Subnets[].AvailabilityZone' --output text 2>/dev/null \
        | tr '\t' '\n' | sort -u | grep -c .)
    fi
  fi
  if [[ "${NAZ:-0}" -ge 2 ]]; then
    pass "I${IDX}. [$R] 퍼블릭 서브넷 2개 AZ (IGW 라우트)" 2
  else
    fail "I${IDX}. [$R] 퍼블릭 서브넷 2AZ" "퍼블릭 AZ 수=${NAZ:-0}"
  fi
  IDX=$((IDX + 1))

  # 매치메이킹 노드 EC2
  IID=$($CLI ec2 describe-instances \
        --filters "Name=tag:Name,Values=wsc2026-ga-node-*" "Name=instance-state-name,Values=running" \
        --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null)
  NODE_IDS="$NODE_IDS $R=${IID}"
  if [[ -n "$IID" && "$IID" != "None" ]]; then
    pass "I${IDX}. [$R] 매치메이킹 노드 EC2 running" 2
  else
    fail "I${IDX}. [$R] 노드 EC2" "running 인스턴스 없음"
  fi
  IDX=$((IDX + 1))

  # 노드 SG 의 8080 이 인터넷에 열려있지 않은지
  OPEN="unknown"
  if [[ -n "$IID" && "$IID" != "None" ]]; then
    OPEN="closed"
    SGS=$($CLI ec2 describe-instances --instance-ids "$IID" \
          --query 'Reservations[0].Instances[0].SecurityGroups[].GroupId' --output text 2>/dev/null)
    for SG in $SGS; do
      HIT=$($CLI ec2 describe-security-groups --group-ids "$SG" \
        --query 'SecurityGroups[0].IpPermissions[?FromPort<=`8080` && ToPort>=`8080`].IpRanges[?CidrIp==`0.0.0.0/0`].CidrIp' \
        --output text 2>/dev/null)
      [[ -n "$HIT" && "$HIT" != "None" ]] && OPEN="open"
    done
  fi
  if [[ "$OPEN" == "closed" ]]; then
    pass "I${IDX}. [$R] 노드 SG 8080 이 인터넷에 열려있지 않음" 2
  else
    fail "I${IDX}. [$R] 노드 SG 8080" "상태=$OPEN"
  fi
  IDX=$((IDX + 1))

  # NLB internet-facing + TCP:8080 리스너
  ARN=$($CLI elbv2 describe-load-balancers \
        --query "LoadBalancers[?starts_with(LoadBalancerName, 'wsc2026-ga-nlb-') && Type=='network'].LoadBalancerArn | [0]" \
        --output text 2>/dev/null)
  NLB_ARNS="$NLB_ARNS $R=${ARN}"
  SCHEME=""; LPORT=""; LPROTO=""
  if [[ -n "$ARN" && "$ARN" != "None" ]]; then
    SCHEME=$($CLI elbv2 describe-load-balancers --load-balancer-arns "$ARN" \
             --query 'LoadBalancers[0].Scheme' --output text 2>/dev/null)
    LPORT=$($CLI elbv2 describe-listeners --load-balancer-arn "$ARN" \
            --query 'Listeners[0].Port' --output text 2>/dev/null)
    LPROTO=$($CLI elbv2 describe-listeners --load-balancer-arn "$ARN" \
             --query 'Listeners[0].Protocol' --output text 2>/dev/null)
  fi
  if [[ "$SCHEME" == "internet-facing" && "$LPORT" == "8080" && "$LPROTO" == "TCP" ]]; then
    pass "I${IDX}. [$R] NLB internet-facing + TCP:8080 리스너" 2
  else
    fail "I${IDX}. [$R] NLB" "scheme=${SCHEME:-none} listener=${LPROTO:-none}:${LPORT:-none}"
  fi
  IDX=$((IDX + 1))

  # 대상그룹 — HTTP /health, client IP 보존 비활성, healthy 대상 존재
  HCPROTO=""; HCPATH=""; PRESERVE=""; HEALTHY=0
  if [[ -n "$ARN" && "$ARN" != "None" ]]; then
    TG=$($CLI elbv2 describe-target-groups --load-balancer-arn "$ARN" \
         --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null)
    if [[ -n "$TG" && "$TG" != "None" ]]; then
      HCPROTO=$($CLI elbv2 describe-target-groups --target-group-arns "$TG" \
                --query 'TargetGroups[0].HealthCheckProtocol' --output text 2>/dev/null)
      HCPATH=$($CLI elbv2 describe-target-groups --target-group-arns "$TG" \
               --query 'TargetGroups[0].HealthCheckPath' --output text 2>/dev/null)
      PRESERVE=$($CLI elbv2 describe-target-group-attributes --target-group-arn "$TG" \
                 --query "Attributes[?Key=='preserve_client_ip.enabled'].Value | [0]" --output text 2>/dev/null)
      HEALTHY=$($CLI elbv2 describe-target-health --target-group-arn "$TG" \
                --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text 2>/dev/null)
    fi
  fi
  if [[ "$HCPROTO" == "HTTP" && "$HCPATH" == "/health" && "$PRESERVE" == "false" && "${HEALTHY:-0}" -ge 1 ]]; then
    pass "I${IDX}. [$R] 대상그룹 HTTP /health + client IP 보존 비활성 + healthy" 3
  else
    fail "I${IDX}. [$R] 대상그룹" "hc=${HCPROTO:-none}${HCPATH:-} preserve=${PRESERVE:-none} healthy=${HEALTHY:-0}"
  fi
  IDX=$((IDX + 1))
  echo
done

##############################################
# Global Accelerator 구성
##############################################

if [[ ( "$ACC_ENABLED" == "True" || "$ACC_ENABLED" == "true" ) && "${NIP:-0}" -ge 2 ]]; then
  pass "I${IDX}. Global Accelerator 활성 + 고정 IP 2개" 3
else
  fail "I${IDX}. Global Accelerator" "enabled=${ACC_ENABLED:-none} ip수=${NIP:-0}"
fi
IDX=$((IDX + 1))

LPROTO_GA=""; LPORT_GA=""
if [[ -n "$LARN" && "$LARN" != "None" ]]; then
  LPROTO_GA=$($GA globalaccelerator describe-listener --listener-arn "$LARN" \
              --query 'Listener.Protocol' --output text 2>/dev/null)
  LPORT_GA=$($GA globalaccelerator describe-listener --listener-arn "$LARN" \
             --query 'Listener.PortRanges[0].FromPort' --output text 2>/dev/null)
fi
if [[ "$LPROTO_GA" == "TCP" && "$LPORT_GA" == "8080" ]]; then
  pass "I${IDX}. GA 리스너 TCP:8080" 2
else
  fail "I${IDX}. GA 리스너" "proto=${LPROTO_GA:-none} port=${LPORT_GA:-none}"
fi
IDX=$((IDX + 1))

# 엔드포인트 그룹 2개, 각 그룹이 자기 리전 NLB 를 트래픽 다이얼 100 으로 물고 있는지
EG_RES=$(python3 - "$EG_JSON" "$NLB_ARNS" <<'EOF'
import sys, json
data = json.loads(sys.argv[1] or "{}")
pairs = dict(p.split("=", 1) for p in sys.argv[2].split() if "=" in p)
groups = data.get("EndpointGroups", [])
if len(groups) < 2:
    print("NG|엔드포인트 그룹이 2개 미만"); raise SystemExit
for g in groups:
    region = g["EndpointGroupRegion"]
    dial = float(g.get("TrafficDialPercentage", 0))
    if dial != 100.0:
        print(f"NG|{region} 트래픽 다이얼={dial}"); raise SystemExit
    ids = [d.get("EndpointId", "") for d in g.get("EndpointDescriptions", [])]
    nlb = pairs.get(region, "")
    if not nlb or nlb == "None" or nlb not in ids:
        print(f"NG|{region} 엔드포인트에 해당 리전 NLB 가 없음"); raise SystemExit
print("OK|")
EOF
)
if [[ "${EG_RES%%|*}" == "OK" ]]; then
  pass "I${IDX}. Endpoint Group 2개 (각 리전 NLB, 트래픽 다이얼 100)" 4
else
  fail "I${IDX}. Endpoint Group" "${EG_RES#*|}"
fi

echo
echo "── 동작 검증 ──────────────────────────────────────"

GA_IP=$(echo "$STATIC_IPS" | tr '\t' '\n' | grep -m1 '[0-9]')
if [[ -z "${GA_IP:-}" ]]; then
  fail "B1~B4. 동작 검증" "고정 IP 를 찾을 수 없어 동작 검증 불가"
  printf "\n  최종 점수: %.1f / %s 점\n" "$(echo "$SCORE10" | awk '{print $1/10}')" "$TOTAL"
  exit 0
fi
echo "  고정 IP: $GA_IP"

whoami_region() {
  curl -s --max-time 8 "http://${GA_IP}:8080/whoami" 2>/dev/null \
    | python3 -c 'import sys,json;print(json.load(sys.stdin).get("region",""))' 2>/dev/null
}

# B1. 평시 라우팅 — 엔드포인트 그룹 리전 중 하나(가까운 쪽)가 응답해야 한다.
R1=$(whoami_region)
if [[ -n "$R1" ]] && echo "$REGIONS" | tr ' ' '\n' | grep -qx "$R1"; then
  pass "B1. 고정 IP → 매치메이킹 노드 응답 (라우팅된 리전: $R1)" 5
else
  fail "B1. 평시 라우팅" "응답 리전=${R1:-없음}"
fi

# B2. /match 라운드트립
MC=$(curl -s --max-time 8 "http://${GA_IP}:8080/match" 2>/dev/null \
     | python3 -c 'import sys,json;print(json.load(sys.stdin).get("matched",""))' 2>/dev/null)
if [[ "$MC" == "True" || "$MC" == "true" ]]; then
  pass "B2. /match 정상 응답" 2
else
  fail "B2. /match" "matched=${MC:-없음}"
fi

# B3+B4. 페일오버 — 지금 응답 중인 리전의 노드를 드레인하고 나머지 리전으로 넘어가는지 본다.
#        드레인 후엔 NLB 가 그 대상으로 라우팅하지 않으므로 SSM 으로 인스턴스에서 직접 호출한다.
if [[ -z "$R1" ]]; then
  fail "B3. 리전 페일오버" "평시 응답 리전을 알 수 없어 장애 주입 불가"
else
  OTHER=$(echo "$REGIONS" | tr ' ' '\n' | grep -vx "$R1" | head -1)
  NODE=$(lookup "$NODE_IDS" "$R1")

  node_curl() {   # $1 = /drain | /restore
    aws $PF --region "$R1" ssm send-command \
      --instance-ids "$NODE" \
      --document-name "AWS-RunShellScript" \
      --parameters "commands=[\"curl -s --max-time 5 http://127.0.0.1:8080$1\"]" \
      --query 'Command.CommandId' --output text 2>/dev/null
  }

  if [[ -z "$NODE" || "$NODE" == "None" ]]; then
    fail "B3. 리전 페일오버" "$R1 노드를 찾을 수 없어 장애 주입 불가"
  else
    echo "  $R1 노드 드레인 중... (SSM → 127.0.0.1:8080/drain)"
    CMD=$(node_curl "/drain")
    if [[ -z "$CMD" || "$CMD" == "None" ]]; then
      fail "B3. 리전 페일오버" "SSM 명령 전송 실패 (노드에 SSM 접속 불가)"
    else
      sleep 5
      FAILOVER=""; ELAPSED=0
      for _ in $(seq 1 36); do          # 5초 × 36 = 180초
        sleep 5; ELAPSED=$((ELAPSED + 5))
        R=$(whoami_region)
        if [[ "$R" == "$OTHER" ]]; then FAILOVER="$R"; break; fi
        printf "\r  전환 대기 %ds (현재 응답: %s)   " "$ELAPSED" "${R:-무응답}"
      done
      echo

      if [[ -n "$FAILOVER" ]]; then
        pass "B3. 리전 장애 시 같은 고정 IP 로 $OTHER 전환 (${ELAPSED}초)" 5
      else
        fail "B3. 리전 페일오버" "180초 내 $OTHER 로 전환되지 않음"
      fi

      # B4. 전환 후에도 동일 고정 IP 로 서비스가 이어지는지
      CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://${GA_IP}:8080/health" 2>/dev/null)
      if [[ "$CODE" == "200" ]]; then
        pass "B4. 전환 후에도 동일 고정 IP 로 서비스 지속" 3
      else
        fail "B4. IP 불변" "code=${CODE:-없음}"
      fi

      echo "  $R1 노드 복구 중..."
      node_curl "/restore" >/dev/null
    fi
  fi
fi

printf "\n  최종 점수: %.1f / %s 점\n" "$(echo "$SCORE10" | awk '{print $1/10}')" "$TOTAL"
