"""Pre-generation assignment blueprint and deterministic structural review."""
import json, re
from pydantic import BaseModel, ConfigDict, Field
from .models import TaskDraft, TaskRequest

def _camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)

class BlueprintModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)

class BlueprintComponent(BlueprintModel):
    id: str
    service: str
    resource_type: str
    role: str
    required: bool = True
    configured_by_candidate: bool = True

class BlueprintModule(BlueprintModel):
    id: str
    title: str
    component_ids: list[str] = Field(default_factory=list)

class BlueprintFile(BlueprintModel):
    path: str
    used_by_module: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

class DataFlow(BlueprintModel):
    from_node: str
    to_node: str
    action: str

class AssignmentBlueprint(BlueprintModel):
    goal: str
    data_flow: list[DataFlow] = Field(default_factory=list)
    components: list[BlueprintComponent] = Field(default_factory=list)
    logical_modules: list[BlueprintModule] = Field(default_factory=list)
    provided_files: list[BlueprintFile] = Field(default_factory=list)
    fixed_specs: list[dict] = Field(default_factory=list)
    behavior_checks: list[dict] = Field(default_factory=list)
    dependencies: list[dict] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

SERVICE_HINTS = [
    ("AWS Lambda", ("lambda", "함수"), "Function", "실행 코드"),
    ("Amazon S3", ("s3", "버킷"), "Bucket", "객체 저장"),
    ("AWS KMS", ("kms", "암호화 키"), "KMS Key", "암호화"),
    ("Amazon SNS", ("sns", "topic", "알림"), "Topic", "알림 발행"),
    ("Amazon SQS", ("sqs", "queue", "큐"), "Queue", "메시지 버퍼"),
    ("Amazon API Gateway", ("api gateway",), "API", "요청 수집"),
    ("Amazon Route 53", ("route 53", "route53", "failover", "dns"), "DNS", "DNS 장애 전환"),
    ("Amazon VPC", ("vpc", "subnet", "서브넷"), "VPC", "네트워크 기반"),
]

def create_blueprint(req: TaskRequest) -> AssignmentBlueprint:
    text = f"{req.raw} {req.service} {req.analysis}".lower()
    components = []
    for service, hints, resource, role in SERVICE_HINTS:
        if any(hint in text for hint in hints):
            slug = re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-")
            components.append(BlueprintComponent(id=slug, service=service, resource_type=resource, role=role))
    if "kms" in text or "sse-kms" in text:
        components.append(BlueprintComponent(id="kms-key", service="AWS KMS", resource_type="KMS Key", role="암호화 키")) if not any(c.service == "AWS KMS" for c in components) else None
    modules = [BlueprintModule(id=c.id, title=c.service, component_ids=[c.id]) for c in components]
    files = []
    if "lambda_function.py" in text or "lambda" in text or "함수" in text:
        files.append(BlueprintFile(path="lambda_function.py", used_by_module=["aws-lambda"], dependencies=["Function", "Runtime", "Handler", "Execution Role"]))
    flows = []
    for left, right, action in re.findall(r"([A-Za-z0-9가-힣 ]+)\s*(?:→|->)\s*([A-Za-z0-9가-힣 ]+)(?:\s*[:：]\s*([^,\n]+))?", req.raw):
        flows.append(DataFlow(from_node=left.strip(), to_node=right.strip(), action=(action or "request/data flow").strip()))
    if not flows and len(components) >= 2:
        flows = [DataFlow(from_node=components[i].service, to_node=components[i + 1].service, action="configured integration") for i in range(len(components) - 1)]
    return AssignmentBlueprint(goal=req.raw.strip(), data_flow=flows, components=components, logical_modules=modules, provided_files=files, risks=["권한·정책·실제 동작 검증 누락", "지급파일과 배포 리소스 불일치"])

def validate_blueprint(blueprint: AssignmentBlueprint) -> list[str]:
    errors = []
    component_ids = {c.id for c in blueprint.components}
    mapped = {cid for module in blueprint.logical_modules for cid in module.component_ids}
    missing = component_ids - mapped
    if missing: errors.append(f"Blueprint 구성요소가 module에 매핑되지 않았습니다: {sorted(missing)}")
    if not blueprint.goal.strip(): errors.append("Blueprint goal이 비어 있습니다.")
    if len(blueprint.components) >= 2 and not blueprint.data_flow: errors.append("Blueprint dataFlow가 없습니다.")
    for file in blueprint.provided_files:
        if not file.used_by_module: errors.append(f"지급파일 사용 module이 없습니다: {file.path}")
        if file.path.endswith("lambda_function.py") and not any(c.service == "AWS Lambda" for c in blueprint.components): errors.append("Lambda 지급파일이 있지만 Lambda component가 없습니다.")
    return errors

def review_generated_draft(blueprint: AssignmentBlueprint, draft: TaskDraft) -> list[str]:
    errors = []
    if not draft.document: return ["Blueprint Reviewer: document가 없습니다."]
    modules = draft.document.modules
    corpus = " ".join(m.model_dump_json() for m in modules).lower()
    architecture = f"{draft.document.architecture} {draft.document.overview} {' '.join(draft.document.requirements)}".lower()
    for component in blueprint.components:
        aliases = [component.service.lower(), component.resource_type.lower(), component.role.lower()]
        if any(alias in architecture for alias in aliases) and not any(alias in corpus for alias in aliases):
            errors.append(f"아키텍처 구성요소가 module에 없습니다: {component.service}")
    for file in blueprint.provided_files:
        matching_files = [f for f in draft.deployment_files if f.path.lower() == file.path.lower()]
        if matching_files and not any("lambda" in m.service.lower() or "function" in m.title.lower() for m in modules):
            errors.append(f"지급파일 배포 대상 module이 없습니다: {file.path}")
        for deployment in matching_files:
            code = deployment.content
            env_names = set(re.findall(r"os\.environ(?:\.get)?(?:\(|\[)\s*[\"']([A-Z][A-Z0-9_]+)", code))
            module_text = " ".join(m.model_dump_json() for m in modules).lower()
            for env_name in env_names:
                if env_name.lower() not in module_text:
                    errors.append(f"지급파일 환경 변수가 module에 정의되지 않았습니다: {env_name}")
            operations = {
                "s3.get_object": ("Amazon S3", "GetObject"), "s3.copy_object": ("Amazon S3", "PutObject"),
                "s3.delete_object": ("Amazon S3", "DeleteObject"), "sns.publish": ("Amazon SNS", "Publish"),
                "kms.encrypt": ("AWS KMS", "Encrypt"), "kms.generate_data_key": ("AWS KMS", "GenerateDataKey")
            }
            for operation, (service, permission) in operations.items():
                if operation in code.lower() and (service.lower() not in module_text or permission.lower() not in module_text):
                    errors.append(f"지급파일 의존성이 명세에 없습니다: {operation} → {permission}")
    forbidden = re.compile(r"status ?code|body ?contain|exit ?code|failover success|subscription ?count", re.I)
    for module in modules:
        if any(forbidden.search(str(spec.label)) for spec in (module.fixed_specs or module.specs)):
            errors.append(f"검증 조건이 fixedSpec에 포함되었습니다: {module.title}")
    if len(blueprint.components) >= 2 and not draft.checks: errors.append("Blueprint를 검증할 gradingSpec/checks가 없습니다.")
    return errors

def architect_prompt(blueprint: AssignmentBlueprint) -> str:
    return """[Architect 단계]
과제 JSON을 아직 작성하지 말고 아래 Blueprint를 기준으로 목표, 데이터 흐름, 구성요소, 논리 module, 지급파일 의존성, IAM/이벤트 연결, fixedSpec과 behaviorCheck를 먼저 설계하라. 모든 필수 구성요소는 module 또는 includedResources에 매핑해야 한다.
""" + json.dumps(blueprint.model_dump(by_alias=True), ensure_ascii=False)

def reviewer_prompt(blueprint: AssignmentBlueprint) -> str:
    return """[Reviewer 단계]
아래 Blueprint를 Reviewer 관점에서 검사하라. 누락된 실행 리소스, 지급파일 사용처, 환경 변수, 이벤트/권한/정책, end-to-end 단계, fixedSpec에 섞인 검증 조건을 찾아 오류 목록으로 반환하라. PASS가 아니면 Writer 단계로 진행하지 않는다.
""" + json.dumps(blueprint.model_dump(by_alias=True), ensure_ascii=False)

def blueprint_prompt(blueprint: AssignmentBlueprint) -> str:
    return architect_prompt(blueprint) + "\n\n" + reviewer_prompt(blueprint)
