import logging, os
from .models import TaskDraft, TaskRequest

logger = logging.getLogger("aws-task-agent")

async def generate(req: TaskRequest) -> TaskDraft:
    backend = os.getenv("AGENT_BACKEND", "opencode").lower()
    if backend == "opencode":
        from .opencode import generate as run
        try:
            return await run(req)
        except Exception as exc:
            logger.warning("OpenCode unavailable (%s: %s); switching to Gemini fallback", type(exc).__name__, str(exc)[:500])
            # OpenCode Go의 일시적인 503/failover_exhausted로 전체 과제를 실패시키지 않는다.
            if os.getenv("OPENCODE_FALLBACK_GEMINI", "true").lower() == "true":
                from .gemini import generate as fallback
                return await fallback(req)
            raise
    from .gemini import generate as run
    return await run(req)
