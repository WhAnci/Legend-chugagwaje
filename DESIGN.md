# AWS 추가과제 생성 에이전트 설계

## 1. 목표

Discord에서 멘션만으로 AWS 실습형 추가과제를 생성하고, 다음 산출물을 하나의 결과 묶음으로 반환한다.

- 과제지 PDF
- 배포 파일(필요한 경우: Terraform, CloudFormation, 샘플 코드, 설정 파일 등)
- 채점기준표 PDF
- 채점 스크립트 Shell script
- 생성 결과 설명 및 실행 방법

지원 예시:

```text
@AWS과제봇 EC2와 ALB를 이용한 추가과제 만들어줘
@AWS과제봇 Lambda 서비스를 이용한 추가과제 만들어줘
@AWS과제봇 추가과제 생성해줘
```

주제를 지정하지 않은 경우 에이전트가 서비스, 난이도, 학습목표를 조합하여 주제를 선정한다.

---

## 2. 기본 원칙

1. **과제지와 채점 로직의 일치**: 과제에서 요구하지 않은 항목을 감점하지 않는다.
2. **재현 가능한 채점**: 동일한 제출물과 AWS 상태는 동일한 점수를 반환해야 한다.
3. **최소 권한**: 채점용 AWS 계정과 생성용 AWS 계정을 분리한다.
4. **비용 안전성**: 과제 생성 시 예상 비용과 정리 방법을 반드시 포함한다.
5. **검증 우선**: PDF와 스크립트를 전달하기 전에 자동 검증한다.
6. **비밀정보 금지**: PDF, 배포 파일, 로그에 AWS 키·토큰·비밀번호를 포함하지 않는다.
7. **위험한 작업 제한**: 대규모 리소스 생성, 외부 공격, 데이터 삭제 과제는 기본적으로 금지하거나 별도 승인한다.

---

## 3. 전체 구성

```text
Discord
  |
  v
Discord Bot Gateway
  |
  v
Request Parser / Job Queue
  |
  v
Hermes Agent 또는 OpenClaw
  |  - 과제 설계
  |  - 채점기준 설계
  |  - 파일 생성
  |  - 품질 검토
  v
Artifact Builder
  |  - Markdown -> PDF
  |  - 파일 패키징
  |  - shell script 검사
  v
Validator / Sandbox
  |
  v
Artifact Storage
  |
  v
Discord 응답
  - 과제지.pdf
  - 채점기준표.pdf
  - grading.sh
  - deploy.zip
  - README.md
```

### 3.1 컴포넌트

| 컴포넌트 | 역할 |
|---|---|
| Discord Bot | 멘션 수신, 권한 확인, 진행상황 및 파일 전달 |
| Request Parser | 서비스, 난이도, 형식, 제약조건 추출 |
| Job Queue | 장시간 생성 작업의 비동기 처리 및 재시도 |
| Agent Adapter | Hermes Agent/OpenClaw 호출 인터페이스 통일 |
| Task Planner | 학습목표, 요구사항, 리소스, 평가항목 설계 |
| Artifact Builder | Markdown, YAML, Shell을 PDF/압축파일로 변환 |
| Validator | 형식, 보안, 실행 가능성, 정합성 검사 |
| Artifact Storage | 임시/영구 산출물 저장 및 만료 처리 |
| Audit Logger | 요청자, 생성 버전, 검증결과, 다운로드 기록 저장 |

---

## 4. Discord 인터페이스

### 4.1 자연어 명령

```text
@AWS과제봇 EC2와 ALB를 이용한 추가과제 만들어줘
@AWS과제봇 난이도 중급으로 S3 추가과제 생성해줘
@AWS과제봇 Lambda 주제, 제한시간 90분, Terraform 형식으로 만들어줘
@AWS과제봇 추가과제 생성해줘
```

### 4.2 선택 옵션

자연어에서 추출하거나 명령어 옵션으로 지원한다.

| 옵션 | 예시 | 기본값 |
|---|---|---|
| `service` | `s3`, `lambda`, `ec2+alb` | 자동 선정 |
| `difficulty` | `초급`, `중급`, `고급` | 중급 |
| `iac` | `terraform`, `cloudformation`, `none` | none |
| `duration` | `90분` | 60분 |
| `region` | `ap-northeast-2` | 운영 기본 리전 |
| `language` | `ko` | ko |
| `format` | `pdf+zip` | 전체 산출물 |

### 4.3 처리 흐름

1. 봇이 멘션을 감지한다.
2. 요청자와 채널의 권한을 확인한다.
3. 요청을 파싱하고 누락된 필수 정보는 기본값으로 채운다.
4. 작업 ID를 발급하고 `생성 중` 메시지를 보낸다.
5. 에이전트가 과제 설계 초안을 만든다.
6. 채점기준과 채점 스크립트를 함께 생성한다.
7. PDF 변환, 압축, 정적 검증을 수행한다.
8. 실패 시 최대 정해진 횟수만큼 수정·재검증한다.
9. 성공 시 Discord에 파일과 요약을 첨부한다.
10. 작업 결과와 만료시각을 기록한다.

예상 응답:

```text
✅ AWS 추가과제가 생성되었습니다.
주제: S3 정적 웹사이트와 CloudFront 구성
난이도: 중급 / 제한시간: 60분
검증: 과제-채점기준 정합성 통과, ShellCheck 통과
파일:
- assignment.pdf
- rubric.pdf
- grading.sh
- deployment.zip
- README.md
```

---

## 5. 에이전트 생성 파이프라인

### Phase A. 요청 해석

입력 요청을 다음 구조로 정규화한다.

```json
{
  "service": ["s3"],
  "difficulty": "intermediate",
  "duration_minutes": 60,
  "iac_format": "terraform",
  "region": "ap-northeast-2",
  "language": "ko",
  "output": ["assignment_pdf", "rubric_pdf", "grading_sh", "deployment_zip"]
}
```

### Phase B. 과제 설계

다음 항목을 먼저 내부 객체로 만든다.

- 제목과 배경
- 학습목표
- 사전조건
- 제공 리소스
- 응시자 요구사항
- 제한사항
- 제출물
- 성공조건
- 예상 소요시간
- 비용 및 정리 절차
- 채점 가능한 관찰 포인트

### Phase C. 채점 설계

각 요구사항은 반드시 하나 이상의 채점 항목과 연결한다.

```text
요구사항 R-01 -> 채점항목 C-01 -> grading.sh 검사 G-01
```

채점은 가능하면 다음 우선순위를 사용한다.

1. AWS API를 통한 실제 상태 검사
2. 리소스 태그와 설정값 검사
3. 제출 파일의 정적 검사
4. 명령 실행 결과 검사
5. 수동 검토가 필요한 항목은 별도 표시

### Phase D. 산출물 생성

작업 디렉터리 예시:

```text
job-<id>/
├── source/
│   ├── assignment.md
│   ├── rubric.md
│   ├── grading.sh
│   ├── task-spec.json
│   └── deployment/
├── build/
│   ├── assignment.pdf
│   ├── rubric.pdf
│   └── deployment.zip
├── validation/
│   └── report.json
└── README.md
```

### Phase E. 검증 및 보정

검증 실패 시 에이전트에 오류 목록을 다시 전달하여 수정한다. 검증을 통과하지 못한 결과는 Discord에 제공하지 않는다.

---

## 6. 출력물 명세

### 6.1 과제지 PDF

필수 구성:

1. 과제 제목
2. 배경 및 목표
3. 시나리오
4. 환경 및 사전조건
5. 요구사항 번호 목록
6. 제약조건
7. 제출물 형식
8. 검증 방법
9. 제한시간
10. 비용 주의사항
11. 리소스 정리 방법
12. 힌트 및 참고자료(선택)

과제지에는 정답이나 채점 내부 로직을 직접 노출하지 않는다.

### 6.2 채점기준표 PDF

| ID | 평가 항목 | 배점 | 자동/수동 | 부분점수 | 판정 기준 |
|---|---|---:|---|---|---|
| C-01 | 핵심 리소스 구성 | 30 | 자동 | 가능 | 요구된 리소스와 상태 확인 |
| C-02 | 보안 설정 | 25 | 자동 | 가능 | 공개 범위 및 정책 검사 |
| C-03 | 기능 동작 | 25 | 자동 | 가능 | 엔드포인트/API 응답 검사 |
| C-04 | IaC 또는 제출물 품질 | 10 | 자동 | 가능 | 구문 및 재현성 검사 |
| C-05 | 문서화 | 10 | 수동 | 불가 | 제출 문서 검토 |

총점, 합격 기준, 감점 사유, 검증 실패 시 처리 규칙을 포함한다.

### 6.3 `grading.sh`

기본 요구사항:

- `set -Eeuo pipefail` 사용
- `--help`, `--dry-run` 지원
- 리전과 제출 경로를 인자로 받을 수 있음
- AWS CLI 오류와 권한 오류를 구분
- 각 평가항목별 결과를 JSON 또는 명확한 텍스트로 출력
- 종료 코드 정의
  - `0`: 채점 완료
  - `1`: 응시자 실패 또는 미충족
  - `2`: 채점 환경 오류
  - `3`: 입력/권한 오류
- 비밀정보 출력 금지
- 반복 실행 시 원상태를 변경하지 않는 읽기 전용 검사 우선

권장 실행 형태:

```bash
./grading.sh \
  --region ap-northeast-2 \
  --submission ./submission \
  --output ./result.json
```

### 6.4 배포 파일

필요할 때만 제공하며 다음 중 하나로 구성한다.

- `terraform/`: `main.tf`, `variables.tf`, `outputs.tf`, `README.md`
- `cloudformation/`: 템플릿과 파라미터 예시
- `scripts/`: 초기 환경 설정 또는 테스트용 코드
- `fixtures/`: 안전한 테스트 데이터

배포 파일에는 다음을 포함하지 않는다.

- 장기 AWS Access Key
- 개인 계정 정보
- 운영 데이터
- 삭제되지 않는 고비용 리소스

---

## 7. 검증 체계

### 7.1 정적 검증

- 필수 산출물 존재 여부
- PDF 생성 가능 여부 및 빈 페이지 여부
- Markdown 링크와 파일 경로 유효성
- `shellcheck grading.sh`
- Shell 문법 검사: `bash -n grading.sh`
- JSON/YAML/Terraform 구문 검사
- 비밀정보 패턴 탐지
- 위험 명령(`rm -rf`, 무제한 리소스 생성 등) 탐지

### 7.2 의미 검증

- 과제지의 모든 요구사항이 루브릭에 존재하는지
- 루브릭의 자동 평가 항목이 스크립트에 구현됐는지
- 배점 합계가 100점인지
- 스크립트가 존재하지 않는 리소스나 이름을 검사하지 않는지
- 기본 리전·리소스명·태그 규칙이 모든 산출물에서 일치하는지
- 과제지에 명시되지 않은 필수 조건이 없는지

### 7.3 실행 검증

가능한 경우 격리된 테스트 계정에서 다음을 수행한다.

1. 배포 파일 구문 검증
2. 테스트용 리소스 배포
3. `grading.sh` 정상 제출물 실행
4. 요구사항 하나씩 누락한 제출물 실행
5. 리소스 정리

실행 검증은 비용 한도와 시간 제한을 둔다.

---

## 8. 보안 및 운영

### 계정 분리

- 봇/에이전트 계정: 산출물 생성만 수행
- 검증 계정: 제한된 테스트 리소스만 생성
- 채점 계정: 응시자 환경을 읽을 최소 권한 사용

### 권한

채점 스크립트에는 서비스별 ReadOnly 권한을 기본 적용한다. 쓰기 권한이 필요한 과제는 별도 역할과 명시적 승인 절차를 둔다.

### 저장 및 만료

- 작업별 랜덤 디렉터리 사용
- 파일은 기본 7일 후 삭제
- Discord 메시지에는 임시 다운로드 링크 또는 첨부파일 제공
- 생성 요청과 결과의 해시를 기록하여 재현 가능하게 함

### 비용 보호

- 허용 서비스 목록과 금지 서비스 목록 관리
- 리소스 수량, 인스턴스 유형, 실행시간 제한
- 모든 리소스에 `CreatedBy`, `JobId`, `AutoCleanup` 태그 부여
- 예상 비용과 정리 명령을 과제지에 표시

---

## 9. Hermes Agent / OpenClaw 연동 추상화

특정 에이전트에 종속되지 않도록 내부 인터페이스를 정의한다.

```typescript
interface TaskAgent {
  generateTask(input: TaskRequest): Promise<TaskDraft>;
  reviseTask(draft: TaskDraft, errors: ValidationError[]): Promise<TaskDraft>;
}

interface TaskDraft {
  spec: TaskSpec;
  assignmentMarkdown: string;
  rubricMarkdown: string;
  gradingScript: string;
  deploymentFiles: File[];
}
```

- `HermesAdapter`: Hermes Agent의 API/CLI 호출 담당
- `OpenClawAdapter`: OpenClaw의 API/CLI 호출 담당
- `MockAdapter`: 개발 및 테스트용 고정 결과 반환

에이전트 프롬프트에는 반드시 `task-spec.json` 스키마, 출력 파일 목록, 금지사항, 검증 오류 수정 규칙을 포함한다.

---

## 10. 권장 저장소 구조

```text
aws-task-agent/
├── bot/
│   ├── discord-gateway
│   └── commands
├── agent/
│   ├── adapter.ts
│   ├── hermes.ts
│   ├── openclaw.ts
│   └── prompts/
├── domain/
│   ├── task-spec.schema.json
│   └── rubric.schema.json
├── builder/
│   ├── pdf.ts
│   ├── archive.ts
│   └── shell.ts
├── validator/
│   ├── static.ts
│   ├── semantic.ts
│   └── runtime.ts
├── templates/
│   ├── assignment.md
│   ├── rubric.md
│   └── grading.sh
├── storage/
├── tests/
└── docs/
    └── DESIGN.md
```

---

## 11. 1차 MVP 범위

처음부터 실시간 AWS 배포까지 구현하지 않고 다음 범위로 시작한다.

1. Discord 멘션 수신
2. 주제·난이도·제약조건 파싱
3. Hermes/OpenClaw 중 하나를 통한 초안 생성
4. Markdown을 PDF로 변환
5. `grading.sh`와 선택적 `deployment.zip` 생성
6. ShellCheck, PDF, 배점, 요구사항 정합성 검사
7. Discord에 결과 파일 첨부
8. 실패 시 오류 요약 반환

후속 단계에서 추가한다.

- 테스트 AWS 계정 자동 배포
- 실제 정상/실패 제출물 실행 검증
- 과제 템플릿·서비스별 난이도 카탈로그
- 관리자 승인 워크플로
- 생성 이력 검색 및 재생성
- 비용 추정 및 자동 정리

---

## 12. 주요 실패 시나리오

| 상황 | 처리 |
|---|---|
| 지원하지 않는 AWS 서비스 | 지원 서비스 목록과 함께 재요청 안내 |
| PDF 변환 실패 | 원인 기록 후 재생성, 최종 실패 시 파일 미전달 |
| 채점 스크립트 문법 오류 | 자동 수정 후 재검증 |
| 요구사항-루브릭 불일치 | 에이전트 수정 루프 수행 |
| AWS 권한 부족 | 채점 환경 오류로 분류하고 필요한 권한 표시 |
| 비용 위험 과제 | 관리자 승인 없이는 생성 차단 |
| Discord 첨부 용량 초과 | 압축 또는 만료 링크로 대체 |

---

## 13. 기술 선택안

### 결론

초기에는 **Hermes Agent나 OpenClaw를 직접 도입하지 않고**, Docker 안에서 실행되는 자체 오케스트레이터와 LLM API 어댑터로 시작하는 것을 권장한다.

이 시스템의 핵심은 대화형 에이전트 자체가 아니라 다음의 재현 가능한 산출물 파이프라인이기 때문이다.

```text
Discord 요청 -> 구조화된 TaskSpec -> LLM 초안 생성 -> 템플릿/빌더 -> 검증 -> 파일 전달
```

에이전트 프레임워크를 먼저 선택하면 설치·권한·세션·도구 호출 방식에 시스템이 종속될 수 있다. 대신 나중에 Hermes 또는 OpenClaw를 교체 가능한 어댑터로 추가한다.

### 권장 1차 기술 스택

| 영역 | 선택 |
|---|---|
| 언어 | Python 3.12 |
| Discord | `discord.py` |
| 작업 큐 | MVP는 `asyncio` 백그라운드 작업, 이후 Redis + Celery/RQ |
| LLM 호출 | OpenAI 호환 API 어댑터 또는 LiteLLM |
| 구조화 출력 | Pydantic + JSON Schema |
| PDF | Pandoc + Chromium 또는 WeasyPrint |
| 압축 | Python 표준 라이브러리 |
| Shell 검증 | Bash, ShellCheck |
| AWS SDK | boto3 |
| 격리 실행 | Docker 컨테이너 |
| 저장소 | MVP 로컬 볼륨, 이후 S3 |

### Hermes와 OpenClaw의 도입 시점

- **Hermes**: 해당 프로젝트의 도구 호출, 장기 메모리, 멀티스텝 실행이 실제로 필요할 때 어댑터로 검토한다.
- **OpenClaw**: 이미 운영 중인 OpenClaw 환경이나 커넥터를 재사용해야 할 때 어댑터로 검토한다.
- 둘 중 하나를 선택하더라도 `TaskAgent` 인터페이스 뒤에 숨겨 Discord 봇과 산출물 빌더가 영향을 받지 않게 한다.

현재는 두 제품 모두 설치되어 있지 않으므로, 특정 제품을 기준으로 전체 구조를 고정하지 않는다. 먼저 MockAgent로 파이프라인을 완성한 후 실제 LLM을 연결하는 방식이 안전하다.

### Docker 구성

```text
compose.yaml
├── bot           Discord 이벤트 수신 및 결과 전송
├── worker        과제 생성/검증 작업 실행
├── artifact      PDF·압축파일 빌드 컨테이너(선택)
├── redis         작업 큐(선택, MVP 후 도입)
└── localstack    로컬 AWS 테스트(선택)
```

MVP에서는 `bot`과 `worker`를 하나의 이미지로 운영해도 된다. PDF 변환과 Shell 실행은 별도 작업 디렉터리에서 수행하고, 작업별 CPU·메모리·시간 제한을 적용한다.

### Docker와 AWS 환경의 구분

Docker는 봇과 생성·검증 코드를 격리하지만, 실제 AWS 리소스 자체를 완전히 가상화하지는 않는다. 따라서 다음을 분리한다.

1. **문서/스크립트 검증**: Docker 내부에서 수행
2. **AWS API 검증**: 별도 AWS 샌드박스 계정 또는 엄격히 제한된 역할 사용
3. **서비스 호환성 테스트**: 가능한 서비스만 LocalStack 사용
4. **비용이 발생하는 통합 테스트**: 승인된 계정에서만 수행하고 자동 정리

Docker 컨테이너에 호스트의 `~/.aws` 디렉터리를 마운트하거나 장기 Access Key를 이미지에 넣지 않는다. AWS 자격증명은 실행 시 임시 자격증명 또는 Secrets 관리 기능으로 주입한다.

### 단계별 도입

#### 1단계: 생성 파이프라인

- MockAgent
- 고정 샘플 과제 생성
- PDF/ZIP/`grading.sh` 빌드
- 정적 검증
- Discord 첨부 전송

#### 2단계: 실제 LLM 연결

- OpenAI 호환 API 어댑터
- Pydantic 구조화 출력
- 과제 초안 생성 및 오류 보정 루프
- 모델·공급자별 설정 분리

#### 3단계: AWS 검증

- 제한된 테스트 계정
- 읽기 전용 채점 역할
- 태그 기반 리소스 추적 및 정리
- 정상/실패 fixture 실행

#### 4단계: Hermes/OpenClaw 선택 도입

- 동일한 `TaskAgent` 인터페이스 구현
- 기존 파이프라인과 결과 비교
- 안정성, 비용, 실행시간, 도구 호출 이점을 평가한 뒤 채택

## 14. 완료 기준

MVP는 다음 조건을 모두 만족해야 한다.

- 멘션 요청 한 번으로 작업이 시작된다.
- 주제 지정 및 자동 주제 선정이 모두 동작한다.
- 과제지 PDF와 채점기준표 PDF가 생성된다.
- `grading.sh`가 문법 및 보안 검사를 통과한다.
- 선택적 배포 파일이 압축되어 제공된다.
- 요구사항, 채점기준, 스크립트 간 매핑 검증이 가능하다.
- 생성 실패 시 불완전한 파일을 전달하지 않는다.
- AWS 자격증명과 민감정보가 산출물에 포함되지 않는다.
