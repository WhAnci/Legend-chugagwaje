import json, os
from pathlib import Path

PATH = Path(os.getenv("USAGE_FILE", "/data/usage.json"))

def record(provider: str, model: str, usage: dict | None = None) -> None:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, ValueError, TypeError):
        data = {}
    providers = data.setdefault("providers", {})
    item = providers.setdefault(provider, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "models": {}})
    item["requests"] += 1
    usage = usage or {}
    input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", usage.get("promptTokenCount", 0))) or 0)
    output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", usage.get("candidatesTokenCount", 0))) or 0)
    total_tokens = int(usage.get("total_tokens", usage.get("totalTokenCount", input_tokens + output_tokens)) or 0)
    item["input_tokens"] += input_tokens
    item["output_tokens"] += output_tokens
    item["total_tokens"] += total_tokens
    models = item.setdefault("models", {})
    model_item = models.setdefault(model, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    model_item["requests"] += 1
    model_item["input_tokens"] += input_tokens
    model_item["output_tokens"] += output_tokens
    model_item["total_tokens"] += total_tokens
    data["updated_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def links() -> str:
    return "🔗 사용량 확인 링크\n- [Gemini AI Studio](https://aistudio.google.com/u/3/usage?timeRange=last-28-days)\n- [OpenCode Workspace](https://opencode.ai/workspace/wrk_01KYDZAY6CZCWE0PRV2HCZ0KCB/usage)"

def summary() -> str:
    try: data = json.loads(PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError): return "📊 API 사용량 기록이 없습니다."
    lines = ["📊 API 사용량"]
    for provider, item in data.get("providers", {}).items():
        lines.append(f"\n**{provider}**")
        lines.append(f"요청: {item.get('requests', 0)}회")
        lines.append(f"입력 토큰: {item.get('input_tokens', 0):,}")
        lines.append(f"출력 토큰: {item.get('output_tokens', 0):,}")
        lines.append(f"총 토큰: {item.get('total_tokens', 0):,}")
        for model, values in item.get("models", {}).items():
            lines.append(f"`{model}`: {values.get('requests', 0)}회 / {values.get('total_tokens', 0):,} tokens")
    if data.get("updated_at"): lines.append(f"\n최근 갱신: {data['updated_at']}")
    return "\n".join(lines)
