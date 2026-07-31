import logging, os, json
from .models import TaskDraft, TaskRequest
from .blueprint import AssignmentBlueprint, create_blueprint, validate_blueprint, review_generated_draft, blueprint_prompt
from . import ai_control

logger = logging.getLogger("aws-task-agent")

async def generate(req: TaskRequest) -> TaskDraft:
    if not ai_control.is_enabled():
        raise RuntimeError("AI 생성이 현재 꺼져 있습니다. /ai on 또는 /ai opencode를 사용하세요.")
    # AssignmentBlueprint is designed and reviewed before any assignment JSON/PDF generation.
    blueprint = AssignmentBlueprint.model_validate_json(req.approved_blueprint) if req.approved_blueprint else create_blueprint(req)
    blueprint_errors = validate_blueprint(blueprint)
    if blueprint_errors:
        raise RuntimeError("Blueprint 검증 실패: " + "; ".join(blueprint_errors))
    req = req.model_copy(update={"analysis": (req.analysis + "\n\n" if req.analysis else "") + blueprint_prompt(blueprint)})
    backend = (ai_control.backend() or os.getenv("AGENT_BACKEND", "opencode")).lower()

    def reviewed(draft: TaskDraft) -> TaskDraft:
        errors = review_generated_draft(blueprint, draft)
        if errors:
            draft.notes = "BLUEPRINT_REVIEW_ERRORS: " + " | ".join(errors)
        return draft
    if backend == "opencode":
        from .opencode import generate as run
        try:
            return reviewed(await run(req))
        except Exception as exc:
            logger.warning("OpenCode unavailable (%s: %s); switching to Gemini fallback", type(exc).__name__, str(exc)[:500])
            # OpenCode Go의 일시적인 503/failover_exhausted로 전체 과제를 실패시키지 않는다.
            if os.getenv("OPENCODE_FALLBACK_GEMINI", "true").lower() == "true":
                from .gemini import generate as fallback
                return reviewed(await fallback(req))
            raise
    from .gemini import generate as run
    return reviewed(await run(req))
