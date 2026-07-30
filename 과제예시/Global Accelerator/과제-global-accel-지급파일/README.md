# 지급파일 — arena (매치메이킹 노드)

| 파일 | 용도 |
|---|---|
| `arena` | 매치메이킹 노드 바이너리 (linux/amd64, static). 빌드·수정 불필요 |
| `app-src/` | 참고용 소스. 빌드할 필요 없음 |

## 애플리케이션 명세

TCP **8080** 포트에서 HTTP 로 대기한다 (환경변수 `PORT` 로 변경 가능).
EC2 인스턴스에서 실행하며, 리전·AZ·인스턴스 ID 는 IMDSv2 에서 자동으로 읽는다.

| 엔드포인트 | 응답 |
|---|---|
| `GET /health` | `200 ok` — 로드밸런서 헬스체크 대상. 드레인 상태면 `503` |
| `GET /whoami` | `{"region":"...","az":"...","instance":"i-...","node":"..."}` — **어느 리전의 노드가 응답했는지** 식별 |
| `GET /match` | 매치 성사 응답 JSON (`region`, `node`, `peer` 포함) |
| `GET /drain` | 이 노드를 헬스체크 실패(503) 상태로 전환 — **리전 장애 시뮬레이션용** |
| `GET /restore` | 헬스체크 정상 상태로 복구 |

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `PORT` | `8080` | 대기 포트 |
| `NODE_NAME` | 호스트명 | 노드 식별 라벨 |
| `REGION` | IMDS 자동탐지 | 응답에 표시할 리전 |

## 실행 예시 (EC2 user_data)

```bash
#!/bin/bash
curl -fsSL <arena 바이너리 URL> -o /usr/local/bin/arena
chmod +x /usr/local/bin/arena
cat >/etc/systemd/system/arena.service <<'EOF'
[Unit]
Description=arena
[Service]
ExecStart=/usr/local/bin/arena
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl enable --now arena
```

바이너리는 각자 S3 등에 올려 두고 받아 써도 되고, 인스턴스에 직접 복사해도 된다.
