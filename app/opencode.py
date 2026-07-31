import asyncio, base64, json, logging, os, re, time
import httpx
from .models import TaskDraft, TaskRequest
from .prompt import SYSTEM, make_prompt
from .blueprint import AssignmentBlueprint, reviewer_prompt
from .usage import record as record_usage

class OpenCodeError(RuntimeError): pass
logger = logging.getLogger("aws-task-opencode")

def _json_object(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    start = text.find("{")
    if start < 0: raise ValueError("OpenCode 응답에서 JSON 객체를 찾지 못했습니다")
    payload = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text[start:])
    payload = re.sub(r",\s*([}\]])", r"\1", payload)
    decoder = json.JSONDecoder()
    for _ in range(100):
        try: value, _ = decoder.raw_decode(payload); return value
        except json.JSONDecodeError as exc:
            if "escape" not in exc.msg.lower() and "unicode" not in exc.msg.lower(): raise
            payload = payload[:exc.pos] + "\\\\" + payload[exc.pos:]
    raise ValueError("JSON escape 보정 한도를 초과했습니다")

def _headers() -> dict:
    password = os.getenv("OPENCODE_SERVER_PASSWORD", "")
    if password:
        token = base64.b64encode(f"opencode:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {}

def _models() -> list[str]:
    configured = os.getenv("OPENCODE_MODELS", "")
    values = [x.strip() for x in configured.split(",") if x.strip()] if configured else []
    if not values: values = [os.getenv("OPENCODE_MODEL", "opencode-go/deepseek-v4-flash"), "opencode-go/mimo-v2.5"]
    return list(dict.fromkeys(values))

async def _api_model(req: TaskRequest, api_key: str, model_setting: str) -> TaskDraft:
    model_id = model_setting.split("/", 1)[-1]
    api_url = os.getenv("OPENCODE_API_URL", "https://opencode.ai/zen/go/v1").rstrip("/") + "/chat/completions"
    body = {"model": model_id, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": make_prompt(req, include_references=False, include_system=False)}], "temperature": 0.2}
    retries = min(int(os.getenv("OPENCODE_RETRIES", "1")), 2); timeout = float(os.getenv("OPENCODE_TIMEOUT_SECONDS", "120")); last_error = ""
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retries + 1):
            logger.info("OpenCode API attempt=%d/%d model=%s", attempt + 1, retries + 1, model_setting)
            try: response = await client.post(api_url, headers={"Authorization": f"Bearer {api_key}"}, json=body)
            except httpx.ReadTimeout:
                last_error = f"read timeout after {timeout}s"
                if attempt < retries: continue
                break
            logger.info("OpenCode API status=%s model=%s", response.status_code, model_setting)
            if response.status_code < 400:
                try:
                    payload = response.json(); record_usage("OpenCode", model_id, payload.get("usage", {}))
                    return TaskDraft.model_validate(_json_object(payload["choices"][0]["message"]["content"]))
                except Exception as exc: raise OpenCodeError(f"OpenCode Go API JSON 파싱 실패: {exc}") from exc
            last_error = f"{response.status_code}: {response.text[:800]}"
            logger.warning("OpenCode API transient/failure model=%s: %s", model_setting, last_error[:300])
            if "failover_exhausted" in response.text:
                if response.status_code == 503 and attempt < retries and os.getenv("OPENCODE_FAILOVER_RETRY_FAILOVER_EXHAUSTED", "false").lower() == "true":
                    await asyncio.sleep(float(os.getenv("OPENCODE_FAILOVER_RETRY_DELAY_SECONDS", "20"))); continue
                break
            if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt >= retries: break
            await asyncio.sleep(5 * (attempt + 1))
    raise OpenCodeError(f"OpenCode Go API 오류 모델={model_setting}: {last_error}")

async def review_blueprint(blueprint: AssignmentBlueprint) -> list[str]:
    """Ask OpenCode to review a Blueprint before it is shown for approval."""
    api_key = os.getenv("OPENCODE_API_KEY", "").strip()
    if not api_key: raise OpenCodeError("OpenCode API 키가 없어 Blueprint 검토를 수행할 수 없습니다.")
    prompt = reviewer_prompt(blueprint) + "\n검토 결과는 JSON 배열만 반환하라. 문제가 없으면 []를 반환하라."
    timeout = float(os.getenv("OPENCODE_REVIEW_TIMEOUT_SECONDS", "45"))
    errors = []
    for model_setting in _models():
        model_id = model_setting.split("/", 1)[-1]
        body = {"model": model_id, "messages": [{"role": "system", "content": "너는 AWS 과제 Blueprint Reviewer다."}, {"role": "user", "content": prompt}], "temperature": 0}
        try:
            url = os.getenv("OPENCODE_API_URL", "https://opencode.ai/zen/go/v1").rstrip("/") + "/chat/completions"
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=body)
            if response.status_code >= 400:
                errors.append(f"{model_setting}: HTTP {response.status_code}"); continue
            text = response.json()["choices"][0]["message"]["content"].strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
            start, end = text.find("["), text.rfind("]")
            if start < 0 or end < start: raise ValueError("Reviewer JSON 배열이 없습니다")
            result = json.loads(text[start:end + 1])
            return [str(item) for item in result if str(item).strip()] if isinstance(result, list) else ["Reviewer 결과가 배열이 아닙니다."]
        except Exception as exc:
            errors.append(f"{model_setting}: {repr(exc)[:200]}")
    raise OpenCodeError("OpenCode Blueprint Reviewer 실패: " + " | ".join(errors[-3:]))

async def generate(req: TaskRequest) -> TaskDraft:
    started = time.monotonic(); api_key = os.getenv("OPENCODE_API_KEY", "").strip(); models = _models()
    logger.info("request start models=%s api_mode=%s prompt_request=%s", ",".join(models), bool(api_key), req.raw[:100])
    if api_key:
        errors = []
        for model in models:
            try: return await _api_model(req, api_key, model)
            except OpenCodeError as exc:
                errors.append(str(exc)); logger.warning("OpenCode model unavailable; trying next model=%s", model)
        raise OpenCodeError(f"OpenCode 모델을 모두 사용할 수 없습니다(경과 {int(time.monotonic()-started)}s): " + " | ".join(errors[-3:]))

    logger.info("using OpenCode server mode elapsed=%ss", int(time.monotonic() - started))
    base = os.getenv("OPENCODE_URL", "http://host.docker.internal:4096").rstrip("/"); directory = os.getenv("OPENCODE_DIRECTORY", "")
    model = os.getenv("OPENCODE_MODEL", ""); body = {}
    if model and "/" in model:
        provider, model_id = model.split("/", 1); body["model"] = {"providerID": provider, "id": model_id}
    prompt = make_prompt(req, include_references=False, include_system=False) + "\n\n반드시 최종 응답은 JSON 객체 하나만 반환하라."
    async with httpx.AsyncClient(timeout=float(os.getenv("OPENCODE_TIMEOUT_SECONDS", "120")), headers=_headers()) as client:
        params = {"directory": directory} if directory else {}
        session = await client.post(f"{base}/session", params=params, json=body)
        if session.status_code >= 400: raise OpenCodeError(f"OpenCode 세션 생성 실패 {session.status_code}: {session.text[:500]}")
        sid = session.json().get("id")
        payload = {"system": SYSTEM, "parts": [{"type": "text", "text": prompt}]}
        if model and "/" in model:
            provider, model_id = model.split("/", 1); payload["model"] = {"providerID": provider, "modelID": model_id}
        response = await client.post(f"{base}/session/{sid}/message", params=params, json=payload)
    if response.status_code >= 400: raise OpenCodeError(f"OpenCode 응답 실패 {response.status_code}: {response.text[:800]}")
    try:
        data = response.json(); record_usage("OpenCode", model or "server", data.get("usage", {})); text = "\n".join(p.get("text", "") for p in data.get("parts", []) if p.get("type") == "text")
        return TaskDraft.model_validate(_json_object(text))
    except Exception as exc: raise OpenCodeError(f"OpenCode JSON 파싱 실패: {exc}") from exc
