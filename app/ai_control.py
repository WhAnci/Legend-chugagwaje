"""Runtime AI backend controls changed by authorized Discord commands."""
_enabled = True
_backend = ""  # empty means AGENT_BACKEND

def status() -> tuple[bool, str]:
    return _enabled, _backend or "환경설정"

def configure(mode: str) -> tuple[bool, str]:
    global _enabled, _backend
    value = mode.strip().lower()
    if value in {"on", "켜기", "활성화"}: _enabled = True
    elif value in {"off", "끄기", "비활성화"}: _enabled = False
    elif value in {"opencode", "open-code"}: _enabled, _backend = True, "opencode"
    elif value in {"gemini"}: _enabled, _backend = True, "gemini"
    elif value in {"reset", "기본값", "auto", "자동"}: _enabled, _backend = True, ""
    else: raise ValueError("사용법: auto, opencode, gemini, status")
    return status()

def is_enabled() -> bool: return _enabled
def backend() -> str: return _backend
