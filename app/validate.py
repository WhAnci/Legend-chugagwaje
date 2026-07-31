import re, subprocess, tempfile
from pathlib import Path
from .models import TaskDraft, ValidationResult
from .consistency import check_consistency
from .module_decomposition import detect, official_service

SECRET = re.compile(r"(AKIA[0-9A-Z]{16}|aws_secret_access_key|BEGIN PRIVATE KEY|discord\.com/api/webhooks)", re.I)

def validate(draft: TaskDraft) -> ValidationResult:
    errors, warnings = [], []
    structured_text = draft.document.model_dump_json() if draft.document else ""
    assignment_source = draft.assignment_markdown or structured_text
    all_text = "\n".join([assignment_source, draft.rubric_markdown, draft.grading_script] + [f.content for f in draft.deployment_files])
    if SECRET.search(all_text): errors.append("비밀정보 또는 자격증명 패턴이 포함되었습니다.")
    if not assignment_source.strip(): errors.append("과제지 내용이 비어 있습니다.")
    if draft.document and re.search(r"cloudshell", assignment_source, re.I):
        errors.append("CloudShell은 채점 스크립트 전용이며 과제 본문에 Client/구현 환경으로 포함할 수 없습니다.")
    if not draft.rubric_markdown.strip(): errors.append("채점기준표 내용이 비어 있습니다.")
    if not draft.document: errors.append("구조화된 document 객체가 없습니다.")
    if draft.document and not draft.document.modules: errors.append("구조화된 과제 문서에 modules가 없습니다.")
    if draft.checks:
        errors.extend(check_consistency(draft))
    else:
        warnings.append("공통 grading checks가 없어 산출물 간 정합성을 완전히 검증할 수 없습니다.")
    if draft.document:
        modules = draft.document.modules
        numbers = [m.number for m in modules]
        if numbers != list(range(1, len(numbers) + 1)): errors.append("module 번호가 1부터 연속되지 않습니다.")
        seen_services = set()
        strict_modules = bool(draft.checks)
        for module in modules:
            if strict_modules and (not module.description.strip() or not (module.fixed_specs or module.specs)):
                errors.append(f"module에 description 또는 fixedSpec이 없습니다: {module.title}")
            expected_service = official_service(module.service or module.title)
            if expected_service != module.title and module.title not in {"Security Group", "IAM Role"}:
                errors.append(f"module.service와 title이 일치하지 않습니다: {module.service} / {module.title}")
            service_key = expected_service.lower()
            if service_key in seen_services and service_key not in {"security group", "iam role"}:
                errors.append(f"동일 AWS 서비스 module이 중복됩니다: {module.title}")
            seen_services.add(service_key)
            labels = " ".join(str(spec.label).lower() for spec in (module.fixed_specs or module.specs))
            if "dynamodb" in service_key and re.search(r"runtime|handler|function name", labels):
                errors.append("DynamoDB module에 Lambda 설정이 섞여 있습니다.")
            if "s3" in service_key and re.search(r"runtime|handler|function name", labels):
                errors.append("S3 module에 Lambda 설정이 섞여 있습니다.")
            if strict_modules:
                forbidden = {
                    "sqs": r"runtime|handler|function name",
                    "dynamodb": r"runtime|handler|function name",
                    "sns": r"queue name",
                    "lambda": r"billing mode",
                    "api gateway": r"partition key",
                }
                for key, pattern in forbidden.items():
                    if key in service_key and re.search(pattern, labels): errors.append(f"{module.title} module에 다른 서비스 설정이 섞여 있습니다.")
            detected = detect(f"{module.title} {module.service} {module.resource_type}".lower())
            if len(detected) >= 2:
                errors.append(f"여러 핵심 서비스가 하나의 module에 합쳐져 있습니다: {module.title}")
            if module.title.strip().lower() in {"인프라 구성", "전체 구성", "시스템 구축", "핵심 서비스 설정"}:
                errors.append("module 제목은 구체적인 AWS 서비스/리소스명을 포함해야 합니다.")
    criterion_ids = {int(x) for x in re.findall(r"C-(\d+)", draft.rubric_markdown)}
    if draft.checks:
        if len(draft.checks) < 5: errors.append("공통 checks가 부족합니다(C-01부터 최소 C-05 필요).")
        if any(check.score > 1.5 for check in draft.checks): errors.append("checks 배점이 1.5점을 초과합니다.")
    elif len(criterion_ids) < 5: errors.append("채점 대항목이 부족합니다(C-01부터 최소 C-05 필요).")
    forbidden_assignment_headings = re.findall(r"(?im)^#{1,3}\s*[^\n]*(?:표지|리소스\s*사양|제출물|제한사항|비용|리소스\s*정리|세부\s*요구사항)", draft.assignment_markdown)
    if forbidden_assignment_headings:
        errors.append("과제지에 제외 대상 섹션(표지/리소스 사양/제출물/비용·제한사항/정리)이 포함되었습니다.")
    if re.search(r"(?m)^\s*\|.*\|\s*$", draft.assignment_markdown):
        errors.append("과제지에는 Markdown 표를 사용하지 않습니다. 서비스 항목과 Name : value 형식으로 작성하세요.")
    simple_markers = ["VPC", "서브넷", "EC2", "User Data", "SSM"]
    if sum(marker.lower() in all_text.lower() for marker in simple_markers) >= 5 and not any(x in all_text for x in ["ALB", "ASG", "CloudWatch", "EventBridge", "SQS", "DynamoDB"]):
        errors.append("VPC·EC2·User Data·SSM만 포함된 단순 과제입니다. end-to-end 구성과 동작 검증을 추가하세요.")
    if not draft.grading_script.lstrip().startswith("#!"): errors.append("grading.sh에 shebang이 없습니다.")
    if "set -Eeuo pipefail" not in draft.grading_script: errors.append("grading.sh에 set -Eeuo pipefail이 없습니다.")
    if "--dry-run" not in draft.grading_script: errors.append("grading.sh에 --dry-run이 없습니다.")
    if "--region" not in draft.grading_script: errors.append("grading.sh에 --region 옵션이 없습니다.")
    if "--output" not in draft.grading_script: errors.append("grading.sh에 --output 옵션이 없습니다.")
    if len(assignment_source) > 45000: warnings.append("과제지가 길어 PDF 7쪽을 초과할 가능성이 있습니다.")
    if len(draft.rubric_markdown) > 26000: warnings.append("채점기준표가 길어 PDF 4쪽을 초과할 가능성이 있습니다.")
    for f in draft.deployment_files:
        if f.path.startswith("/") or ".." in Path(f.path).parts: errors.append(f"허용되지 않는 배포 파일 경로: {f.path}")
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "grading.sh"; script.write_text(draft.grading_script, encoding="utf-8")
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        if result.returncode: errors.append(f"grading.sh 문법 오류: {result.stderr.strip()}")
        if subprocess.run(["which", "shellcheck"], capture_output=True).returncode == 0:
            result = subprocess.run(["shellcheck", str(script)], capture_output=True, text=True)
            if result.returncode: warnings.append(f"ShellCheck 경고: {result.stdout[:500].strip()}")
    rubric = draft.rubric_markdown
    score_lines = [line for line in rubric.splitlines() if not re.search(r"총점|합계|total", line, re.I)]
    numbers = [float(x) for x in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)\s*점", "\n".join(score_lines))]
    if not draft.checks and numbers and max(numbers) > 1.5: errors.append("채점 항목 배점이 1.5점을 초과합니다.")
    if not re.search(r"(?:총점|합계|total).{0,20}6(?:\.0)?\s*점", rubric, re.I | re.S):
        warnings.append("채점기준표의 모듈 총점 6.0점 표기를 확인하세요.")
    if len(re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+[^\n]+", rubric)) > 14: warnings.append("채점표 항목 수를 확인하세요(최대 7개 권장).")
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)
