# AWS 추가과제 생성 봇 (MVP)

Discord 멘션을 받아 Gemini API로 AWS 과제 산출물을 생성하는 Docker 기반 MVP입니다.

## 생성 파일

- `assignment.pdf`: 표지 포함 최대 7쪽 과제지
- `rubric.pdf`: 최대 4쪽, 최대 7개 평가항목 채점기준표
- `grading.sh`: CloudShell에서 실행하는 읽기 중심 채점 스크립트
- `deployment.zip`: Terraform/CloudFormation 등 필요 시 포함
- `README.md`: 실행 및 정리 안내

## 실행

Gemini는 요구사항 분석을 담당하고, OpenCode Go DeepSeek V4 Flash는 실제 과제 산출물 제작을 담당합니다. API 키를 넣으면 OpenCode Go API를 직접 호출하므로 별도 서버가 필요 없습니다.

```env
AGENT_BACKEND=opencode
OPENCODE_API_KEY=sk-여기에_실제_OpenCode_API_키
OPENCODE_MODEL=opencode-go/deepseek-v4-flash
OPENCODE_RETRIES=3
OPENCODE_FALLBACK_GEMINI=true
```

API 키를 사용하지 않는 경우에만 OpenCode 서버 방식을 사용합니다.

```bash
opencode serve --hostname 0.0.0.0 --port 4096
```

```env
OPENCODE_URL=http://host.docker.internal:4096
OPENCODE_DIRECTORY=
```

OpenCode 서버 방식은 외부에 직접 공개하지 말고 방화벽으로 제한합니다.


```bash
cp .env.example .env
# GEMINI_API_KEY, DISCORD_TOKEN 입력
# 기본 모델: gemini-3.1-flash-lite-preview
# DISCORD_APPLICATION_ID는 선택

docker compose up --build
```

봇을 초대한 뒤 슬래시 명령어를 사용합니다.

```text
/추가과제
/추가과제 requirements:S3와 Lambda를 이용한 주문 처리 과제
/추가과제 requirements:Route 53 장애조치 웹 서비스
```

`/추가과제`처럼 요구사항을 비워두면 AI가 주제 3개를 제시하고, 버튼으로 선택할 수 있습니다. `DISCORD_GUILD_ID`를 설정하면 테스트 서버에 명령어가 즉시 등록됩니다.

## 개발 실행

```bash
python -m venv .venv
.venv/Scripts/activate  # Windows
pip install -r requirements.txt
python -m app
```

## 주의

- `.env`와 AWS 자격증명을 이미지나 결과물에 넣지 않습니다.
- 현재 버전은 실제 AWS 배포 검증을 하지 않고 산출물 정적 검증만 합니다.
- 채점 스크립트는 CloudShell에서 응시자의 현재 IAM Role 권한으로 실행하도록 생성됩니다. Access Key를 요구하지 않습니다.
- 생성 규칙은 `과제제작가이드.txt`를 기준으로 합니다.
