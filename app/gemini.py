import json, logging, os, re, time
import httpx
from .models import TaskDraft, TaskRequest
from .prompt import make_prompt
from .usage import record as record_usage
from .topic_validation import validate_topic_candidate, filter_topic_candidates
from .fallback_topics import FALLBACK_TOPIC_POOL

class GeminiError(RuntimeError): pass

logger = logging.getLogger("aws-task-gemini")

async def _call(prompt: str, *, json_mode: bool = True, model_override: str | None = None, timeout: float | None = None) -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise GeminiError("GEMINI_API_KEY가 설정되지 않았습니다.")
    model = model_override or os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    config = {"temperature": 0.2}
    if json_mode: config["responseMimeType"] = "application/json"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}
    started = time.monotonic()
    logger.info("Gemini request start model=%s", model)
    request_timeout = timeout if timeout is not None else float(os.getenv("GEMINI_TIMEOUT_SECONDS", "90"))
    async with httpx.AsyncClient(timeout=request_timeout) as client:
        response = await client.post(url, params={"key": key}, json=body)
    logger.info("Gemini response status=%s elapsed=%ss", response.status_code, int(time.monotonic() - started))
    if response.status_code >= 400:
        raise GeminiError(f"Gemini API 오류 {response.status_code}: {response.text[:500]}")
    try:
        payload = response.json()
        record_usage("Gemini", model, payload.get("usageMetadata", {}))
        return payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as exc:
        raise GeminiError(f"Gemini 응답 본문 파싱 실패: {exc}") from exc

def _object(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    start = text.find("{")
    if start < 0: raise ValueError("JSON 객체 시작을 찾을 수 없음")
    # 모델이 JSON 문자열 안의 백슬래시와 trailing comma를 보정한다.
    payload = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text[start:])
    payload = re.sub(r",\\s*([}\\]])", r"\\1", payload)
    decoder = json.JSONDecoder()
    for _ in range(100):
        try:
            value, _ = decoder.raw_decode(payload)
            return value
        except json.JSONDecodeError as exc:
            if "escape" not in exc.msg.lower() and "unicode" not in exc.msg.lower(): raise
            payload = payload[:exc.pos] + "\\\\" + payload[exc.pos:]
    raise ValueError("JSON escape 보정 한도를 초과했습니다")

def _array(text: str) -> list:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    start = text.find("[")
    if start < 0: raise ValueError("JSON 배열 시작을 찾을 수 없음")
    payload = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text[start:])
    payload = re.sub(r",\\s*([}\\]])", r"\\1", payload)
    decoder = json.JSONDecoder()
    for _ in range(100):
        try:
            value, _ = decoder.raw_decode(payload)
            return value
        except json.JSONDecodeError as exc:
            if "escape" not in exc.msg.lower() and "unicode" not in exc.msg.lower(): raise
            payload = payload[:exc.pos] + "\\\\" + payload[exc.pos:]
    raise ValueError("JSON 배열 escape 보정 한도를 초과했습니다")

async def suggest_topics(raw: str, previous: list | None = None) -> list[dict]:
    previous_text = "\n".join(f"- {x.get('title', x) if isinstance(x, dict) else x}" for x in (previous or [])) or "없음"
    prompt = f"""AWS 클라우드 대회용 추가과제 후보 3개를 기획하라.

목표는 단순히 서로 다른 AWS 서비스를 사용하는 것이 아니라, 선수에게 요구하는 문제 해결 방식, 아키텍처 패턴, 검증 방식이 서로 다른 과제를 만드는 것이다.

후보 생성 전 내부적으로 다음 순서로 사고하라.
1. 서로 다른 문제 유형 3개를 선택한다.
2. 각 문제 유형에 적합한 주력 AWS 서비스를 선택한다.
3. 현실적인 운영 시나리오를 설계한다.
4. 60분 안에 구현 및 자동 채점 가능한 범위로 축소한다.
5. 이전 과제와 제목뿐 아니라 구조와 검증 방식까지 비교한다.

문제 유형 후보: 보안 통제, 장애 복구, 데이터 정합성, 메시지 순서 및 중복 방지, 배포 안전성, 네트워크 격리, 운영 자동화, 백업 및 복원, 비밀정보 및 암호화, 트래픽 제어, 감사 및 추적, 상태 기반 워크플로, 비용 및 리소스 통제, 서비스 간 접근 제어, 실패 이벤트 재처리.

과제 형식 후보: 신규 구축형, 잘못된 기존 구성을 수정하는 장애 해결형, 보안 요구사항을 추가하는 강화형, 수동 절차를 자동화하는 운영 자동화형, 마이그레이션형, 지급 리소스 통합형, 실패 조건 회복성 검증형.

후보 3개는 반드시 주력 서비스, 서비스 family, 문제 유형, 아키텍처 패턴, 핵심 검증 방식, 선수의 주요 작업이 달라야 한다. 서비스 이름만 다르고 구조가 동일한 후보는 금지한다. 이벤트→Lambda→DynamoDB, 이벤트→EventBridge→Lambda, 큐→Lambda, 단순 리소스 생성 후 상태 확인 구조를 반복하지 마라.

각 과제에는 실제 동작 검증이 있어야 한다. 예: 중복 결과 방지, 취약 이미지 차단, 네트워크 접근 차단, 실제 백업 복원, 엣지 헤더 차단, DLQ 재처리, SNS 필터링, 만료 자격증명 거부, 선택적 장애 복구, 멱등성 검증.

AI, IoT, 게임, IAM Identity Center는 제외한다. supportingServices는 2~4개, 총 logical service module은 3~5개로 구성한다. 4개 구성을 우선하며, 60분 안에 생성·연결·동작 테스트·CLI 자동 채점·저비용 정리가 가능해야 한다.

최근 주제와 구조적 중복 기준: 같은 주력 서비스, 리소스 유형, 이벤트 흐름, 실패 조건, 검증 방식이 겹치거나 제목만 바뀐 경우 제외한다.
최근 주제 이력:
{previous_text}
사용자 요청:
{raw}

내부적으로 간단히 비교한 뒤 가장 차별화된 3개만 선택하라. 장황한 설명 없이 JSON 배열만 반환하라. 각 원소는 title, problemType, taskType, primaryService, supportingServices(2~4개), expectedModules(3~5개 service/role/required), architectureDomain, architecturePattern, estimatedModuleCount, scenario, coreWork 배열, behaviorValidation, difference 필드를 가져야 한다. 세 후보는 서로 다른 domain/pattern을 사용하고 Lambda/S3/EventBridge 중심 반복을 피하라."""
    try:
        items = _array(await _call(prompt, timeout=float(os.getenv("GEMINI_TOPIC_TIMEOUT_SECONDS", "35"))) )
        if isinstance(items, list) and len(items) >= 3 and all(isinstance(x, dict) for x in items[:3]):
            values = []
            for item in items[:3]:
                values.append({
                    "title": str(item.get("title", "AWS 추가과제")).strip(),
                    "problemType": str(item.get("problemType", "")).strip(),
                    "taskType": str(item.get("taskType", "")).strip(),
                    "primaryService": str(item.get("primaryService", "")).strip(),
                    "supportingServices": [str(x) for x in item.get("supportingServices", [])],
                    "scenario": str(item.get("scenario", "")).strip(),
                    "coreWork": [str(x) for x in item.get("coreWork", [])],
                    "behaviorValidation": str(item.get("behaviorValidation", "")).strip(),
                    "difference": str(item.get("difference", "")).strip(),
                    "expectedModules": item.get("expectedModules", []),
                    "architectureDomain": str(item.get("architectureDomain", "")).strip(),
                    "architecturePattern": str(item.get("architecturePattern", "")).strip(),
                    "estimatedModuleCount": int(item.get("estimatedModuleCount", len(item.get("expectedModules", [])) or 0)),
                })
            valid = filter_topic_candidates(values, previous or [])
            if len(valid) >= 3: return valid[:3]
            # Keep valid Gemini candidates and fill only the missing slots.
            logger.warning("topic candidates partially valid: passed=%d requested=3", len(valid))
            recent_titles = {x.get("title") for x in (previous or []) if isinstance(x, dict)}
            used_titles = {x.get("title") for x in valid}
            pool = [x for x in FALLBACK_TOPIC_POOL if x.get("title") not in recent_titles and x.get("title") not in used_titles]
            valid.extend(pool[:max(0, 3 - len(valid))])
            if len(valid) >= 3: return valid[:3]
            raise ValueError("3개 logical module과 다양성 검증을 통과한 후보가 부족합니다")
    except Exception as exc:
        logger.warning("topic suggestion failed; using deterministic fallback: %s", str(exc)[:300])
    # 주제 후보는 산출물 생성 전 선택 화면용이므로 Gemini 일시 장애나 JSON 형식 오류가
    # 전체 UX를 막지 않도록 결정적 후보를 제공한다. 실제 과제 산출물은 승인 후
    # Blueprint/검증 파이프라인을 다시 통과한다.
    fallback = list(FALLBACK_TOPIC_POOL)
    recent_titles = {x.get("title") for x in (previous or []) if isinstance(x, dict)}
    available = [x for x in fallback if x.get("title") not in recent_titles]
    logger.warning("topic fallback selected: passed=0 fallback_count=%d", min(3, len(available)))
    return available[:3] if len(available) >= 3 else fallback[:3]

async def review_draft(raw: str, draft) -> list[str]:
    """DeepSeek 산출물에 대한 단 한 번의 최종 내용 검토."""
    payload = draft.document.model_dump(mode="json") if draft.document else {}
    prompt = f"""AWS 과제 PDF 최종 검토자다. 아래 입력과 구조화 문서가 메타데이터를 그대로 보존하는지, 빈 항목/단독 번호 찌꺼기 원인이 될 구조가 없는지 한 번만 검토하라. JSON 배열만 반환하라. 문제가 없으면 []를 반환한다. 원문 입력: {raw}\n문서: {json.dumps(payload, ensure_ascii=False)}"""
    try:
        result = json.loads(await _call(prompt))
        return [str(x) for x in result] if isinstance(result, list) else []
    except Exception:
        return []

async def analyze_request(req: TaskRequest) -> str:
    prompt = f"""AWS 대회 추가과제 제작을 위한 요구사항 분석 담당자다.
사용자 요청을 분석해 OpenCode Go 제작자에게 전달할 설계 메모를 JSON으로 반환하라. CloudShell은 채점 전용으로만 사용하며, 과제에서 Client나 실제 요청 발생기가 필요하면 EC2 Client 인스턴스 또는 지급파일을 설계한다.
필드: topic, service_scope, difficulty, region, duration_minutes, scenario, required_components, behavior_tests, constraints, deliverables.
주제가 없으면 예시와 가이드에 맞는 현실적인 주제 하나를 선정하라. Cognito, WebSocket, AppSync, WAF, CloudFront Functions, EventBridge Pipes 같은 특색 있는 서비스를 우선 검토하되 1시간 안에 검증 가능한 범위로 축소하라.
반드시 1시간 이내, 총점 6점, 독립적으로 채점 가능한 핵심 AWS 리소스는 각각 별도 module로 분리하고 직접 연결된 보조 서비스만 포함하며, 직접 생성 리소스 10개 이하로 제한하라. 주제의 중심 리소스와 무관한 서비스는 추가하지 마라. EKS 과제는 EKS 클러스터를 중심으로 Kubernetes/CNCF 구성요소를 사용할 수 있다.
구형 vf 통합본은 참고하지 말고 새 독립 예시의 품질을 기준으로 하라.
사용자 요청: {req.raw}
정규화 조건: 서비스={req.service}, 난이도={req.difficulty}, 리전={req.region}, 제한시간={req.duration_minutes}분"""
    text = await _call(prompt)
    try:
        return json.dumps(_object(text), ensure_ascii=False, indent=2)
    except Exception as exc:
        raise GeminiError(f"요구사항 분석 JSON 파싱 실패: {exc}") from exc

async def generate(req: TaskRequest) -> TaskDraft:
    models = [x.strip() for x in os.getenv(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-3.6-flash"
    ).split(",") if x.strip()]
    errors = []
    for model in models:
        try:
            logger.info("Gemini generation model attempt=%s", model)
            text = await _call(make_prompt(req), json_mode=True, model_override=model)
            return TaskDraft.model_validate(_object(text))
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            logger.warning("Gemini model failed; trying fallback model=%s error=%s", model, str(exc)[:300])
    raise GeminiError("Gemini fallback 모델을 모두 사용할 수 없습니다: " + " | ".join(errors[-3:]))
