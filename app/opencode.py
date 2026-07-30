import asyncio, base64, json, logging, os, re, time
from pathlib import Path
import httpx
from .models import TaskDraft, TaskRequest
from .prompt import SYSTEM, make_prompt
from .usage import record as record_usage

class OpenCodeError(RuntimeError): pass

logger = logging.getLogger("aws-task-opencode")

def _json_object(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    start = text.find("{")
    if start < 0: raise ValueError("OpenCode 응답에서 JSON 객체를 찾지 못했습니다")
    payload = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text[start:])
    decoder = json.JSONDecoder()
    for _ in range(100):
        try:
            value, _ = decoder.raw_decode(payload)
            return value
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

async def generate(req: TaskRequest) -> TaskDraft:
    started = time.monotonic()
    api_key = os.getenv("OPENCODE_API_KEY", "").strip()
    logger.info("request start model=%s api_mode=%s prompt_request=%s", os.getenv("OPENCODE_MODEL", "opencode-go/deepseek-v4-flash"), bool(api_key), req.raw[:100])
    model_setting = os.getenv("OPENCODE_MODEL", "opencode-go/deepseek-v4-flash")
    model_id = model_setting.split("/", 1)[-1]

    # API 키가 있으면 OpenCode Go의 OpenAI 호환 API를 직접 사용한다.
    # 이 경로는 별도 opencode serve 프로세스가 필요 없다.
    if api_key:
        api_url = os.getenv("OPENCODE_API_URL", "https://opencode.ai/zen/go/v1").rstrip("/") + "/chat/completions"
        body = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": make_prompt(req, include_references=False, include_system=False)}
            ],
            "temperature": 0.2,
        }
        retries = min(int(os.getenv("OPENCODE_RETRIES", "1")), 2)
        timeout = float(os.getenv("OPENCODE_TIMEOUT_SECONDS", "180"))
        last_error = ""
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(retries + 1):
                logger.info("OpenCode API attempt=%d/%d", attempt + 1, retries + 1)
                try:
                    response = await client.post(api_url, headers={"Authorization": f"Bearer {api_key}"}, json=body)
                except httpx.ReadTimeout as exc:
                    last_error = f"read timeout after {timeout}s"
                    logger.warning("OpenCode API read timeout attempt=%d/%d", attempt + 1, retries + 1)
                    if attempt < retries:
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    break
                logger.info("OpenCode API status=%s elapsed=%ss", response.status_code, int(time.monotonic() - started))
                if response.status_code < 400:
                    try:
                        payload = response.json()
                        record_usage("OpenCode", model_id, payload.get("usage", {}))
                        text = payload["choices"][0]["message"]["content"]
                        return TaskDraft.model_validate(_json_object(text))
                    except Exception as exc:
                        raise OpenCodeError(f"OpenCode Go API JSON 파싱 실패: {exc}") from exc
                last_error = f"{response.status_code}: {response.text[:800]}"
                logger.warning("OpenCode API transient/failure: %s", last_error[:300])
                # failover_exhausted는 OpenCode provider 자체가 이미 모든 upstream을
                # 시도한 상태이므로 같은 요청을 즉시 반복하지 않는다.
                if "failover_exhausted" in response.text:
                    logger.warning("OpenCode provider failover exhausted; switching to fallback")
                    break
                if response.status_code not in {408, 429, 500, 502, 503, 504} or attempt >= retries:
                    break
                await asyncio.sleep(5 * (attempt + 1))
        raise OpenCodeError(f"OpenCode Go API 오류(재시도 {retries}회 후): {last_error}")

    logger.info("using OpenCode server mode elapsed=%ss", int(time.monotonic() - started))
    base = os.getenv("OPENCODE_URL", "http://host.docker.internal:4096").rstrip("/")
    directory = os.getenv("OPENCODE_DIRECTORY", "")
    model = os.getenv("OPENCODE_MODEL", "")
    body = {}
    if model and "/" in model:
        provider, model_id = model.split("/", 1)
        body["model"] = {"providerID": provider, "id": model_id}
    prompt = make_prompt(req, include_references=False, include_system=False) + "\n\n반드시 최종 응답은 JSON 객체 하나만 반환하라. 작업공간의 과제예시 파일은 읽기 전용으로 참고하고 결과 파일은 JSON 필드로 반환하라."
    async with httpx.AsyncClient(timeout=float(os.getenv("OPENCODE_TIMEOUT_SECONDS", "180")), headers=_headers()) as client:
        session_params = {"directory": directory} if directory else {}
        session = await client.post(f"{base}/session", params=session_params, json=body)
        if session.status_code >= 400:
            raise OpenCodeError(f"OpenCode 세션 생성 실패 {session.status_code}: {session.text[:500]}")
        sid = session.json().get("id")
        if not sid: raise OpenCodeError("OpenCode 세션 ID가 없습니다")
        payload = {"system": SYSTEM, "parts": [{"type": "text", "text": prompt}]}
        if model and "/" in model:
            provider, model_id = model.split("/", 1)
            payload["model"] = {"providerID": provider, "modelID": model_id}
        response = await client.post(f"{base}/session/{sid}/message", params=session_params, json=payload)
    if response.status_code >= 400:
        raise OpenCodeError(f"OpenCode 응답 실패 {response.status_code}: {response.text[:800]}")
    try:
        data = response.json()
        record_usage("OpenCode", model or "server", data.get("usage", {}))
        text = "\n".join(p.get("text", "") for p in data.get("parts", []) if p.get("type") == "text")
        return TaskDraft.model_validate(_json_object(text))
    except Exception as exc:
        raise OpenCodeError(f"OpenCode JSON 파싱 실패: {exc}") from exc
