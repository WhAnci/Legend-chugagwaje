"""Logical architecture-role grouping for assignment modules."""
import json
import re

OFFICIAL_NAMES = {
    "api gateway websocket": "Amazon API Gateway", "eventbridge pipes": "Amazon EventBridge Pipes", "cloudfront functions": "CloudFront Functions", "vpc endpoint": "Amazon VPC Endpoint", "secrets manager": "AWS Secrets Manager", "network firewall": "AWS Network Firewall", "stateful rule group": "AWS Network Firewall", "firewall policy": "AWS Network Firewall", "firewall endpoint": "AWS Network Firewall", "step functions": "AWS Step Functions", "application load balancer": "Application Load Balancer", "route 53": "Amazon Route 53", "vpc lattice": "Amazon VPC Lattice", "cloudwatch": "Amazon CloudWatch", "cloudfront": "Amazon CloudFront", "dynamodb": "Amazon DynamoDB", "eventbridge": "Amazon EventBridge", "cognito": "Amazon Cognito", "lambda": "AWS Lambda", "sqs": "Amazon SQS", "sns": "Amazon SNS", "waf": "AWS WAF", "ecs": "Amazon ECS", "ecr": "Amazon ECR", "ec2": "Amazon EC2", "alb": "Application Load Balancer", "rds": "Amazon RDS", "vpc": "Amazon VPC", "s3": "Amazon S3", "kms": "AWS KMS", "ssm": "AWS Systems Manager", "route53": "Amazon Route 53"
}

def official_service(value: str) -> str:
    text = str(value or "").lower()
    for key, official in sorted(OFFICIAL_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text): return official
    return str(value or "").strip()

CATALOG = [
    ("CloudFront Functions", "Function", "CloudFront Function 요청 검증", ("cloudfront function", "cloudfront functions"), ("function", "handler", "event", "viewer request", "header", "code")),
    ("AWS Network Firewall", "Firewall", "Network Firewall 보호 계층", ("network firewall", "stateful rule group", "firewall policy", "firewall endpoint"), ("rule group", "policy", "endpoint", "subnet", "domain", "alert", "flow")),
    ("AWS WAF", "WebACL", "AWS WAF Web ACL", ("aws waf", "waf", "web acl", "webacl"), ("acl", "scope", "rule", "block", "allow", "ip set", "managed rule")),
    ("Amazon CloudFront", "Distribution", "CloudFront 배포", ("cloudfront", "distribution", "콘텐츠 배포"), ("distribution", "origin", "cache", "viewer", "protocol", "behavior")),
    ("Amazon VPC Lattice", "ServiceNetwork", "VPC Lattice 서비스 연결 계층", ("vpc lattice", "service network", "service association", "service network association"), ("service network", "association", "service", "listener", "target group", "auth policy")),
    ("Amazon API Gateway", "API", "API Gateway API", ("api gateway", "websocket api", "websocket"), ("route", "stage", "resource", "method", "integration")),
    ("Amazon DynamoDB", "Table", "DynamoDB 테이블", ("dynamodb", "dynamo db", "테이블"), ("table", "partition", "sort key", "billing", "gsi")),
    ("AWS Lambda", "Function", "Lambda 함수", ("lambda", "함수"), ("runtime", "handler", "environment", "role", "timeout", "event source")),
    ("Amazon SQS", "Queue", "SQS 메시지 큐", ("sqs", "message queue", "메시지 큐"), ("queue", "visibility", "retention", "dead letter", "fifo")),
    ("Amazon SNS", "Topic", "SNS topic", ("sns", "notification topic"), ("topic", "subscription", "publisher")),
    ("Amazon VPC", "VPC", "VPC 네트워크 기본 구조", ("vpc", "virtual private cloud"), ("subnet", "route table", "internet gateway", "nat gateway", "cidr")),
    ("Amazon EC2 애플리케이션", "Instance", "EC2 애플리케이션 실행 계층", ("ec2", "user data", "웹 애플리케이션", "애플리케이션 서버"), ("instance", "ami", "instance type", "user data", "security group", "application")),
    ("Amazon S3", "Bucket", "S3 객체 저장소", ("s3", "object storage", "객체 저장 버킷"), ("bucket", "object", "versioning", "lifecycle", "bucket policy")),
    ("Amazon ECR", "Repository", "ECR 이미지 리포지토리", ("ecr", "container image repository", "이미지 저장소"), ("repository", "image", "scan", "tag")),
    ("Amazon ECS", "Fargate Service", "ECS Fargate 서비스", ("ecs", "fargate", "컨테이너 서비스"), ("service", "task definition", "desired", "cluster")),
    ("Application Load Balancer", "Load Balancer", "Application Load Balancer", ("alb", "elb", "application load balancer", "elastic load balancing", "로드 밸런서", "listener", "target group", "health check"), ("listener", "target group", "health check")),
    ("AWS Secrets Manager", "Secret", "Secrets Manager 시크릿", ("secrets manager",), ("secret", "rotation", "kms")),
    ("Amazon VPC Endpoint", "Interface Endpoint", "VPC Endpoint", ("vpc endpoint", "interface endpoint"), ("endpoint", "private dns", "endpoint policy")),
    ("Amazon CloudWatch", "LogGroup", "CloudWatch 로그", ("cloudwatch logs", "log group"), ("log group", "alert log", "flow log", "retention")),
    ("VPC Routing", "Routing", "VPC 라우팅 계층", ("vpc routing", "routing table", "protected subnet", "firewall endpoint route"), ("route", "route table", "symmetric", "return traffic")),
]

def _text(value): return json.dumps(value, ensure_ascii=False).lower() if not isinstance(value, str) else value.lower()
def _contains(text, alias):
    return re.search(rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])", text) is not None if re.fullmatch(r"[a-z0-9][a-z0-9 -]*", alias.lower()) else alias.lower() in text

def detect(text):
    found = [entry for entry in CATALOG if any(_contains(text, alias) for alias in entry[3])]
    if any(e[0] == "Amazon VPC Endpoint" for e in found): found = [e for e in found if e[0] != "Amazon VPC"]
    return found

def _group_for(text):
    # Child resources are intentionally grouped by architecture role, not API resource count.
    if any(_contains(text, x) for x in ("network firewall", "stateful rule group", "firewall policy", "firewall endpoint")): return "AWS Network Firewall"
    if any(_contains(text, x) for x in ("vpc lattice", "service network", "service association")): return "Amazon VPC Lattice"
    if any(_contains(text, x) for x in ("application load balancer", "alb", "listener", "target group", "health check")): return "Application Load Balancer"
    if any(_contains(text, x) for x in ("cloudfront function", "cloudfront functions")): return "CloudFront Functions"
    if any(_contains(text, x) for x in ("ec2", "user data", "web application", "애플리케이션 서버")): return "Amazon EC2 애플리케이션"
    if any(_contains(text, x) for x in ("s3", "bucket policy", "versioning", "lifecycle")): return "Amazon S3"
    if any(_contains(text, x) for x in ("lambda", "event source mapping")): return "AWS Lambda"
    if any(_contains(text, x) for x in ("api gateway", "resource", "integration", "stage")): return "Amazon API Gateway"
    return None

def _entry_for(group):
    return next((entry for entry in CATALOG if entry[0] == group), (group, "Component", group, (), ()))

def decompose_document(doc):
    """Normalize LLM modules into architecture-role bundles and preserve independent layers."""
    modules = doc.get("modules") or []
    if not isinstance(modules, list): return doc
    grouped = {}
    order = []
    document_blob = _text(modules)
    lattice_context = _contains(document_blob, "vpc lattice") or _contains(document_blob, "service network")
    firewall_context = _contains(document_blob, "network firewall") or _contains(document_blob, "firewall policy") or _contains(document_blob, "stateful rule group")
    for original in modules:
        if not isinstance(original, dict): continue
        blob = _text(original)
        declared = _text({key: original.get(key, "") for key in ("service", "primaryService", "title", "resourceType")})
        group = _group_for(declared) or _group_for(blob) or official_service(original.get("service") or original.get("title")) or str(original.get("title", "구성 요소"))
        if lattice_context and not _contains(declared, "application load balancer") and any(_contains(blob, x) for x in ("service", "listener", "target group", "association")): group = "Amazon VPC Lattice"
        if firewall_context and any(_contains(blob, x) for x in ("rule group", "firewall policy", "firewall endpoint")): group = "AWS Network Firewall"
        # An explicitly named VPC Routing layer remains separate from the VPC foundation.
        if _contains(blob, "vpc routing") or _contains(blob, "protected subnet"): group = "VPC Routing"
        if group not in grouped:
            grouped[group] = {"service": group, "title": group, "resourceType": _entry_for(group)[1], "description": "", "specs": [], "fixedSpecs": [], "includedResources": [], "providedFiles": []}
            order.append(group)
        target = grouped[group]
        target["description"] = target["description"] or original.get("description", "")
        target["specs"].extend(original.get("specs") or [])
        target["fixedSpecs"].extend(original.get("fixedSpecs") or original.get("fixed_specs") or [])
        target["dependencies" ] = target.get("dependencies", []) + (original.get("dependencies") or [])
        target["providedFiles"].extend(original.get("providedFiles") or original.get("provided_files") or [])
        resources = original.get("includedResources") or original.get("included_resources") or []
        if not resources: resources = [str(original.get("resourceType") or original.get("title") or group)]
        for resource in resources:
            if resource not in target["includedResources"]: target["includedResources"].append(resource)
    result = []
    for number, group in enumerate(order, 1):
        module = grouped[group]
        entry = _entry_for(group)
        module["number"] = number
        module["service"] = group
        module["title"] = group
        module["primaryService"] = group
        module["role"] = entry[2]
        module["includedResources"] = module["includedResources"] or [group]
        if not module["description"]: module["description"] = f"{group}의 구성과 동작을 완성합니다."
        result.append(module)
    if result: doc["modules"] = result
    return doc
