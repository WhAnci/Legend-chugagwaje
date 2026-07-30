"""General-purpose extraction of independently assessable AWS modules."""
import json
import re

CATALOG = [
    ("CloudFront Functions", "Function", "CloudFront Function 요청 검증", ("cloudfront function", "cloudfront functions"), ("function", "handler", "event", "viewer request", "header", "code")),
    ("AWS WAF", "WebACL", "AWS WAF Web ACL", ("aws waf", "waf", "web acl", "webacl"), ("acl", "scope", "rule", "block", "allow", "ip set", "managed rule")),
    ("CloudFront", "Distribution", "CloudFront 배포", ("cloudfront", "distribution", "콘텐츠 배포"), ("distribution", "origin", "cache", "viewer", "protocol", "behavior")),
    ("EventBridge Pipes", "Pipe", "EventBridge Pipes", ("eventbridge pipes", "pipes"), ("pipe", "source", "target", "enrichment", "batch")),
    ("EventBridge", "Rule", "EventBridge 이벤트 규칙", ("eventbridge", "예약 실행", "이벤트 규칙"), ("schedule", "event pattern", "target", "cron")),
    ("API Gateway WebSocket", "WebSocket API", "API Gateway WebSocket API", ("api gateway websocket", "websocket api", "websocket"), ("route", "stage", "connection", "api id")),
    ("API Gateway", "API", "API Gateway API", ("api gateway",), ("route", "stage", "resource", "method")),
    ("DynamoDB", "Table", "DynamoDB 테이블", ("dynamodb", "dynamo db", "테이블"), ("table", "partition", "sort key", "billing", "gsi")),
    ("Lambda", "Function", "Lambda 함수", ("lambda", "함수"), ("runtime", "handler", "environment", "role", "timeout")),
    ("SQS", "Queue", "SQS 메시지 큐", ("sqs", "message queue", "메시지 큐"), ("queue", "visibility", "retention", "dead letter", "fifo")),
    ("ECR", "Repository", "ECR 이미지 리포지토리", ("ecr", "container image repository", "이미지 저장소"), ("repository", "image", "scan", "tag")),
    ("ECS", "Fargate Service", "ECS Fargate 서비스", ("ecs", "fargate", "컨테이너 서비스"), ("service", "task definition", "desired", "cluster")),
    ("ALB", "Load Balancer", "Application Load Balancer", ("alb", "application load balancer", "로드 밸런서"), ("listener", "target group", "health check")),
    ("Cognito", "UserPool", "Cognito User Pool", ("cognito", "user pool", "사용자 계정 풀"), ("user pool", "client", "domain", "issuer")),
    ("S3", "Bucket", "S3 버킷", ("s3", "object storage", "객체 저장 버킷"), ("bucket", "object", "versioning", "lifecycle")),
    ("EC2", "Instance", "EC2 인스턴스", ("ec2", "elastic compute cloud", "가상 서버"), ("instance", "ami", "instance type", "user data", "subnet")),
    ("KMS", "Key", "KMS 암호화 키", ("kms", "encryption key"), ("key", "alias", "rotation", "grant")),
    ("Route 53", "Hosted Zone", "Route 53 DNS", ("route 53", "route53", "dns"), ("hosted zone", "record", "alias", "health check")),
]


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False).lower() if not isinstance(value, str) else value.lower()


def _contains(text: str, alias: str) -> bool:
    if re.fullmatch(r"[a-z0-9][a-z0-9 -]*", alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text) is not None
    return alias in text


def detect(text: str) -> list[tuple]:
    found = []
    for entry in CATALOG:
        if any(_contains(text, alias) for alias in entry[3]):
            found.append(entry)
    # Specialized resources suppress their parent service to avoid phantom modules.
    if any(entry[0] == "EventBridge Pipes" for entry in found):
        found = [entry for entry in found if entry[0] != "EventBridge"]
    if any(entry[0] == "API Gateway WebSocket" for entry in found):
        found = [entry for entry in found if entry[0] != "API Gateway"]
    return found


def _spec_score(spec, entry) -> int:
    blob = _text(spec)
    return sum(1 for keyword in entry[4] if _contains(blob, keyword))


def decompose_document(doc: dict) -> dict:
    """Split combined modules without relying on a particular assignment topic."""
    modules = doc.get("modules") or []
    if not isinstance(modules, list): return doc
    output = []
    for original in modules:
        if not isinstance(original, dict): continue
        entries = detect(_text(original))
        # A service-specific module remains intact; only genuinely mixed modules split.
        if len(entries) < 2:
            output.append(original)
            continue
        groups = {entry[0]: [] for entry in entries}
        for spec in original.get("specs", []) or []:
            ranked = sorted(entries, key=lambda entry: _spec_score(spec, entry), reverse=True)
            winner = ranked[0]
            # Generic connection/region values belong to the first service unless a label is specific.
            if _spec_score(spec, winner) == 0:
                winner = entries[0]
            groups[winner[0]].append(spec)
        for entry in entries:
            service, resource_type, title, _, _ = entry
            module = {
                "number": len(output) + 1,
                "service": service,
                "resourceType": resource_type,
                "title": title,
                "description": original.get("description", ""),
                "specs": groups[service],
            }
            if original.get("dependencies"): module["dependencies"] = original["dependencies"]
            if original.get("providedFiles"): module["providedFiles"] = original["providedFiles"]
            output.append(module)
    # retry/LLM 응답에서 동일 리소스 module이 반복되면 하나로 정규화한다.
    merged = []
    positions = {}
    for module in output:
        service = str(module.get("service", "")).lower()
        resource_type = str(module.get("resourceType", module.get("resource_type", ""))).lower()
        title = re.sub(r"[^a-z0-9가-힣]", "", str(module.get("title", "")).lower())
        names = [str(spec.get("value", "")).lower() for spec in module.get("specs", []) if isinstance(spec, dict) and "name" in str(spec.get("label", "")).lower()]
        key = (service, resource_type, names[0] if names else title)
        if key not in positions:
            positions[key] = len(merged); merged.append(module); continue
        target = merged[positions[key]]
        existing = {(str(s.get("label")), json.dumps(s.get("value"), ensure_ascii=False)) for s in target.get("specs", []) if isinstance(s, dict)}
        for spec in module.get("specs", []):
            if not isinstance(spec, dict): continue
            pair = (str(spec.get("label")), json.dumps(spec.get("value"), ensure_ascii=False))
            if pair not in existing: target.setdefault("specs", []).append(spec); existing.add(pair)
        if module.get("description") and not target.get("description"): target["description"] = module["description"]
    for number, module in enumerate(merged, 1):
        module["number"] = number
    if merged: doc["modules"] = merged
    return doc
