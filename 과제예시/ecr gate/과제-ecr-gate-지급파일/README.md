# 지급파일 — shopd (주문 API)

| 파일 | 용도 |
|---|---|
| `shopd` | 주문 API 바이너리 (linux/amd64, static). 빌드·수정 불필요 |
| `Dockerfile.v1` | shopd v1 이미지 빌드 정의 |
| `Dockerfile.v2` | shopd v2 이미지 빌드 정의 |
| `build-push.sh` | 두 이미지를 ECR 로 빌드·푸시하는 스크립트 |
| `gate/gate.py` | 승격 게이트 Lambda 코드 (수정 불필요, 그대로 배포) |
| `app-src/` | 참고용 소스. 빌드할 필요 없음 |

## 게이트 Lambda (`gate/gate.py`)

스캔 완료 이벤트를 받아 해당 이미지의 스캔 결과를 조회하고, **CRITICAL 취약점이 0건이면
같은 다이제스트에 승격 태그를 부여**한다. 1건이라도 있으면 태그를 부여하지 않는다.
판정 결과(`decision`, `severity`, `digest`)는 로그로 남긴다.

| 항목 | 값 |
|---|---|
| 런타임 | `python3.12` |
| 핸들러 | `gate.handler` |
| 환경변수 `REPO` | 대상 ECR 리포지토리 이름 (필수) |
| 환경변수 `PROMOTE_TAG` | 승격 태그 (기본 `prod`) |

패키징:

```bash
cd gate && zip -r ../gate.zip gate.py
```

코드는 수정하지 않는다. 이벤트 배선·권한·환경변수는 선수가 구성한다.

## 애플리케이션 명세

컨테이너 포트 **8080** (환경변수 `PORT` 로 변경 가능).

| 엔드포인트 | 응답 |
|---|---|
| `GET /health` | `200 ok` — 로드밸런서 헬스체크 대상 |
| `GET /version` | `{"app":"shopd","build":"<v1\|v2>"}` — 실행 중인 이미지 식별 |
| `GET /orders` | 주문 목록 JSON (`build` 필드 포함) |

`BUILD` 환경변수는 각 Dockerfile 에 이미 박혀 있다(v1 → `v1`, v2 → `v2`).
따라서 `/version` 의 `build` 값으로 **지금 어떤 이미지가 돌고 있는지** 확인할 수 있다.

## 이미지 빌드·푸시

```bash
export REGION=ap-northeast-2
export REPO=shopd
bash build-push.sh          # v1, v2 두 태그를 모두 ECR 에 푸시
```

스크립트는 리포지토리가 없으면 만들지 않는다. **리포지토리는 문제지 요구사항대로 먼저 생성**해 두어야 한다.
