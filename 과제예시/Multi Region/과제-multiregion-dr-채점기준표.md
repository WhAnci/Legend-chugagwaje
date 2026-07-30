# 제4과제 — 멀티리전 재해복구(DR) 아키텍처 · 채점기준표

> 총 100점 · 인프라 구성 40점 + 동작 검증 60점
> 채점 도구: `grade-multiregion-dr.sh` (CloudShell에서 실행)
> 동작 검증은 **위임된 실 도메인 `app.<비번호>.gongju.click` 에 외부에서 직접 curl** 하여 수행한다.

## 사전 조건 (채점 전 확인)

- 선수는 자기 계정에 `<비번호>.gongju.click` 호스팅 영역을 생성하고, NS 4개를 **Slack으로 운영자에게 전달**.
- 운영자가 부모 도메인 `gongju.click` 에 NS 위임 레코드를 등록 완료.
- `dig app.<비번호>.gongju.click +short` 가 공용 DNS로 응답해야 채점 가능. (미위임 시 동작 0점)

실행:
```bash
ZONE_NAME=<비번호>.gongju.click bash grade-multiregion-dr.sh <비번호>
```

---

## 1. 인프라 구성 (40점)

| 항목 | 세부 기준 | 배점 |
|---|---|---|
| 네트워크(VPC) | 서울·도쿄 각 VPC 존재 + 각 VPC에 서로 다른 AZ 퍼블릭 서브넷 2개 + IGW | 10 |
| ASG (서울) | `wsc2026-dr-asg-p-<비번호>` desired≥2, Health Check Type = ELB | 5 |
| ASG (도쿄) | `wsc2026-dr-asg-s-<비번호>` desired≥2, Health Check Type = ELB | 5 |
| ALB + Target Group | 양 리전 ALB + TG, 헬스체크 경로 `/health` | 10 |
| Route53 페일오버 | 동일 레코드명에 PRIMARY(서울)·SECONDARY(도쿄) + Primary에 헬스체크 연결 | 10 |

## 2. 동작 검증 (60점)

> 모두 외부에서 `app.<비번호>.gongju.click` 로 직접 접근하여 검증한다.

| 항목 | 세부 기준 | 배점 |
|---|---|---|
| 정상 접속 | 외부 curl `http://app.<비번호>.gongju.click/` → `REGION ap-northeast-2`(서울) 응답 | 20 |
| 리전 장애 페일오버 | 서울 ASG desired=0 주입 → 헬스체크 실패 → 외부 curl 응답이 `REGION ap-northeast-1`(도쿄)로 자동 전환 | 30 |
| 헬스체크 관측 | Route53 헬스체크 관측 데이터 조회 가능(체커 1곳 이상) | 10 |

---

## 채점 메커니즘 요약

1. **인프라 존재/구성**을 AWS API로 확인 (VPC·서브넷·IGW·ASG·ALB·TG·Route53).
2. **정상 동작**: 실 도메인으로 외부 curl → 서울 응답 확인.
3. **장애 주입**: 서울 ASG를 0으로 낮춰 서울 ALB가 5xx → Route53 헬스체크 실패 유도.
4. **자동 페일오버**: 약 1~2분 내 외부 curl 응답이 도쿄로 전환되는지 확인 (최대 6분 폴링).
5. **자동 복구**: 채점 종료 시 서울 ASG를 desired=2로 복구.

## 유의 사항

- 동작 검증은 단순 리소스 존재가 아니라 **실제 리전 장애를 일으켜 외부에서 DNS 페일오버를 확인**한다.
- DNS TTL·헬스체크 주기로 페일오버 감지에 1~2분 소요될 수 있다 (정상).
- 채점이 ASG를 일시적으로 0으로 낮췄다가 복구하므로, 채점 직후에는 인스턴스 재기동 시간이 필요하다.
