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
    included_resources: list[str] = Field(default_factory=list)
    fixed_specs: list[dict] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

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

SERVICE_ALIASES = {
    "Amazon ECR": ("ecr", "elastic container registry", "container registry"),
    "Amazon EventBridge": ("eventbridge", "event bus", "event rule"),
    "Amazon Route 53": ("route 53", "route53", "dns", "hosted zone", "failover"),
    "Amazon VPC": ("vpc", "subnet", "route table", "internet gateway", "nat gateway"),
    "Amazon EC2": ("ec2", "elastic compute cloud", "instance"),
    "Application Load Balancer": ("alb", "application load balancer", "target group", "listener"),
    "Amazon DynamoDB": ("dynamodb", "dynamo db", "partition key", "table"),
    "Amazon SQS": ("sqs", "queue", "visibility timeout", "dead letter"),
    "Amazon SNS": ("sns", "topic", "subscription", "publish"),
    "AWS KMS": ("kms", "key policy", "customer managed key", "sse-kms"),
    "AWS CloudTrail": ("cloudtrail", "감사 로그", "접근 제어 감사"),
    "AWS Network Firewall": ("network firewall", "firewall policy", "stateful rule group", "firewall endpoint"),
    "AWS Config": ("aws config", "config rule", "구성 감사"),
    "AWS Secrets Manager": ("secrets manager", "secret name", "getsecretvalue"),
    "Amazon CloudWatch": ("cloudwatch", "log group", "alarm", "metric"),
    "AWS IAM": ("iam", "execution role", "trust policy", "allowed actions"),
}

SERVICE_HINTS = [
    ("AWS Lambda", ("lambda", "함수"), "Function", "실행 코드"),
    ("Amazon S3", ("s3", "버킷"), "Bucket", "객체 저장"),
    ("Amazon CloudFront", ("cloudfront", "cdn", "edge"), "Distribution", "엣지 콘텐츠 배포"),
    ("AWS WAF", ("waf", "web acl", "sqli", "xss", "지오 블로킹"), "Web ACL", "웹 공격·지역 트래픽 차단"),
    ("CloudFront Functions", ("cloudfront function", "보안 헤더", "csp", "hsts"), "Function", "엣지 요청/응답 헤더 처리"),
    ("AWS KMS", ("kms", "암호화 키"), "KMS Key", "암호화"),
    ("Amazon SNS", ("sns", "topic", "알림"), "Topic", "알림 발행"),
    ("Amazon SQS", ("sqs", "queue", "큐"), "Queue", "메시지 버퍼"),
    ("Amazon API Gateway", ("api gateway",), "API", "요청 수집"),
    ("Amazon ECR", ("ecr", "container registry", "컨테이너 이미지"), "Repository", "이미지 저장·검사"),
    ("Amazon EventBridge", ("eventbridge", "이벤트 규칙"), "Rule", "이벤트 라우팅"),
    ("AWS Network Firewall", ("network firewall",), "Firewall", "네트워크 보호"),
    ("Amazon Route 53", ("route 53", "route53", "failover", "dns"), "DNS", "DNS 장애 전환"),
    ("Amazon VPC", ("vpc", "subnet", "서브넷"), "VPC", "네트워크 기반"),
]

def create_blueprint(req: TaskRequest) -> AssignmentBlueprint:
    request_object = {}
    try: request_object = json.loads(req.raw) if req.raw.strip().startswith("{") else {}
    except Exception: request_object = {}
    text = f"{req.raw} {req.service} {req.analysis}".lower()
    # Reviewer 오류는 사용자 요구 서비스가 아니므로 phantom module을 만들지 않는다.
    text = text.split("blueprint 자동 수정 지시", 1)[0]
    components = []
    for service, hints, resource, role in SERVICE_HINTS:
        if any((re.search(rf"(?<![a-z0-9]){re.escape(hint.lower())}(?![a-z0-9])", text) if re.fullmatch(r"[a-z0-9 -]+", hint.lower()) else hint.lower() in text) for hint in hints):
            slug = re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-")
            components.append(BlueprintComponent(id=slug, service=service, resource_type=resource, role=role))
    if "kms" in text or "sse-kms" in text:
        components.append(BlueprintComponent(id="kms-key", service="AWS KMS", resource_type="KMS Key", role="암호화 키")) if not any(c.service == "AWS KMS" for c in components) else None
    # Extract services from primaryService/supportingServices/coreWork as well as the raw prose.
    # This prevents a topic JSON from collapsing to only its primary service.
    for service, aliases in SERVICE_ALIASES.items():
        if any(re.search(rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])", text) if re.fullmatch(r"[a-z0-9 -]+", alias.lower()) else alias.lower() in text for alias in aliases):
            if not any(component.service == service for component in components):
                resource, role = {"Amazon ECR": ("Repository", "이미지 저장·검사"), "Amazon EventBridge": ("Rule", "이벤트 라우팅"), "Amazon Route 53": ("DNS", "DNS 장애 전환"), "AWS IAM": ("Policy/Role", "접근 제어")}.get(service, ("Resource", "아키텍처 구성요소"))
                components.append(BlueprintComponent(id=re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-"), service=service, resource_type=resource, role=role))
    # Difficulty revision may add a directly related module without changing the original topic.
    # The additions are bounded to one or two supporting roles and are reviewed again.
    if any(token in text for token in ("난이도를 높", "난이도 높", "보안·실패", "보강")):
        existing_services = {component.service for component in components}
        additions = []
        if "Amazon CloudFront" in existing_services and "AWS WAF" not in existing_services: additions.append(("aws-waf", "AWS WAF", "Web ACL", "엣지 공격 차단"))
        if "Amazon ECR" in existing_services:
            if "Amazon EventBridge" not in existing_services: additions.append(("amazon-eventbridge", "Amazon EventBridge", "Rule", "취약점 이벤트 라우팅"))
            if "AWS Lambda" not in existing_services: additions.append(("aws-lambda", "AWS Lambda", "Function", "취약 이미지 자동 격리"))
        if "Amazon S3" in existing_services and "AWS Lambda" in existing_services and "AWS KMS" not in existing_services: additions.append(("aws-kms", "AWS KMS", "KMS Key", "민감 객체 암호화"))
        for component_id, service, resource_type, role in additions:
            components.append(BlueprintComponent(id=component_id, service=service, resource_type=resource_type, role=role))

    # Architecture closure: infer mandatory execution, event, permission and policy components
    # before showing the proposal. A primary service alone is never considered complete.
    def add_component(component_id, service, resource_type, role, module_service=None):
        existing = next((c for c in components if c.id == component_id), None)
        if not existing:
            components.append(BlueprintComponent(id=component_id, service=service, resource_type=resource_type, role=role))
        owner = module_service or service
        owner_component = next((c for c in components if c.service == owner), None)
        if owner_component:
            owner_id = owner_component.id
        else:
            owner_id = component_id
        return owner_id

    lambda_component = next((c for c in components if c.service == "AWS Lambda"), None)
    s3_component = next((c for c in components if c.service == "Amazon S3"), None)
    ecr_component = next((c for c in components if c.service == "Amazon ECR"), None)
    if lambda_component:
        add_component("lambda-execution-role", "AWS IAM", "Lambda Execution Role", "Lambda 권한과 로그 기록", "AWS Lambda")
        add_component("cloudwatch-lambda-logs", "Amazon CloudWatch", "Log Group", "Lambda 실행 로그", "AWS Lambda")
    if s3_component and lambda_component and any(x in text for x in ("event", "objectcreated", "이벤트")):
        add_component("s3-event-notification", "Amazon S3", "Event Notification", "객체 이벤트 전달", "Amazon S3")
        add_component("lambda-invoke-permission", "AWS Lambda", "Resource Permission", "S3의 Lambda 호출 허용", "AWS Lambda")
    if ecr_component and any(x in text for x in ("취약", "vulnerability", "scan", "격리", "isolate")):
        add_component("amazon-eventbridge", "Amazon EventBridge", "Rule", "ECR scan 완료 이벤트 라우팅")
        add_component("eventbridge-scan-rule", "Amazon EventBridge", "Rule", "ECR scan 완료 이벤트 라우팅", "Amazon EventBridge")
        if not lambda_component:
            lambda_component = BlueprintComponent(id="aws-lambda", service="AWS Lambda", resource_type="Function", role="취약 이미지 처리")
            components.append(lambda_component)
        add_component("lambda-execution-role", "AWS IAM", "Lambda Execution Role", "Lambda 권한과 로그 기록", "AWS Lambda")
        add_component("lambda-invoke-permission", "AWS Lambda", "Resource Permission", "EventBridge의 Lambda 호출 허용", "AWS Lambda")
    if s3_component and any(x in text for x in ("sse-kms", "kms", "암호화")):
        add_component("kms-key", "AWS KMS", "KMS Key", "S3 객체 암호화", "Amazon S3")
        add_component("kms-key-policy", "AWS KMS", "Key Policy", "S3/Lambda 암호화 권한", "Amazon S3")
    if lambda_component and any(x in text for x in ("sns", "publish", "알림")):
        add_component("sns-topic", "Amazon SNS", "Topic", "Lambda 결과 알림")
        add_component("sns-publish-permission", "AWS IAM", "IAM Policy", "Lambda SNS Publish 권한", "AWS Lambda")
    if any(c.service == "Amazon SQS" for c in components) and lambda_component:
        add_component("sqs-event-source-mapping", "AWS Lambda", "Event Source Mapping", "SQS 메시지를 Lambda로 전달", "AWS Lambda")
        add_component("sqs-dlq", "Amazon SQS", "Dead Letter Queue/RedrivePolicy", "실패 메시지 재처리", "Amazon SQS")
    if any(c.service == "Amazon SQS" for c in components) and any(c.service == "Amazon DynamoDB" for c in components):
        add_component("dynamodb-idempotency-key", "Amazon DynamoDB", "Idempotency Table/TTL", "중복 이벤트 처리 상태", "Amazon DynamoDB")
    if any(x in text for x in ("api gateway",)) and lambda_component:
        add_component("api-lambda-integration", "Amazon API Gateway", "Lambda Integration", "API 요청을 Lambda로 전달", "Amazon API Gateway")
        add_component("api-lambda-permission", "AWS Lambda", "Resource Permission", "API Gateway의 Lambda 호출 허용", "AWS Lambda")
    if any(x in text for x in ("rotation", "자동 교체", "자동교체", "secret rotation")) and any(c.service == "AWS Secrets Manager" for c in components):
        if not lambda_component:
            lambda_component = BlueprintComponent(id="aws-lambda", service="AWS Lambda", resource_type="Function", role="Secret Rotation 실행")
            components.append(lambda_component)
        add_component("rotation-schedule", "Amazon EventBridge", "Scheduler", "Secret Rotation 주기 실행")
        add_component("rotation-lambda-permission", "AWS Lambda", "Resource Permission", "Scheduler의 Rotation Lambda 호출 허용", "AWS Lambda")
        add_component("amazon-cloudwatch", "Amazon CloudWatch", "Log Group/Alarm", "Rotation 감사·실패 모니터링")
        add_component("cloudwatch-rotation-logs", "Amazon CloudWatch", "Log Group/Alarm", "Rotation 감사·실패 모니터링", "Amazon CloudWatch")
    if any(x in text for x in ("network firewall", "firewall policy", "stateful rule group", "firewall endpoint")):
        add_component("amazon-vpc", "Amazon VPC", "VPC", "방화벽 전용 서브넷과 네트워크 기반")
        add_component("network-firewall-rule-group", "AWS Network Firewall", "Stateful Rule Group", "도메인/트래픽 차단 규칙", "AWS Network Firewall")
        add_component("network-firewall-policy", "AWS Network Firewall", "Firewall Policy", "Rule Group 정책 연결", "AWS Network Firewall")
        add_component("network-firewall-endpoint", "AWS Network Firewall", "Firewall Endpoint", "전용 서브넷 방화벽 엔드포인트", "AWS Network Firewall")
        add_component("network-firewall-subnet-mapping", "AWS Network Firewall", "SubnetMapping", "방화벽 전용 서브넷 연결", "AWS Network Firewall")
        add_component("network-firewall-logging", "AWS Network Firewall", "LoggingConfiguration", "Alert/Flow 로그 전달", "AWS Network Firewall")
        add_component("vpc-routing", "Amazon VPC", "Route Table", "대칭 방화벽 경로", "Amazon VPC")
        add_component("amazon-cloudwatch", "Amazon CloudWatch", "Log Group", "Network Firewall Alert/Flow 로그")
        if any(x in text for x in ("ec2", "웹 서버", "인스턴스")):
            add_component("amazon-ec2", "Amazon EC2", "Instance", "보호 대상 워크로드")
            add_component("ec2-subnet", "Amazon EC2", "Subnet", "EC2 배치 서브넷", "Amazon EC2")
            add_component("ec2-security-group", "Amazon EC2", "Security Group", "EC2 접근 제어", "Amazon EC2")
            add_component("ec2-instance-profile", "AWS IAM", "Instance Profile", "EC2 실행 권한", "Amazon EC2")
            add_component("ec2-user-data", "Amazon EC2", "User Data", "검증용 Client/트래픽 생성", "Amazon EC2")
        if any(x in text for x in ("dynamodb", "dynamo db", "내부 db", "데이터베이스")):
            add_component("amazon-dynamodb", "Amazon DynamoDB", "Table", "보호 대상 데이터 저장")
            add_component("dynamodb-vpc-endpoint", "Amazon VPC Endpoint", "Gateway Endpoint", "격리 경로의 DynamoDB 접근", "Amazon VPC")
            add_component("dynamodb-access-policy", "AWS IAM", "IAM Policy", "DynamoDB 접근 최소 권한", "Amazon EC2")
    if any(x in text for x in ("cloudtrail", "접근 제어 감사", "감사 로그")):
        add_component("cloudtrail-trail", "AWS CloudTrail", "Trail", "API 접근 감사 기록")
        add_component("amazon-cloudwatch", "Amazon CloudWatch", "Log Group/Alarm", "감사 로그 모니터링")
        add_component("cloudwatch-audit-logs", "Amazon CloudWatch", "Log Group/Alarm", "감사 로그 모니터링", "Amazon CloudWatch")

    owned_component_ids = {"lambda-execution-role", "cloudwatch-lambda-logs", "lambda-invoke-permission", "api-lambda-integration", "api-lambda-permission", "sns-publish-permission", "rotation-lambda-permission", "s3-event-notification", "kms-key", "kms-key-policy", "sns-topic", "eventbridge-scan-rule", "cloudtrail-trail", "cloudwatch-audit-logs", "cloudwatch-rotation-logs", "network-firewall-rule-group", "network-firewall-policy", "network-firewall-endpoint", "vpc-routing", "sqs-event-source-mapping", "sqs-dlq", "dynamodb-idempotency-key", "network-firewall-subnet-mapping", "network-firewall-logging", "ec2-subnet", "ec2-security-group", "ec2-instance-profile", "ec2-user-data", "dynamodb-vpc-endpoint", "dynamodb-access-policy"}
    modules = [BlueprintModule(id=c.id, title=c.service, component_ids=[c.id]) for c in components if c.service not in {"AWS IAM"} and c.id not in owned_component_ids]
    if any(c.service == "AWS IAM" for c in components) and not any("lambda" in m.title.lower() for m in modules):
        iam_component = next(c for c in components if c.service == "AWS IAM")
        modules.append(BlueprintModule(id=iam_component.id, title="AWS IAM 및 접근 제어", component_ids=[iam_component.id]))
    # Supporting components are included in the owning logical module, not dropped or made into noise modules.
    for component in components:
        if component.service == "AWS IAM":
            owner = next((m for m in modules if "lambda" in m.title.lower()), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
            elif not owner:
                owner = next((m for m in modules if m.id == component.id or "iam" in m.title.lower()), None)
                if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id in {"lambda-execution-role", "cloudwatch-lambda-logs", "lambda-invoke-permission", "api-lambda-permission", "sns-publish-permission", "rotation-lambda-permission"}:
            owner = next((m for m in modules if "lambda" in m.title.lower()), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id == "s3-event-notification":
            owner = next((m for m in modules if m.title == "Amazon S3"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id in {"kms-key", "kms-key-policy"}:
            owner = next((m for m in modules if m.title == "AWS KMS"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id == "sns-topic":
            owner = next((m for m in modules if m.title == "Amazon SNS"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id == "kms-key-policy":
            owner = next((m for m in modules if m.title == "AWS KMS"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id in {"network-firewall-subnet-mapping", "network-firewall-logging"}:
            owner = next((m for m in modules if m.title == "AWS Network Firewall"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id in {"ec2-subnet", "ec2-security-group", "ec2-instance-profile", "ec2-user-data"}:
            owner = next((m for m in modules if m.title == "Amazon EC2"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id in {"dynamodb-vpc-endpoint", "dynamodb-access-policy"}:
            owner = next((m for m in modules if m.title == "Amazon VPC" or m.title == "Amazon EC2"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id == "sqs-event-source-mapping":
            owner = next((m for m in modules if m.title == "AWS Lambda"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id in {"sqs-dlq"}:
            owner = next((m for m in modules if m.title == "Amazon SQS"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id == "dynamodb-idempotency-key":
            owner = next((m for m in modules if m.title == "Amazon DynamoDB"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id in {"network-firewall-rule-group", "network-firewall-policy", "network-firewall-endpoint"}:
            owner = next((m for m in modules if m.title == "AWS Network Firewall"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id == "vpc-routing":
            owner = next((m for m in modules if m.title == "Amazon VPC"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id in {"cloudtrail-trail", "cloudwatch-audit-logs", "cloudwatch-rotation-logs"}:
            target_title = "AWS CloudTrail" if component.id == "cloudtrail-trail" else "Amazon CloudWatch"
            owner = next((m for m in modules if m.title == target_title), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
        elif component.id == "eventbridge-scan-rule":
            owner = next((m for m in modules if m.title == "Amazon EventBridge"), None)
            if owner and component.id not in owner.component_ids: owner.component_ids.append(component.id)
    for module in modules:
        module.included_resources = [c.resource_type for c in components if c.id in module.component_ids]
        module.dependencies = []
    files = []
    if any(c.service == "AWS Lambda" for c in components) or "lambda_function.py" in text or "lambda" in text or "함수" in text:
        files.append(BlueprintFile(path="lambda_function.py", used_by_module=["aws-lambda"], dependencies=["Function", "Runtime", "Handler", "Execution Role"]))
    flows = []
    for left, right, action in re.findall(r"([A-Za-z0-9가-힣 ]+)\s*(?:→|->)\s*([A-Za-z0-9가-힣 ]+)(?:\s*[:：]\s*([^,\n]+))?", req.raw):
        flows.append(DataFlow(from_node=left.strip(), to_node=right.strip(), action=(action or "request/data flow").strip()))
    for module in modules:
        module.dependencies = []
    if any(c.service == "Amazon SQS" for c in components) and any(c.service == "AWS Lambda" for c in components):
        flows = [DataFlow(from_node="Amazon SQS", to_node="AWS Lambda", action="Event Source Mapping/ReceiveMessage"), DataFlow(from_node="AWS Lambda", to_node="Amazon DynamoDB", action="ConditionalCheck idempotency"), DataFlow(from_node="AWS Lambda", to_node="Amazon SQS DLQ", action="Redrive failed messages")]
    elif any(x in text for x in ("rotation", "자동 교체", "자동교체", "secret rotation")):
        flows = [DataFlow(from_node="Amazon EventBridge Scheduler", to_node="AWS Lambda Rotation", action="Scheduled invocation"), DataFlow(from_node="AWS Lambda Rotation", to_node="AWS Secrets Manager", action="RotateSecret/GetSecretValue"), DataFlow(from_node="AWS Lambda Rotation", to_node="Amazon CloudWatch", action="Audit and failure logs")]
    elif not flows and len(components) >= 2:
        flows = [DataFlow(from_node=components[i].service, to_node=components[i + 1].service, action="configured integration") for i in range(len(components) - 1)]
    for module in modules:
        module.dependencies = [f"{flow.from_node} -> {flow.to_node}: {flow.action}" for flow in flows if any(module.title.lower() in node.lower() for node in (flow.from_node, flow.to_node))]
    fixed_specs = []
    services = {c.service for c in components}
    if "AWS Lambda" in services: fixed_specs += [{"moduleId": "aws-lambda", "field": field} for field in ("Function Name", "Runtime", "Handler", "Code/Provided File", "Execution Role")]
    if "Amazon EventBridge" in services: fixed_specs += [{"moduleId": "amazon-eventbridge", "field": field} for field in ("Schedule Expression", "Timezone", "Target ARN", "Scheduler Execution Role")]
    if "AWS KMS" in services: fixed_specs += [{"moduleId": "aws-kms", "field": field} for field in ("Key Alias", "Key Policy", "Encryption Target")]
    if "AWS Secrets Manager" in services: fixed_specs.append({"moduleId": "aws-secrets-manager", "field": "Secret Name/ARN"})
    if "AWS Network Firewall" in services: fixed_specs += [{"moduleId": "aws-network-firewall", "field": field} for field in ("FirewallPolicy", "Stateful RuleGroup", "Firewall Subnet", "SubnetMapping", "LoggingConfiguration", "Route Table/Endpoint")]
    if "Amazon EC2" in services: fixed_specs += [{"moduleId": "amazon-ec2", "field": field} for field in ("Subnet", "Security Group", "Instance Profile", "User Data")]
    if "Amazon DynamoDB" in services: fixed_specs += [{"moduleId": "amazon-dynamodb", "field": field} for field in ("Table Name", "Partition Key", "DynamoDB Access Policy")]
    if "AWS IAM" in services or "AWS Lambda" in services: fixed_specs.append({"moduleId": "aws-lambda", "field": "Least-Privilege IAM Actions"})
    if "Amazon SQS" in services and "AWS Lambda" in services: fixed_specs += [{"moduleId": "aws-lambda", "field": field} for field in ("SQS Queue URL/ARN Environment Variable", "AWS_REGION Environment Variable", "Event Source Mapping", "SQS Receive/Delete Permissions", "DLQ/RedrivePolicy")]
    if "Amazon DynamoDB" in services: fixed_specs += [{"moduleId": "amazon-dynamodb", "field": field} for field in ("Table Name", "Idempotency Key", "Status Field", "TTL", "ConditionalCheck Permission")]
    dependencies = [{"from": flow.from_node, "to": flow.to_node, "action": flow.action} for flow in flows]
    for module in modules:
        module.fixed_specs = [spec for spec in fixed_specs if spec.get("moduleId") == module.id]
    behavior_checks = [{"type": "end_to_end", "description": "정상 경로의 실제 데이터 또는 요청 흐름 검증"}]
    if any(c.service == "AWS Lambda" for c in components): behavior_checks.append({"type": "failure_or_security", "description": "Lambda 오류·권한·민감정보 비노출 검증"})
    if any(c.service == "AWS Network Firewall" for c in components): behavior_checks.append({"type": "network_behavior", "description": "EC2에서 허용 도메인과 차단 도메인으로 실제 트래픽을 전송하고 Firewall Endpoint 경유·허용/차단·Alert 로그를 검증"})
    if any(c.service == "AWS KMS" for c in components): behavior_checks.append({"type": "behavior", "description": "암호화와 키 정책 적용 검증"})
    goal = str(request_object.get("title") or request_object.get("topic") or req.raw.strip())
    return AssignmentBlueprint(goal=goal, data_flow=flows, components=components, logical_modules=modules, provided_files=files, fixed_specs=fixed_specs, dependencies=dependencies, behavior_checks=behavior_checks, risks=["권한·정책·실제 동작 검증 누락", "지급파일과 배포 리소스 불일치"])

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

def check_approved_modules(blueprint: AssignmentBlueprint, draft: TaskDraft) -> list[str]:
    expected = {module.title.lower() for module in blueprint.logical_modules}
    actual = {module.title.lower() for module in (draft.document.modules if draft.document else [])}
    missing = expected - actual
    extra = actual - expected
    return ([f"승인된 module이 최종 문서에서 누락되었습니다: {sorted(missing)}"] if missing else []) + ([f"승인되지 않은 module이 최종 문서에 추가되었습니다: {sorted(extra)}"] if extra else [])

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
