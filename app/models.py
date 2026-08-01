import json, re
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .module_decomposition import decompose_document, official_service

def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)

class StructuredModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

class TaskRequest(BaseModel):
    raw: str
    service: str = "자동 선정"
    difficulty: str = "중급"
    duration_minutes: int = 60
    region: str = ""
    iac: str = "필요 시 선택"
    analysis: str = ""
    previous_draft: str = ""
    approved_blueprint: str = ""

class DeploymentFile(StructuredModel):
    path: str
    content: str
    used_by_module: list[str] = Field(default_factory=list)

class GradingCheck(StructuredModel):
    id: str
    module: str = ""
    module_id: str = ""
    label: str
    requirement: str = ""
    behavior_expectation: str = ""
    expected: dict = Field(default_factory=dict)
    score: float
    required: bool = True
    script_check: str

class SpecItem(StructuredModel):
    label: str
    value: str | list[str]

class TaskSection(StructuredModel):
    number: int
    title: str
    description: str = ""
    tasks: list[str] = Field(default_factory=list)
    specs: list[SpecItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)

class TaskModule(StructuredModel):
    id: str = ""
    number: int = 1
    title: str
    subtitle: str = ""
    primary_service: str = ""
    role: str = ""
    included_resources: list[str] = Field(default_factory=list)
    service: str = ""
    resource_type: str = ""
    description: str = ""
    dependencies: list[dict] = Field(default_factory=list)
    provided_files: list[str | dict] = Field(default_factory=list)
    specs: list[SpecItem] = Field(default_factory=list)
    fixed_specs: list[SpecItem] = Field(default_factory=list)
    inferred_constraints: list[str] = Field(default_factory=list)
    scenario: str = ""
    region_notice: str = ""
    architecture_flow: str = ""
    deployments: list[dict] = Field(default_factory=list)
    sections: list[TaskSection] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    cleanup: list[str] = Field(default_factory=list)

class TaskMeta(StructuredModel):
    document_title: str = ""
    year: int | str = ""
    occupation: str = ""
    title: str = ""
    assignment_number: str = ""
    duration: str = ""
    region: str = ""
    candidate_number: str = ""
    judge_confirmation: str = ""
    mock: bool = False

class ProvidedFile(StructuredModel):
    name: str
    description: str = ""
    used_by_module: list[str] = Field(default_factory=list)

class TaskDocument(StructuredModel):
    meta: TaskMeta
    overview: str = ""
    architecture: str = ""
    requirements: list[str] = Field(default_factory=list)
    precautions: list[str] = Field(default_factory=list)
    provided_files: list[ProvidedFile] = Field(default_factory=list)
    modules: list[TaskModule] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    cleanup: list[str] = Field(default_factory=list)
    footer: str = ""

class TaskDraft(BaseModel):
    title: str = "AWS 추가과제"
    summary: str = ""
    assignment_markdown: str = ""
    rubric_markdown: str = ""
    grading_script: str = "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
    deployment_files: list[DeploymentFile] = Field(default_factory=list)
    notes: str = ""
    document: TaskDocument | None = None
    checks: list[GradingCheck] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_llm_shapes(cls, data):
        if not isinstance(data, dict): return data
        data = dict(data)
        script = data.get("grading_script")
        if isinstance(script, dict):
            data["grading_script"] = script.get("content", script.get("text", ""))
        files = data.get("deployment_files")
        if isinstance(files, dict): data["deployment_files"] = [files]
        doc = data.get("document")
        if isinstance(doc, dict):
            doc = dict(doc)
            meta = doc.get("meta")
            if isinstance(meta, dict):
                meta = dict(meta)
                for key in ("assignment_number", "assignmentNumber", "candidate_number", "candidateNumber"):
                    if key in meta: meta[key] = str(meta[key])
                doc["meta"] = meta
            if isinstance(doc.get("modules"), dict): doc["modules"] = [doc["modules"]]
            if isinstance(doc.get("provided_files"), dict): doc["provided_files"] = [doc["provided_files"]]
            def as_list(value):
                if value is None: return []
                return value if isinstance(value, list) else [value]
            def normalize_specs(value):
                values = as_list(value)
                result = []
                for item in values:
                    if isinstance(item, dict):
                        result.append(item)
                    elif isinstance(item, str) and ":" in item:
                        label, raw_value = item.split(":", 1)
                        result.append({"label": label.strip(), "value": raw_value.strip()})
                return result
            for module in doc.get("modules", []):
                if isinstance(module, dict):
                    if "specs" in module: module["specs"] = normalize_specs(module["specs"])
                    if "fixedSpecs" in module: module["fixedSpecs"] = normalize_specs(module["fixedSpecs"])
                    if "fixed_specs" in module: module["fixed_specs"] = normalize_specs(module["fixed_specs"])
                    if "inferredConstraints" in module: module["inferredConstraints"] = as_list(module["inferredConstraints"])
                    if "inferred_constraints" in module: module["inferred_constraints"] = as_list(module["inferred_constraints"])
                    if "dependencies" in module:
                        module["dependencies"] = [item if isinstance(item, dict) else {"type": "dependsOn", "target": str(item)} for item in as_list(module["dependencies"])]
                    provided_key = "providedFiles" if "providedFiles" in module else "provided_files" if "provided_files" in module else None
                    if provided_key:
                        module[provided_key] = [item if isinstance(item, dict) else str(item) for item in as_list(module[provided_key])]
                    if isinstance(module.get("sections"), dict): module["sections"] = [module["sections"]]
                    for key in ("verification", "cleanup"):
                        if key in module: module[key] = as_list(module[key])
                    for spec in module.get("specs", []) + module.get("fixedSpecs", []) + module.get("fixed_specs", []):
                        if isinstance(spec, dict):
                            if "label" in spec: spec["label"] = str(spec["label"])
                            if "value" in spec and not isinstance(spec["value"], list): spec["value"] = str(spec["value"])
                            if isinstance(spec.get("value"), list): spec["value"] = [str(x) for x in spec["value"]]
                    for section in module.get("sections", []):
                        if isinstance(section, dict):
                            for key in ("tasks", "notes", "verification"):
                                if key in section: section[key] = as_list(section[key])
                            if "specs" in section: section["specs"] = normalize_specs(section["specs"])
                            for spec in section.get("specs", []):
                                if isinstance(spec, dict):
                                    if "label" in spec: spec["label"] = str(spec["label"])
                                    if "value" in spec and not isinstance(spec["value"], list): spec["value"] = str(spec["value"])
                                    if isinstance(spec.get("value"), list): spec["value"] = [str(x) for x in spec["value"]]
            for key in ("requirements", "precautions"):
                if key in doc: doc[key] = as_list(doc[key])
            # 구형 응답이 하나의 module 안에 서비스별 sections를 넣은 경우
            # 각 section을 독립 module로 승격한다.
            if len(doc.get("modules", [])) == 1 and isinstance(doc["modules"][0], dict):
                parent = doc["modules"][0]
                sections = parent.get("sections", [])
                if len(sections) >= 2:
                    promoted = []
                    for index, section in enumerate(sections, 1):
                        if not isinstance(section, dict): continue
                        promoted.append({
                            "number": index,
                            "title": section.get("title", f"서비스 {index}"),
                            "service": section.get("service", section.get("title", "")),
                            "description": section.get("description", ""),
                            "specs": section.get("specs", []),
                        })
                    if len(promoted) >= 2: doc["modules"] = promoted
            doc = decompose_document(doc)
            used_ids = set()
            for module in doc.get("modules", []):
                if not isinstance(module, dict): continue
                raw_title = str(module.get("title", "")).strip()
                service = official_service(module.get("primaryService", "") or module.get("service", "") or raw_title)
                module["service"] = service
                module["primaryService"] = module.get("primaryService") or service
                # Logical titles such as "Amazon EC2 애플리케이션" are retained when they still identify the primary service.
                title_service = official_service(raw_title)
                logical_title = raw_title if raw_title and title_service == service else service
                if not raw_title or raw_title.lower() != logical_title.lower(): module["subtitle"] = module.get("subtitle") or (raw_title if raw_title != logical_title else "")
                module["title"] = logical_title
            for index, module in enumerate(doc.get("modules", []), 1):
                if not isinstance(module, dict): continue
                base = re.sub(r"[^a-z0-9]+", "-", str(module.get("service", "") or module.get("title", "")).lower()).strip("-") or f"module-{index}"
                module_id = base
                suffix = 2
                while module_id in used_ids:
                    module_id = f"{base}-{suffix}"; suffix += 1
                module["id"] = module_id
                module["number"] = index
                used_ids.add(module_id)
            seen_descriptions = set()
            for module in doc.get("modules", []):
                if not isinstance(module, dict): continue
                description = str(module.get("description", "")).strip()
                if description and description in seen_descriptions:
                    module["description"] = f"{module.get('title', '이 module')}의 역할과 최종 상태를 구성합니다."
                if description: seen_descriptions.add(description)
            def sanitize_assignment_text(value):
                if isinstance(value, str):
                    return re.sub(r"cloud\s*shell", "채점 실행 환경", value, flags=re.I)
                if isinstance(value, list): return [sanitize_assignment_text(item) for item in value]
                if isinstance(value, dict): return {key: sanitize_assignment_text(item) for key, item in value.items()}
                return value
            doc = sanitize_assignment_text(doc)
            data["document"] = doc
        checks = data.get("checks")
        if isinstance(checks, dict): checks = [checks]
        if isinstance(checks, list) and isinstance(data.get("document"), dict):
            module_items = [module for module in data["document"].get("modules", []) if isinstance(module, dict)]
            titles = [str(module.get("title", "")) for module in module_items]
            modules_by_id = {str(module.get("id", "")): module for module in module_items}
            for check in checks:
                if not isinstance(check, dict): continue
                module_id = str(check.get("moduleId", check.get("module_id", "")))
                if module_id in modules_by_id:
                    check["module"] = modules_by_id[module_id].get("title", "")
                    continue
                # 모델이 moduleId 자리에 서비스명/화면 제목을 잘못 넣어도
                # 배열 인덱스로 취급하지 않고 module 후보명으로 재매칭한다.
                module_name = str(check.get("module", "")) or ("" if module_id.isdigit() else module_id)
                if module_name.isdigit():
                    expected = check.get("expected", {}) or {}
                    module_name = " ".join([str(x) for x in expected.keys()] + [str(x) for x in expected.values()])
                if module_name in titles or not titles:
                    target = next((module for module in module_items if module.get("title") == module_name), None)
                    if target: check["moduleId"] = target.get("id", "")
                    continue
                def canonical_service(value):
                    value = value.lower()
                    aliases = {
                        "aws systems manager": "ssm", "amazon systems manager": "ssm", "systems manager": "ssm",
                        "cloudwatch logs": "cloudwatch", "amazon cloudwatch": "cloudwatch",
                        "amazon rds": "rds", "relational database service": "rds",
                        "amazon s3": "s3", "simple storage service": "s3",
                        "amazon sqs": "sqs", "simple queue service": "sqs",
                        "amazon sns": "sns", "simple notification service": "sns",
                        "amazon ecs": "ecs", "elastic container service": "ecs",
                        "amazon ecr": "ecr", "elastic container registry": "ecr",
                        "amazon vpc": "vpc", "amazon api gateway": "api gateway",
                        "aws waf": "waf", "web application firewall": "waf",
                        "amazon cloudfront": "cloudfront", "cloudfront functions": "cloudfront function",
                        "aws backup": "backup", "amazon cognito": "cognito", "user pool": "cognito",
                        "eventbridge pipes": "eventbridge pipes", "amazon eventbridge": "eventbridge",
                        "step functions": "step functions", "aws kms": "kms", "secrets manager": "secrets manager",
                        "application load balancer": "alb", "amazon alb": "alb", "load balancer": "alb", "load-balancer": "alb", "elb": "alb", "elastic load balancing": "alb", "route 53": "route53", "route53": "route53",
                        "vpc lattice": "vpc lattice", "amazon opensearch": "opensearch", "amazon ec2": "ec2", "elastic compute cloud": "ec2",
                        "람다": "lambda", "다이나모디비": "dynamodb", "다이너모디비": "dynamodb",
                        "에스큐에스": "sqs", "에이피아이 게이트웨이": "api gateway", "클라우드프론트": "cloudfront",
                        "웹 애플리케이션 방화벽": "waf", "이벤트브리지": "eventbridge", "백업 볼트": "backup"
                    }
                    for source, target in aliases.items(): value = value.replace(source, target)
                    return value
                expected_normalized = canonical_service(module_name)
                expected_tokens = set(re.findall(r"[a-z0-9가-힣]+", expected_normalized)) - {"구성", "설정", "구현", "처리", "서비스", "함수"}
                module_by_title = {str(module.get("title", "")): module for module in module_items}
                def similarity(title):
                    title_normalized = canonical_service(title + " " + json.dumps(module_by_title.get(title, {}), ensure_ascii=False))
                    title_tokens = set(re.findall(r"[a-z0-9가-힣]+", title_normalized)) - {"구성", "설정", "구현", "처리", "서비스", "함수"}
                    compact_a = re.sub(r"[^a-z0-9가-힣]", "", expected_normalized)
                    compact_b = re.sub(r"[^a-z0-9가-힣]", "", title_normalized)
                    compact_match = bool(compact_a) and (compact_a in compact_b or compact_b in compact_a)
                    return (2 if compact_match else 0) + len(expected_tokens & title_tokens)
                ranked = sorted(titles, key=similarity, reverse=True)
                if ranked and similarity(ranked[0]) > 0:
                    check["module"] = ranked[0]
                    target = module_by_title.get(ranked[0], {})
                    check["moduleId"] = target.get("id", "")
            # 모델이 checks를 5개 미만으로 반환해도 module의 최종 specs에서
            # 누락된 자동검사 항목을 보완한다.
            if len(checks) < 5:
                modules = [module for module in data["document"].get("modules", []) if isinstance(module, dict)]
                candidates = []
                for module in modules:
                    for spec in (module.get("fixedSpecs", []) or module.get("specs", []) or []):
                        if isinstance(spec, dict): candidates.append((module, spec))
                if not candidates: candidates = [(module, {}) for module in modules]
                index = 0
                while len(checks) < 5 and candidates:
                    module, spec = candidates[index % len(candidates)]
                    index += 1
                    check_id = f"AUTO-{len(checks) + 1:02d}"
                    script_check = f"check_auto_{len(checks) + 1:02d}"
                    checks.append({"id": check_id, "moduleId": module.get("id", ""), "module": module.get("title", ""), "label": spec.get("label", "자동 검사"), "requirement": "최종 설정값을 확인합니다.", "expected": {str(spec.get("label", "value")): spec.get("value", "")}, "score": 1.0, "required": True, "scriptCheck": script_check})
                    data["rubric_markdown"] = str(data.get("rubric_markdown", "")) + f"\n[{check_id}] {spec.get('label', '자동 검사')} 1.0점"
                    data["grading_script"] = str(data.get("grading_script", "")) + f"\n# [{check_id}]\n{script_check}() {{ :; }}\n"
            scores = [float(check.get("score", 0) or 0) for check in checks if isinstance(check, dict)]
            if scores and (max(scores) > 1.5 or round(sum(scores), 2) != 6.0):
                valid = [check for check in checks if isinstance(check, dict)]
                for check in valid: check["score"] = min(1.5, max(0.0, float(check.get("score", 0) or 0)))
                while round(sum(float(x.get("score", 0)) for x in valid), 2) < 6.0:
                    changed = False
                    for check in valid:
                        if float(check.get("score", 0)) < 1.5:
                            check["score"] = min(1.5, float(check.get("score", 0)) + 0.25); changed = True
                            if round(sum(float(x.get("score", 0)) for x in valid), 2) >= 6.0: break
                    if not changed: break
                while round(sum(float(x.get("score", 0)) for x in valid), 2) > 6.0:
                    changed = False
                    for check in reversed(valid):
                        if float(check.get("score", 0)) >= 0.25:
                            check["score"] = max(0.0, float(check.get("score", 0)) - 0.25); changed = True
                            if round(sum(float(x.get("score", 0)) for x in valid), 2) <= 6.0: break
                    if not changed: break
            # 모든 module은 최소 하나의 check를 가져야 한다. 모델이 특정 module의
            # check를 빠뜨리면 해당 module의 첫 specs에서 자동 생성한다.
            existing_modules = {str(check.get("module", "")) for check in checks if isinstance(check, dict)}
            for module in [m for m in data["document"].get("modules", []) if isinstance(m, dict)]:
                title = str(module.get("title", ""))
                if not title or title in existing_modules: continue
                spec = next((s for s in module.get("specs", []) if isinstance(s, dict)), {})
                number = len(checks) + 1
                check_id = f"AUTO-{number:02d}"
                script_check = f"check_auto_{number:02d}"
                checks.append({"id": check_id, "moduleId": module.get("id", ""), "module": title, "label": spec.get("label", "module 존재"), "requirement": "module의 최종 구성을 확인합니다.", "expected": ({str(spec.get("label")): spec.get("value")} if spec else {}), "score": 0.5, "required": True, "scriptCheck": script_check})
                data["rubric_markdown"] = str(data.get("rubric_markdown", "")) + f"\n[{check_id}] {title} 0.5점"
                data["grading_script"] = str(data.get("grading_script", "")) + f"\n# [{check_id}]\n{script_check}() {{ :; }}\n"
                existing_modules.add(title)
            # 자동 추가 후에도 총점 6.0을 유지한다.
            total_score = sum(float(check.get("score", 0) or 0) for check in checks if isinstance(check, dict))
            if checks and total_score and round(total_score, 2) != 6.0:
                factor = 6.0 / total_score
                for check in checks: check["score"] = min(1.5, round(float(check.get("score", 0) or 0) * factor, 2))
                difference = round(6.0 - sum(float(check.get("score", 0) or 0) for check in checks), 2)
                for check in checks:
                    if abs(difference) < 0.01: break
                    step = min(0.01, difference) if difference > 0 else max(-0.01, difference)
                    if difference > 0 and float(check.get("score", 0)) < 1.5 or difference < 0 and float(check.get("score", 0)) >= 0.01:
                        check["score"] = round(float(check.get("score", 0)) + step, 2); difference = round(difference - step, 2)
            # checks가 요구하는 고정값이 과제 module에 빠진 경우, 전체 재생성 대신
            # 해당 module의 최종 상태 명세에 자동 보완한다.
            if checks:
                module_by_title = {str(module.get("title", "")): module for module in data["document"].get("modules", []) if isinstance(module, dict)}
                for check in checks:
                    target = module_by_title.get(str(check.get("module", "")))
                    if not target: continue
                    specs = target.setdefault("fixedSpecs", [])
                    existing = json.dumps(specs, ensure_ascii=False).lower()
                    for key, value in (check.get("expected", {}) or {}).items():
                        if str(key).lower() in {"status", "dbinstancestatus", "state", "health", "health_status", "availability"}: continue
                        if isinstance(value, (str, int, float, bool)) and str(value).lower() not in existing:
                            specs.append({"label": str(key).replace("_", " ").title(), "value": str(value)})
                            existing = json.dumps(specs, ensure_ascii=False).lower()
            for module in data["document"].get("modules", []):
                if not isinstance(module, dict): continue
                constraints = module.get("inferredConstraints", module.get("inferred_constraints", [])) or []
                if constraints:
                    description = str(module.get("description", ""))
                    additions = " ".join(str(item).strip() for item in constraints if str(item).strip() and str(item).strip() not in description)
                    if additions: module["description"] = (description + " " + additions).strip()
            # 자동 보정/생성된 모든 check ID는 최종 rubric과 script에도 남긴다.
            rubric_text = str(data.get("rubric_markdown", ""))
            script_text = str(data.get("grading_script", ""))
            for check in checks:
                check_id = str(check.get("id", ""))
                if check_id and check_id not in rubric_text:
                    rubric_text += f"\n[{check_id}] {check.get('label', '검사')} {float(check.get('score', 0) or 0):g}점"
                if check_id and check_id not in script_text:
                    script_text += f"\n# [{check_id}]\n{check.get('scriptCheck', 'check_' + check_id.lower().replace('-', '_'))}() {{ :; }}\n"
            data["rubric_markdown"] = rubric_text
            data["grading_script"] = script_text
            data["checks"] = checks
        if isinstance(data.get("assignment_markdown"), str):
            data["assignment_markdown"] = re.sub(r"cloud\s*shell", "채점 실행 환경", data["assignment_markdown"], flags=re.I)
        return data

def align_modules_to_approved_blueprint(draft: TaskDraft, approved_blueprint) -> TaskDraft:
    """Preserve approved module IDs while allowing human-friendly generated titles."""
    if not draft.document or not approved_blueprint: return draft
    approved = {}
    for module in approved_blueprint.logical_modules:
        approved[official_service(module.title).strip().casefold()] = (module.id, module.title)
    for module in draft.document.modules:
        key = official_service(module.primary_service or module.service or module.title or module.id).strip().casefold()
        if key in approved:
            module.id, official_title = approved[key]
            module.service = official_title
            module.primary_service = official_title
    return draft

class ValidationResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
