# 채점기준표 — 이벤트 드리븐 서버리스 파이프라인

> 총점 100점 / 채점 리전: ap-northeast-2 (서울)

---

## 1. DynamoDB (15점)

| 항목 | 배점 | 채점 기준 |
|---|---|---|
| `wsc2026-orders` 테이블 존재 | 2 | 테이블 이름 정확 일치 |
| PK: `orderId` (S) / SK: `createdAt` (S) | 2 | KeySchema 확인 |
| GSI `status-index` 존재 (PK: `status`, SK: `createdAt`) | 3 | GSI 이름 및 키 확인 |
| On-Demand 과금 방식 | 2 | BillingMode = PAY_PER_REQUEST |
| Point-in-Time Recovery 활성화 | 2 | PITR enabled |
| Deletion Protection 활성화 | 2 | DeletionProtectionEnabled = true |
| `wsc2026-order-state` 테이블 존재 (PK: `executionId`) | 2 | 테이블 이름 및 키 확인 |

---

## 2. S3 (10점)

| 항목 | 배점 | 채점 기준 |
|---|---|---|
| `wsc2026-orders-archive-<비번호>` 버킷 존재 | 2 | 접두사 일치 확인 |
| 퍼블릭 액세스 완전 차단 | 2 | 4개 항목 모두 true |
| SSE-KMS 암호화 적용 | 2 | ServerSideEncryptionConfiguration 확인 |
| 버전 관리 활성화 | 2 | VersioningConfiguration = Enabled |
| 수명 주기 규칙 (30일 → Intelligent-Tiering) | 2 | Transition 규칙 확인 |

---

## 3. SQS (10점)

| 항목 | 배점 | 채점 기준 |
|---|---|---|
| `wsc2026-order-queue` 존재 | 2 | 큐 이름 정확 일치 |
| `wsc2026-order-dlq` 존재 | 2 | DLQ 이름 정확 일치 |
| DLQ Redrive Policy 연결 (maxReceiveCount=3) | 3 | RedrivePolicy 확인 |
| Visibility Timeout = 300초 | 1 | VisibilityTimeout 확인 |
| SSE-SQS 암호화 적용 | 2 | SqsManagedSseEnabled = true |

---

## 4. EventBridge (20점)

| 항목 | 배점 | 채점 기준 |
|---|---|---|
| 커스텀 이벤트 버스 `wsc2026-order-bus` 존재 | 2 | 이벤트 버스 이름 확인 |
| Rule `wsc2026-rule-success` 존재 및 패턴 (`OrderProcessed`) | 3 | EventPattern detail-type 확인 |
| Rule `wsc2026-rule-failure` 존재 및 패턴 (`OrderFailed`) | 3 | EventPattern detail-type 확인 |
| 실패 Rule 타겟이 SQS DLQ | 2 | Arn 일치 확인 |
| 실패 Rule Input Transformer 설정 | 2 | InputTransformer 존재 확인 |
| 이벤트 아카이브 `wsc2026-order-archive` 존재 (7일) | 2 | RetentionDays = 7 |
| Pipe `wsc2026-order-pipe` 존재 | 3 | 소스: SQS, 타겟: Step Functions 확인 |
| Pipe IAM Role `wsc2026-pipe-role` 연결 | 3 | Role ARN 확인 |

---

## 5. Step Functions (25점)

| 항목 | 배점 | 채점 기준 |
|---|---|---|
| 상태 머신 `wsc2026-order-workflow` 존재 | 2 | 이름 정확 일치 |
| Type = EXPRESS | 2 | type 확인 |
| IAM Role `wsc2026-sfn-role` 연결 | 2 | roleArn 확인 |
| CloudWatch Logs 연동 (ALL 레벨) | 2 | loggingConfiguration 확인 |
| ValidateOrder Choice 상태 존재 | 3 | States 내 Choice 타입 확인 |
| CheckPrice Choice 상태 (300,000 분기) | 3 | 분기 조건값 확인 |
| ArchiveToS3 SDK 통합 (`aws-sdk:s3:putObject`) | 3 | Resource ARN 확인 |
| SaveToDynamoDB SDK 통합 (`dynamodb:putItem`) | 3 | Resource ARN 확인 |
| NotifySuccess EventBridge 통합 (`events:putEvents`) | 3 | Resource ARN 및 EventBusName 확인 |
| HandleError EventBridge 통합 + Catch 블록 존재 | 2 | Catch 배열 및 HandleError 상태 확인 |

---

## 6. API Gateway (10점)

| 항목 | 배점 | 채점 기준 |
|---|---|---|
| REST API `wsc2026-order-api` 존재 | 2 | API 이름 확인 |
| `POST /orders` 메서드 존재 | 2 | 리소스 경로 및 메서드 확인 |
| SQS 직접 통합 (non-proxy) | 2 | integration type = AWS 확인 |
| API Key + Usage Plan 설정 | 2 | UsagePlan 및 ApiKey 연결 확인 |
| `prod` 스테이지 배포 | 2 | Stage 이름 확인 |

---

## 7. CloudWatch (10점)

| 항목 | 배점 | 채점 기준 |
|---|---|---|
| 알람 `wsc2026-dlq-depth-alarm` 존재 | 3 | 알람 이름 확인 |
| 알람 메트릭: DLQ ApproximateNumberOfMessagesVisible ≥ 1 | 3 | MetricName 및 Threshold 확인 |
| 대시보드 `wsc2026-order-dashboard` 존재 | 2 | 대시보드 이름 확인 |
| 대시보드 위젯 4개 이상 존재 | 2 | DashboardBody 파싱 확인 |
