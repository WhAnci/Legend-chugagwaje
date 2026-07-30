#!/usr/bin/env bash
# 채점 스크립트 — 이벤트 드리븐 서버리스 파이프라인
# 사용법: bash grade.sh <비번호>
# AWS Profile: lee (~/.aws/credentials 또는 ~/.aws/config)
# 리전: ap-northeast-2

set -uo pipefail

BNUM="${1:-}"
if [[ -z "$BNUM" ]]; then
  echo "사용법: bash grade.sh <비번호>"
  exit 1
fi

PROFILE="default"
REGION="ap-northeast-2"
AWS="aws --profile $PROFILE --region $REGION --output json"

SCORE=0
TOTAL=100

pass() { local pts=$1 msg=$2; SCORE=$((SCORE+pts)); echo "  ✅ [+${pts}pt] $msg"; }
fail() { local msg=$1;                               echo "  ❌ [+0pt]  $msg"; }
sep()  { echo; echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; echo "  $1"; echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"; }

# ──────────────────────────────────────────────────
# 1. DynamoDB (6점)
# ──────────────────────────────────────────────────
sep "1. DynamoDB (6점)"

ORDERS_TABLE="wsc2026-orders"
STATE_TABLE="wsc2026-order-state"

if $AWS dynamodb describe-table --table-name "$ORDERS_TABLE" > /tmp/orders_table.json 2>/dev/null; then
  pass 1 "$ORDERS_TABLE 테이블 존재"

  PK=$(jq -r '.Table.KeySchema[] | select(.KeyType=="HASH") | .AttributeName' /tmp/orders_table.json)
  SK=$(jq -r '.Table.KeySchema[] | select(.KeyType=="RANGE") | .AttributeName' /tmp/orders_table.json)
  [[ "$PK" == "orderId" && "$SK" == "createdAt" ]] && pass 1 "PK=orderId, SK=createdAt" || fail "PK/SK 불일치 (PK=$PK, SK=$SK)"

  GSI_NAME=$(jq -r '.Table.GlobalSecondaryIndexes[]? | select(.IndexName=="status-index") | .IndexName' /tmp/orders_table.json)
  if [[ "$GSI_NAME" == "status-index" ]]; then
    GSI_PK=$(jq -r '.Table.GlobalSecondaryIndexes[] | select(.IndexName=="status-index") | .KeySchema[] | select(.KeyType=="HASH") | .AttributeName' /tmp/orders_table.json)
    GSI_SK=$(jq -r '.Table.GlobalSecondaryIndexes[] | select(.IndexName=="status-index") | .KeySchema[] | select(.KeyType=="RANGE") | .AttributeName' /tmp/orders_table.json)
    [[ "$GSI_PK" == "status" && "$GSI_SK" == "createdAt" ]] && pass 1 "GSI status-index (PK=status, SK=createdAt)" || fail "GSI 키 불일치"
  else
    fail "GSI status-index 없음"
  fi

  BILLING=$(jq -r '.Table.BillingModeSummary.BillingMode // "PROVISIONED"' /tmp/orders_table.json)
  [[ "$BILLING" == "PAY_PER_REQUEST" ]] && pass 1 "On-Demand 과금" || fail "On-Demand 미설정 (현재: $BILLING)"

  PITR=$($AWS dynamodb describe-continuous-backups --table-name "$ORDERS_TABLE" 2>/dev/null \
    | jq -r '.ContinuousBackupsDescription.PointInTimeRecoveryDescription.PointInTimeRecoveryStatus')
  [[ "$PITR" == "ENABLED" ]] && pass 1 "PITR 활성화" || fail "PITR 비활성화"

  DP=$(jq -r '.Table.DeletionProtectionEnabled // false' /tmp/orders_table.json)
  [[ "$DP" == "true" ]] && pass 1 "Deletion Protection 활성화" || fail "Deletion Protection 미설정"
else
  for _ in 1 2 3 4 5 6; do fail "DynamoDB 확인 불가 ($ORDERS_TABLE 없음)"; done
fi

if $AWS dynamodb describe-table --table-name "$STATE_TABLE" > /tmp/state_table.json 2>/dev/null; then
  STATE_PK=$(jq -r '.Table.KeySchema[] | select(.KeyType=="HASH") | .AttributeName' /tmp/state_table.json)
  [[ "$STATE_PK" == "executionId" ]] && pass 0 "$STATE_TABLE 존재 (PK=executionId)" || fail "$STATE_TABLE PK 불일치"
else
  fail "$STATE_TABLE 테이블 없음"
fi

# ──────────────────────────────────────────────────
# 2. S3 (4점)
# ──────────────────────────────────────────────────
sep "2. S3 (4점)"

ARCHIVE_BUCKET="wsc2026-orders-archive-${BNUM}"

if $AWS s3api head-bucket --bucket "$ARCHIVE_BUCKET" > /dev/null 2>&1; then
  pass 1 "$ARCHIVE_BUCKET 버킷 존재"

  PA=$($AWS s3api get-public-access-block --bucket "$ARCHIVE_BUCKET" 2>/dev/null \
    | jq '[.PublicAccessBlockConfiguration | to_entries[].value] | all')
  [[ "$PA" == "true" ]] && pass 1 "퍼블릭 액세스 완전 차단" || fail "퍼블릭 액세스 미차단"

  ENC=$($AWS s3api get-bucket-encryption --bucket "$ARCHIVE_BUCKET" 2>/dev/null \
    | jq -r '.ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm // ""')
  [[ "$ENC" == "aws:kms" ]] && pass 1 "SSE-KMS 암호화" || fail "SSE-KMS 미적용 (현재: ${ENC:-없음})"

  VER=$($AWS s3api get-bucket-versioning --bucket "$ARCHIVE_BUCKET" 2>/dev/null | jq -r '.Status // ""')
  [[ "$VER" == "Enabled" ]] && pass 1 "버전 관리 활성화" || fail "버전 관리 미활성화"
else
  for _ in 1 2 3 4; do fail "S3 확인 불가 ($ARCHIVE_BUCKET 없음)"; done
fi

# ──────────────────────────────────────────────────
# 3. SQS (4점)
# ──────────────────────────────────────────────────
sep "3. SQS (4점)"

QUEUE_NAME="wsc2026-order-queue"
DLQ_NAME="wsc2026-order-dlq"

QUEUE_URL=$($AWS sqs get-queue-url --queue-name "$QUEUE_NAME" 2>/dev/null | jq -r '.QueueUrl // ""')
DLQ_URL=$($AWS sqs get-queue-url --queue-name "$DLQ_NAME" 2>/dev/null | jq -r '.QueueUrl // ""')

if [[ -n "$QUEUE_URL" ]]; then
  pass 1 "$QUEUE_NAME 존재"
  ATTRS=$($AWS sqs get-queue-attributes --queue-url "$QUEUE_URL" --attribute-names All 2>/dev/null)

  VT=$(echo "$ATTRS" | jq -r '.Attributes.VisibilityTimeout // "0"')
  [[ "$VT" == "300" ]] && pass 1 "Visibility Timeout = 300초" || fail "Visibility Timeout 불일치 (현재: ${VT}초)"

  REDRIVE=$(echo "$ATTRS" | jq -r '.Attributes.RedrivePolicy // ""')
  if [[ -n "$REDRIVE" ]]; then
    MAX=$(echo "$REDRIVE" | jq -r '.maxReceiveCount // 0')
    [[ "$MAX" == "3" ]] && pass 1 "DLQ Redrive (maxReceiveCount=3)" || fail "maxReceiveCount 불일치 (현재: $MAX)"
  else
    fail "DLQ Redrive Policy 미설정"
  fi
else
  fail "$QUEUE_NAME 없음"
  fail "SQS 설정 확인 불가"
  fail "DLQ Redrive 확인 불가"
fi

[[ -n "$DLQ_URL" ]] && pass 1 "$DLQ_NAME 존재" || fail "$DLQ_NAME 없음"

# ──────────────────────────────────────────────────
# 4. EventBridge (8점)
# ──────────────────────────────────────────────────
sep "4. EventBridge (8점)"

BUS_NAME="wsc2026-order-bus"

$AWS events describe-event-bus --name "$BUS_NAME" > /tmp/bus.json 2>/dev/null \
  && pass 1 "이벤트 버스 $BUS_NAME 존재" || fail "이벤트 버스 $BUS_NAME 없음"

if $AWS events describe-rule --name "wsc2026-rule-success" --event-bus-name "$BUS_NAME" > /tmp/rule_success.json 2>/dev/null; then
  PATTERN=$(jq -r '.EventPattern' /tmp/rule_success.json)
  echo "$PATTERN" | jq -e '."detail-type" | contains(["OrderProcessed"])' > /dev/null 2>&1 \
    && pass 2 "wsc2026-rule-success (OrderProcessed 패턴)" || fail "wsc2026-rule-success EventPattern 불일치"
else
  fail "wsc2026-rule-success Rule 없음"
fi

if $AWS events describe-rule --name "wsc2026-rule-failure" --event-bus-name "$BUS_NAME" > /tmp/rule_failure.json 2>/dev/null; then
  PATTERN=$(jq -r '.EventPattern' /tmp/rule_failure.json)
  echo "$PATTERN" | jq -e '."detail-type" | contains(["OrderFailed"])' > /dev/null 2>&1 \
    && pass 2 "wsc2026-rule-failure (OrderFailed 패턴)" || fail "wsc2026-rule-failure EventPattern 불일치"

  TARGETS=$($AWS events list-targets-by-rule --rule "wsc2026-rule-failure" --event-bus-name "$BUS_NAME" 2>/dev/null)
  DLQ_ARN=$($AWS sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn 2>/dev/null | jq -r '.Attributes.QueueArn // ""' || echo "")
  echo "$TARGETS" | jq -e --arg arn "$DLQ_ARN" '.Targets[] | select(.Arn==$arn)' > /dev/null 2>&1 \
    && pass 1 "실패 Rule 타겟 = DLQ" || fail "실패 Rule 타겟이 DLQ가 아님"

  echo "$TARGETS" | jq -e '.Targets[] | .InputTransformer' > /dev/null 2>&1 \
    && pass 1 "실패 Rule Input Transformer 설정" || fail "Input Transformer 미설정"
else
  fail "wsc2026-rule-failure Rule 없음"
  fail "실패 Rule 타겟 확인 불가"
  fail "Input Transformer 확인 불가"
fi

if $AWS events describe-archive --archive-name "wsc2026-order-archive" > /tmp/eb_archive.json 2>/dev/null; then
  RETENTION=$(jq -r '.RetentionDays' /tmp/eb_archive.json)
  [[ "$RETENTION" == "7" ]] && pass 1 "이벤트 아카이브 (7일)" || fail "아카이브 보존 기간 불일치 (현재: ${RETENTION}일)"
else
  fail "이벤트 아카이브 wsc2026-order-archive 없음"
fi

# ──────────────────────────────────────────────────
# 5. Step Functions (10점)
# ──────────────────────────────────────────────────
sep "5. Step Functions (10점)"

SM_NAME="wsc2026-order-workflow"
SM_ARN=$($AWS stepfunctions list-state-machines 2>/dev/null \
  | jq -r --arg n "$SM_NAME" '.stateMachines[] | select(.name==$n) | .stateMachineArn')

if [[ -n "$SM_ARN" ]]; then
  pass 1 "상태 머신 $SM_NAME 존재"
  $AWS stepfunctions describe-state-machine --state-machine-arn "$SM_ARN" > /tmp/sm.json 2>/dev/null

  TYPE=$(jq -r '.type' /tmp/sm.json)
  [[ "$TYPE" == "EXPRESS" ]] && pass 1 "Type = EXPRESS" || fail "Type 불일치 (현재: $TYPE)"

  ROLE_ARN=$(jq -r '.roleArn' /tmp/sm.json)
  echo "$ROLE_ARN" | grep -q "wsc2026-sfn-role" && pass 1 "IAM Role wsc2026-sfn-role" || fail "IAM Role 불일치"

  LOG_LEVEL=$(jq -r '.loggingConfiguration.level // "OFF"' /tmp/sm.json)
  [[ "$LOG_LEVEL" != "OFF" ]] && pass 1 "CloudWatch Logs 연동 ($LOG_LEVEL)" || fail "CloudWatch Logs 미연동"

  DEF=$(jq -r '.definition' /tmp/sm.json)

  echo "$DEF" | jq -e '.States | to_entries[] | select(.value.Type=="Choice")' > /dev/null 2>&1 \
    && pass 1 "Choice 상태 존재" || fail "Choice 상태 없음"

  echo "$DEF" | jq -e '.. | .NumericGreaterThanEquals? | select(.==300000)' > /dev/null 2>&1 \
    && pass 1 "price ≥ 300,000 분기 조건" || fail "price 분기 조건 없음"

  echo "$DEF" | grep -q 's3:putObject'      && pass 1 "S3 putObject SDK 통합"       || fail "S3 putObject SDK 통합 없음"
  echo "$DEF" | grep -q 'dynamodb:putItem'  && pass 1 "DynamoDB putItem SDK 통합"   || fail "DynamoDB putItem SDK 통합 없음"
  echo "$DEF" | grep -q 'events:putEvents'  && pass 1 "EventBridge putEvents SDK 통합" || fail "EventBridge putEvents SDK 통합 없음"
  echo "$DEF" | jq -e '.. | .Catch? | select(length>0)' > /dev/null 2>&1 \
    && pass 1 "Catch 블록 존재 (HandleError 분기)" || fail "Catch 블록 없음"
else
  for _ in $(seq 1 10); do fail "Step Functions 확인 불가 ($SM_NAME 없음)"; done
fi

# ──────────────────────────────────────────────────
# 6. API Gateway (4점)
# ──────────────────────────────────────────────────
sep "6. API Gateway (4점)"

API_ID=$($AWS apigateway get-rest-apis 2>/dev/null | jq -r '.items[] | select(.name=="wsc2026-order-api") | .id')

if [[ -n "$API_ID" ]]; then
  pass 1 "REST API wsc2026-order-api 존재"

  RESOURCE_ID=$($AWS apigateway get-resources --rest-api-id "$API_ID" 2>/dev/null \
    | jq -r '.items[] | select(.path=="/orders") | .id')
  if [[ -n "$RESOURCE_ID" ]]; then
    $AWS apigateway get-method --rest-api-id "$API_ID" --resource-id "$RESOURCE_ID" --http-method POST \
      > /tmp/method.json 2>/dev/null && pass 1 "POST /orders 메서드 존재" || fail "POST /orders 메서드 없음"
    INT_TYPE=$(jq -r '.methodIntegration.type // ""' /tmp/method.json 2>/dev/null || echo "")
    [[ "$INT_TYPE" == "AWS" ]] && pass 1 "SQS 직접 통합 (type=AWS)" || fail "직접 통합 미설정 (현재: $INT_TYPE)"
  else
    fail "/orders 리소스 없음"
    fail "통합 확인 불가"
  fi

  STAGE=$($AWS apigateway get-stages --rest-api-id "$API_ID" 2>/dev/null \
    | jq -r '.item[] | select(.stageName=="prod") | .stageName')
  [[ "$STAGE" == "prod" ]] && pass 1 "prod 스테이지 배포" || fail "prod 스테이지 없음"
else
  for _ in 1 2 3 4; do fail "API Gateway 확인 불가 (API 없음)"; done
fi

# ──────────────────────────────────────────────────
# 7. CloudWatch (4점)
# ──────────────────────────────────────────────────
sep "7. CloudWatch (4점)"

if $AWS cloudwatch describe-alarms --alarm-names "wsc2026-dlq-depth-alarm" > /tmp/alarm.json 2>/dev/null \
   && [[ $(jq '.MetricAlarms | length' /tmp/alarm.json) -gt 0 ]]; then
  pass 1 "알람 wsc2026-dlq-depth-alarm 존재"
  METRIC=$(jq -r '.MetricAlarms[0].MetricName' /tmp/alarm.json)
  THRESHOLD=$(jq -r '.MetricAlarms[0].Threshold' /tmp/alarm.json)
  [[ "$METRIC" == "ApproximateNumberOfMessagesVisible" && $(echo "$THRESHOLD >= 1" | bc) -eq 1 ]] \
    && pass 2 "알람 메트릭/임계값 정확 (${METRIC} ≥ ${THRESHOLD})" \
    || fail "알람 메트릭 불일치 (MetricName=$METRIC, Threshold=$THRESHOLD)"
else
  fail "알람 wsc2026-dlq-depth-alarm 없음"
  fail "알람 메트릭 확인 불가"
fi

if $AWS cloudwatch get-dashboard --dashboard-name "wsc2026-order-dashboard" > /tmp/dashboard.json 2>/dev/null; then
  pass 1 "대시보드 wsc2026-order-dashboard 존재"
else
  fail "대시보드 wsc2026-order-dashboard 없음"
fi

# ──────────────────────────────────────────────────
# 8. 동작 테스트 (60점)
# ──────────────────────────────────────────────────
sep "8. 동작 테스트 (60점)"

T_API_ID=$($AWS apigateway get-rest-apis 2>/dev/null | jq -r '.items[] | select(.name=="wsc2026-order-api") | .id')
T_API_KEY_ID=$($AWS apigateway get-api-keys --include-values 2>/dev/null | jq -r '.items[] | select(.name=="wsc2026-order-key") | .value')
T_ENDPOINT="https://${T_API_ID}.execute-api.ap-northeast-2.amazonaws.com/prod/orders"
RESULT_QUEUE_URL=$($AWS sqs get-queue-url --queue-name "wsc2026-order-result" 2>/dev/null | jq -r '.QueueUrl // ""')

if [[ -z "$T_API_ID" || -z "$T_API_KEY_ID" ]]; then
  for _ in $(seq 1 6); do fail "API ID 또는 API Key 조회 실패 — 동작 테스트 스킵"; done
else

  # ── 테스트 1: 일반 주문 (price < 300,000) → DynamoDB 저장 (10점) ──
  echo "  [1/6] 일반 주문 → DynamoDB 저장"
  T1_ID="test-normal-$(date +%s)"
  T1_BODY="{\"orderId\":\"${T1_ID}\",\"product\":\"Keyboard\",\"quantity\":2,\"price\":50000,\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}"

  T1_HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$T_ENDPOINT" \
    -H "x-api-key: $T_API_KEY_ID" -H "Content-Type: application/json" -d "$T1_BODY")

  if [[ "$T1_HTTP" == "200" ]]; then
    pass 2 "일반 주문 API 응답 200"
    echo "  ⏳ Step Functions 실행 대기 (15초)..."
    sleep 15
    T1_COUNT=$($AWS dynamodb query --table-name "wsc2026-orders" \
      --index-name "status-index" \
      --key-condition-expression "#s = :s" \
      --filter-expression "orderId = :oid" \
      --expression-attribute-names '{"#s":"status"}' \
      --expression-attribute-values "{\":s\":{\"S\":\"COMPLETED\"},\":oid\":{\"S\":\"${T1_ID}\"}}" \
      2>/dev/null | jq '.Count // 0')
    [[ "${T1_COUNT:-0}" -gt 0 ]] \
      && pass 8 "일반 주문 DynamoDB 저장 확인 (status=COMPLETED)" \
      || fail "일반 주문 DynamoDB 미저장 — Step Functions 흐름 확인 필요"
  else
    fail "일반 주문 API 응답 실패 (HTTP $T1_HTTP)"
    fail "일반 주문 DynamoDB 저장 확인 불가"
  fi

  # ── 테스트 2: 고액 주문 (price ≥ 300,000) → S3 저장 (10점) ──
  echo "  [2/6] 고액 주문 → S3 저장"
  T2_ID="test-high-$(date +%s)"
  T2_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  T2_BODY="{\"orderId\":\"${T2_ID}\",\"product\":\"Laptop\",\"quantity\":1,\"price\":500000,\"timestamp\":\"${T2_TS}\"}"

  T2_HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$T_ENDPOINT" \
    -H "x-api-key: $T_API_KEY_ID" -H "Content-Type: application/json" -d "$T2_BODY")

  if [[ "$T2_HTTP" == "200" ]]; then
    pass 2 "고액 주문 API 응답 200"
    echo "  ⏳ Step Functions 실행 대기 (15초)..."
    sleep 15
    T2_YEAR=$(echo "$T2_TS" | cut -d'-' -f1)
    T2_MONTH=$(echo "$T2_TS" | cut -d'-' -f2)
    T2_DAY=$(echo "$T2_TS" | cut -d'-' -f3 | cut -dT -f1)
    T2_COUNT=$($AWS s3api list-objects-v2 --bucket "wsc2026-orders-archive-${BNUM}" \
      --prefix "orders/${T2_YEAR}/${T2_MONTH}/${T2_DAY}/" 2>/dev/null \
      | jq --arg id "$T2_ID" '[.Contents[]? | select(.Key | contains($id))] | length')
    [[ "${T2_COUNT:-0}" -gt 0 ]] \
      && pass 8 "고액 주문 S3 저장 확인 (orders/${T2_YEAR}/${T2_MONTH}/${T2_DAY}/)" \
      || fail "고액 주문 S3 미저장 — ArchiveToS3 상태 확인 필요"
  else
    fail "고액 주문 API 응답 실패 (HTTP $T2_HTTP)"
    fail "고액 주문 S3 저장 확인 불가"
  fi

  # ── 테스트 3: OrderProcessed → result 큐 도착 확인 (10점) ──
  echo "  [3/6] OrderProcessed 이벤트 → result 큐 도착"
  if [[ -n "$RESULT_QUEUE_URL" ]]; then
    MSG=$($AWS sqs receive-message --queue-url "$RESULT_QUEUE_URL" \
      --max-number-of-messages 1 --wait-time-seconds 5 2>/dev/null | jq '.Messages | length // 0')
    [[ "${MSG:-0}" -gt 0 ]] \
      && pass 10 "OrderProcessed 이벤트 → result 큐 도착 확인" \
      || fail "result 큐에 메시지 없음 — EventBridge success Rule 또는 Pipe 확인 필요"
  else
    fail "wsc2026-order-result 큐 없음 — result 큐 도착 확인 불가"
  fi

  # ── 테스트 4: API GW Request Validator → 400 거부 (5점) ──
  echo "  [4/6] 필드 누락 → API GW 400 거부"
  T4_BODY="{\"orderId\":\"test-bad-$(date +%s)\",\"product\":\"Mouse\"}"  # price/quantity 누락
  T4_HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$T_ENDPOINT" \
    -H "x-api-key: $T_API_KEY_ID" -H "Content-Type: application/json" -d "$T4_BODY")
  [[ "$T4_HTTP" == "400" ]] \
    && pass 5 "필드 누락 주문 API 400 거부 (Request Validator 동작)" \
    || fail "Request Validator 미동작 (HTTP $T4_HTTP)"

  # ── 테스트 5: timestamp 누락 → HandleError → DLQ 도착 (10점) ──
  echo "  [5/6] timestamp 누락 → HandleError → DLQ 도착"
  # price/quantity/orderId/product 포함, timestamp 누락 → API GW 통과 + SFN ValidateOrder Default → HandleError → OrderFailed → DLQ
  T5_ID="test-fail-$(date +%s)"
  T5_BODY="{\"orderId\":\"${T5_ID}\",\"product\":\"Tablet\",\"quantity\":1,\"price\":100000}"
  T5_HTTP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$T_ENDPOINT" \
    -H "x-api-key: $T_API_KEY_ID" -H "Content-Type: application/json" -d "$T5_BODY")

  if [[ "$T5_HTTP" == "200" ]]; then
    echo "  ⏳ HandleError → DLQ 도착 대기 (최대 90초)..."
    DLQ_DEPTH=0
    for i in 1 2 3; do
      sleep 30
      DLQ_DEPTH=$($AWS sqs get-queue-attributes --queue-url "$DLQ_URL" \
        --attribute-names All 2>/dev/null \
        | jq '(.Attributes.ApproximateNumberOfMessages | tonumber) + (.Attributes.ApproximateNumberOfMessagesNotVisible | tonumber)' 2>/dev/null || echo 0)
      [[ "${DLQ_DEPTH:-0}" -gt 0 ]] && break
      echo "  ⏳ DLQ 메시지 없음 (${i}/3 확인)"
    done
    [[ "${DLQ_DEPTH:-0}" -gt 0 ]] \
      && pass 10 "DLQ 메시지 도착 확인 (OrderFailed → EventBridge → DLQ 경로 정상)" \
      || fail "DLQ 메시지 없음 — HandleError→EventBridge→failure Rule→DLQ 경로 확인 필요"
  else
    fail "timestamp 누락 주문 API 응답 실패 (HTTP $T5_HTTP)"
  fi

  # ── 테스트 6: DLQ 메시지 → CloudWatch 알람 ALARM 전환 (15점) ──
  echo "  [6/6] DLQ 메시지 → CloudWatch 알람 ALARM 전환"
  echo "  ⏳ CloudWatch 알람 평가 대기 (최대 90초)..."
  ALARM_STATE=""
  for i in 1 2 3; do
    sleep 30
    ALARM_STATE=$($AWS cloudwatch describe-alarms --alarm-names "wsc2026-dlq-depth-alarm" 2>/dev/null \
      | jq -r '.MetricAlarms[0].StateValue // "UNKNOWN"')
    [[ "$ALARM_STATE" == "ALARM" ]] && break
    echo "  ⏳ 알람 상태: $ALARM_STATE (${i}/3 확인)"
  done
  [[ "$ALARM_STATE" == "ALARM" ]] \
    && pass 15 "CloudWatch 알람 ALARM 전환 확인 (DLQ 메시지 감지)" \
    || fail "알람 ALARM 전환 안됨 (현재 상태: ${ALARM_STATE}) — 알람 메트릭/임계값 확인 필요"

fi

# ──────────────────────────────────────────────────
# 최종 결과 출력
# ──────────────────────────────────────────────────
echo
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  총점: ${SCORE} / ${TOTAL} 점"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
