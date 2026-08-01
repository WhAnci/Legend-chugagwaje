import json, os
from pathlib import Path
PATH = Path(os.getenv("TOPIC_HISTORY_FILE", "/data/topic-history.json"))

def load() -> list[dict]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        result=[]
        for item in data:
            if isinstance(item, dict): result.append(item)
            elif str(item).strip(): result.append({"title": str(item)})
        return result
    except (FileNotFoundError, OSError, ValueError, TypeError): return []

def add(topics: list[dict]) -> None:
    history=load(); seen={x.get("title") for x in history}
    for topic in topics:
        if not isinstance(topic, dict): topic={"title": str(topic)}
        title=str(topic.get("title", "")).strip()
        if title and title not in seen:
            history.append({key: topic.get(key) for key in ("title","primaryService","supportingServices","architectureDomain","architecturePattern","estimatedModuleCount","behaviorValidation")})
            seen.add(title)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(history[-100:], ensure_ascii=False, indent=2), encoding="utf-8")
