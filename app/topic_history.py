import json, os
from pathlib import Path

PATH = Path(os.getenv("TOPIC_HISTORY_FILE", "/data/topic-history.json"))

def load() -> list[str]:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return [str(x) for x in data if str(x).strip()]
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return []

def add(topics: list[str]) -> None:
    history = load()
    for topic in topics:
        value = topic.get("title", "") if isinstance(topic, dict) else str(topic)
        if value and value not in history: history.append(value)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(history[-100:], ensure_ascii=False, indent=2), encoding="utf-8")
