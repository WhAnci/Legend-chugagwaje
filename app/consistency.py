import re
from .models import TaskDraft


def _module_matches(expected: str, titles: set[str]) -> bool:
    if expected in titles: return True
    def canonical(value):
        for source, target in {"aws systems manager":"ssm", "systems manager":"ssm", "amazon systems manager":"ssm", "amazon rds":"rds", "amazon s3":"s3", "amazon sqs":"sqs", "amazon ecs":"ecs", "amazon ecr":"ecr", "aws waf":"waf", "amazon cloudfront":"cloudfront", "amazon cognito":"cognito", "amazon ec2":"ec2", "elastic compute cloud":"ec2", "load balancer":"alb", "load-balancer":"alb", "elb":"alb", "elastic load balancing":"alb", "route 53":"route53", "람다":"lambda", "다이나모디비":"dynamodb"}.items(): value = value.lower().replace(source, target)
        return value
    expected = canonical(expected)
    titles = {canonical(title) for title in titles}
    expected_tokens = {x for x in re.findall(r"[a-z0-9]+", expected.lower()) if x not in {"amazon", "aws", "service", "구성"}}
    for title in titles:
        title_tokens = set(re.findall(r"[a-z0-9]+", title.lower()))
        if len(expected_tokens & title_tokens) >= 1: return True
        compact_expected = re.sub(r"[^a-z0-9가-힣]", "", expected.lower())
        compact_title = re.sub(r"[^a-z0-9가-힣]", "", title.lower())
        if compact_expected in compact_title or compact_title in compact_expected: return True
    return False

CHECK_ID = re.compile(r"\b[A-Z][A-Z0-9_]*-\d{2,}\b")

def check_consistency(draft: TaskDraft) -> list[str]:
    """Cross-check the canonical checks against document, rubric and grading.sh."""
    errors = []
    checks = draft.checks
    if not checks:
        return ["공통 grading checks가 없습니다."]
    ids = [check.id for check in checks]
    if len(ids) != len(set(ids)): errors.append("checks에 중복 ID가 있습니다.")
    if any(not CHECK_ID.fullmatch(check.id) for check in checks): errors.append("check ID 형식이 잘못되었습니다.")
    total = sum(check.score for check in checks)
    if round(total, 2) != 6.0: errors.append(f"checks 총점이 6.0이 아닙니다: {total:g}")
    doc_text = draft.document.model_dump_json() if draft.document else ""
    module_titles = {m.title for m in draft.document.modules} if draft.document else set()
    module_blob = {m.id or m.title: m.model_dump_json().lower() for m in draft.document.modules} if draft.document else {}
    architecture_text = " ".join([draft.document.architecture, draft.document.overview, *draft.document.requirements]).lower() if draft.document else ""
    # 실행 구성요소와 지급파일은 반드시 실제 module에 귀속되어야 한다.
    execution_markers = {"lambda": ("lambda", "runtime", "handler", "function"), "api gateway": ("api gateway", "rest api", "http api"), "route 53": ("route 53", "route53", "failover record", "hosted zone")}
    for component, markers in execution_markers.items():
        if any(marker in architecture_text for marker in markers) and not any(any(marker in text for marker in markers) for text in module_blob.values()):
            errors.append(f"아키텍처의 실행 구성요소가 module에 없습니다: {component}")
    has_lambda_module = any("lambda" in text or "function" in text for text in module_blob.values())
    for module in draft.document.modules if draft.document else []:
        text = module.model_dump_json().lower()
        if "api gateway" in text and "integration" in text and not has_lambda_module:
            errors.append(f"API Gateway-Lambda integration에 대응하는 Lambda module이 없습니다: {module.title}")
        if any(token in text for token in ("hosted zone", "failover", "record name", "failover role")) and not any(token in module.title.lower() for token in ("route 53", "route53", "dns")):
            errors.append(f"Route 53 DNS/Failover 요구사항이 잘못된 module에 분류되었습니다: {module.title}")
    for file in draft.deployment_files:
        if "lambda" in file.path.lower() and not any("lambda" in text or "function" in text for text in module_blob.values()):
            errors.append(f"지급파일을 사용하는 Lambda module이 없습니다: {file.path}")
        if file.used_by_module and not any(module_id in module_blob for module_id in file.used_by_module):
            errors.append(f"지급파일의 usedByModule이 존재하지 않습니다: {file.path}")
    module_ids = {m.id for m in draft.document.modules} if draft.document else set()
    for check in checks:
        if check.module_id:
            if check.module_id not in module_ids:
                errors.append(f"[{check.id}] 존재하지 않는 moduleId입니다: {check.module_id}")
        elif not _module_matches(check.module, module_titles):
            errors.append(f"[{check.id}] 존재하지 않는 module입니다: {check.module}")
        for module in (draft.document.modules if draft.document else []):
            labels = " ".join(str(spec.label).lower() for spec in (module.fixed_specs or module.specs))
            if re.search(r"failover success|status ?code|body ?contain|bodycontains", labels):
                errors.append(f"[{module.title}] 동작 검증 조건이 fixedSpec에 섞였습니다.")
        for key, value in check.expected.items():
            if str(key).lower() in {"status", "dbinstancestatus", "state", "health", "health_status", "availability"}: continue
            if isinstance(value, (str, int, float)) and not isinstance(value, bool) and str(value).strip() and str(value).lower() not in doc_text.lower():
                errors.append(f"[{check.id}] expected 값이 과제 본문에 없습니다: {key}={value}")
        if check.id not in draft.rubric_markdown:
            errors.append(f"[{check.id}] 채점기준표에 없습니다.")
        # rubric.pdf는 build 단계에서 checks로부터 재생성되므로 별도 LLM 문구의
        # 표현(예: true/활성/Enabled)이 달라도 canonical expected를 사용한다.
        if check.id not in draft.grading_script:
            errors.append(f"[{check.id}] 채점 스크립트에 ID가 없습니다.")
        if re.search(r"\s", check.script_check):
            command_hint = " ".join(check.script_check.split()[:2]).lower()
            implemented = command_hint in draft.grading_script.lower() or check.id in draft.grading_script
        else:
            implemented = bool(re.search(rf"{re.escape(check.script_check)}", draft.grading_script))
        if not implemented:
            errors.append(f"[{check.id}] scriptCheck가 채점 스크립트에 없습니다: {check.script_check}")
    rubric_ids = set(CHECK_ID.findall(draft.rubric_markdown))
    script_ids = set(CHECK_ID.findall(draft.grading_script))
    unknown_rubric = rubric_ids - set(ids)
    unknown_script = script_ids - set(ids)
    if unknown_rubric: errors.append(f"채점기준표에 checks 외 ID가 있습니다: {sorted(unknown_rubric)}")
    if unknown_script: errors.append(f"채점 스크립트에 checks 외 ID가 있습니다: {sorted(unknown_script)}")
    if draft.document:
        for module in draft.document.modules:
            module_checks = [c for c in checks if c.module_id == module.id or (not c.module_id and c.module == module.title)]
            if not module_checks: errors.append(f"module에 연결된 check가 없습니다: {module.title}")
    return errors
