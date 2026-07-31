import json, re
from .models import TaskDraft

VPC_WEB_PROFILE = {
    "keywords": ("vpc", "ec2", "alb"),
    "modules": [
        ("VPC", "VPC", "VPC 및 네트워크", "두 가용 영역에 웹 서버와 ALB를 배치할 네트워크를 구성합니다.", [("VPC Name", "web-vpc"), ("Public Subnet Names", ["web-public-a", "web-public-c"]), ("Internet Gateway Name", "web-igw"), ("Route Table Name", "web-public-rt")]),
        ("EC2", "Instance", "EC2 웹 서버", "서로 다른 가용 영역의 두 웹 서버가 HTTP와 /health 응답을 제공하도록 구성합니다.", [("Instance Names", ["web-server-01", "web-server-02"]), ("User Data File", "userdata.sh"), ("Application Port", "80"), ("Health Check Path", "/health"), ("Required Tag", "Project=WebService")]),
        ("ALB", "Load Balancer", "Application Load Balancer", "두 웹 서버 중 정상 상태인 대상에 HTTP 요청을 분산합니다.", [("ALB Name", "web-alb"), ("Target Group Name", "web-tg"), ("Listener", "HTTP 80"), ("Health Check Path", "/health"), ("Registered Targets", ["web-server-01", "web-server-02"])]),
        ("Security Group", "SecurityGroup", "접근 제어", "외부 HTTP 요청은 ALB에서 받고 EC2는 ALB 보안 그룹의 HTTP만 허용합니다.", [("ALB Security Group Name", "web-alb-sg"), ("EC2 Security Group Name", "web-ec2-sg"), ("EC2 HTTP Source", "web-alb-sg")]),
    ],
}
USERDATA = '''#!/usr/bin/env bash
set -Eeuo pipefail
if command -v dnf >/dev/null 2>&1; then dnf install -y nginx; else yum install -y nginx; fi
hostname_value="$(hostname)"
mkdir -p /usr/share/nginx/html
printf 'Hello from %s\\n' "$hostname_value" > /usr/share/nginx/html/index.html
printf 'OK\\n' > /usr/share/nginx/html/health
systemctl enable --now nginx
'''
SECRET_PROFILE = [
    ("Amazon VPC", "VPC", "인터넷 경로 없는 프라이빗 네트워크", "Internet Gateway와 NAT Gateway 없이 두 AZ의 프라이빗 서브넷을 구성합니다.", [("VPC Name", "secure-config-vpc"), ("Private Subnet Names", ["secure-private-a", "secure-private-c"]), ("Required Tag", "Project=SecureConfig")]),
    ("AWS Secrets Manager", "Secret", "애플리케이션 시크릿", "Lambda가 지정된 시크릿을 조회하되 원문은 외부 응답과 로그에 노출하지 않습니다.", [("Secret Name", "app/prod/db-credential")]),
    ("Amazon VPC Endpoint", "Interface Endpoint", "Secrets Manager Interface Endpoint", "격리된 Lambda가 인터넷을 통하지 않고 Secrets Manager API에 접근합니다.", [("Endpoint Service", "com.amazonaws.ap-northeast-2.secretsmanager"), ("Endpoint Type", "Interface"), ("Private DNS", "Enabled"), ("Required Port", "TCP 443")]),
    ("AWS Lambda", "Function", "Lambda 시크릿 조회 함수", "두 프라이빗 서브넷에서 실행되어 비민감 메타데이터만 반환합니다.", [("Function Name", "secure-config-loader"), ("Runtime", "Python 3.11"), ("Handler", "lambda_function.lambda_handler"), ("Environment Variable", "SECRET_NAME"), ("Provided File", "lambda_function.py")]),
    ("IAM 및 접근 제어", "IAM/SecurityGroup", "최소 권한 및 접근 제어", "Lambda 역할과 Endpoint 보안 그룹을 최소 권한으로 제한합니다.", [("Role Name", "secure-config-loader-role"), ("Allowed Action", "secretsmanager:GetSecretValue"), ("Lambda Security Group", "secure-lambda-sg"), ("Endpoint Security Group", "secure-endpoint-sg"), ("Endpoint Port", "TCP 443")]),
]
SECRET_LAMBDA = '''import json, os, boto3

def lambda_handler(event, context):
    try:
        result = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["SECRET_NAME"])
        return {"statusCode": 200, "body": json.dumps({"retrieved": True, "versionId": result.get("VersionId", "")})}
    except Exception as exc:
        return {"statusCode": 500, "body": json.dumps({"retrieved": False, "error": type(exc).__name__})}
'''
EVENT_PROFILE = [
    ("Amazon API Gateway", "API", "HTTP 요청을 SQS로 전달하는 API Gateway 엔드포인트입니다.", [("API Name", "event-api"), ("Protocol", "HTTP"), ("Resource Path", "/events"), ("Method", "POST"), ("Integration Type", "SQS"), ("Target Queue", "event-queue")]),
    ("Amazon SQS", "Queue", "수신 이벤트를 비동기로 버퍼링하는 SQS 큐입니다.", [("Queue Name", "event-queue"), ("Queue Type", "Standard"), ("Visibility Timeout", "30"), ("Message Retention Period", "345600")]),
    ("AWS Lambda", "Function", "SQS 메시지를 처리하는 Lambda 함수입니다.", [("Function Name", "event-processor"), ("Runtime", "Python 3.12"), ("Handler", "lambda_function.lambda_handler"), ("Source Queue", "event-queue"), ("Execution Role", "event-processor-role")]),
    ("Amazon DynamoDB", "Table", "처리된 이벤트의 정형 결과를 저장하는 DynamoDB 테이블입니다.", [("Table Name", "processed-events"), ("Partition Key", "EventId"), ("Billing Mode", "PAY_PER_REQUEST")]),
    ("Amazon SNS", "Topic", "처리 결과 알림을 발행하는 SNS topic입니다.", [("Topic Name", "event-notifications"), ("Topic Type", "Standard"), ("Publisher", "AWS Lambda")]),
]

def _has(text, word): return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text.lower()) is not None
def _secret_requested(raw):
    text = raw.lower()
    return "lambda" in text and "secrets manager" in text and ("vpc" in text or "interface endpoint" in text)

def _complete_secret_bundle(raw, draft):
    data = draft.model_dump(mode="json"); document = data.get("document") or {}
    document["modules"] = [{"service": service, "resourceType": resource_type, "title": service, "description": description, "fixedSpecs": [{"label": label, "value": value} for label, value in specs]} for service, resource_type, _role, description, specs in SECRET_PROFILE]
    document["overview"] = "인터넷 경로가 없는 프라이빗 네트워크의 Lambda가 Secrets Manager Interface VPC Endpoint를 통해 시크릿을 조회하는 보안 구성을 검증합니다."
    document["architecture"] = "Private Lambda → Interface VPC Endpoint → AWS Secrets Manager"
    document["requirements"] = list(dict.fromkeys((document.get("requirements") or []) + ["Internet Gateway와 NAT Gateway를 사용하지 않는 격리 네트워크", "시크릿 원문 비노출", "Endpoint를 통한 실제 조회 동작"]))
    data["document"] = document
    files = data.setdefault("deployment_files", [])
    if not any(isinstance(item, dict) and item.get("path") == "lambda_function.py" for item in files): files.append({"path": "lambda_function.py", "content": SECRET_LAMBDA})
    provided = data.get("provided_files") or []
    if not any(isinstance(item, dict) and item.get("name") == "lambda_function.py" for item in provided): provided.append({"name": "lambda_function.py", "description": "Secrets Manager 조회 및 비민감 결과 반환 Lambda 코드"})
    data["provided_files"] = provided
    return TaskDraft.model_validate(data)

def _event_requested(raw):
    text = raw.lower()
    return all(word in text for word in ("api gateway", "sqs", "lambda", "dynamodb", "sns"))

def _complete_event_bundle(raw, draft):
    data = draft.model_dump(mode="json"); document = data.get("document") or {}; old = document.get("modules") or []
    normalized = []
    for service, resource_type, description, specs in EVENT_PROFILE:
        blob = lambda m: json.dumps(m, ensure_ascii=False).lower()
        token = service.split()[-1].lower()
        candidates = [m for m in old if service.lower() in blob(m) or token in blob(m)]
        module = dict(candidates[0]) if candidates else {}
        module.update({"service": service, "resourceType": resource_type, "title": service, "description": module.get("description") or description})
        fixed = module.get("fixedSpecs") or module.get("fixed_specs") or module.get("specs") or []
        module["fixedSpecs"] = fixed or [{"label": label, "value": value} for label, value in specs]
        module.pop("specs", None); normalized.append(module)
    document["modules"] = normalized
    document["overview"] = document.get("overview") or "HTTP 이벤트를 수집하고 SQS, Lambda, DynamoDB, SNS를 연결해 비동기 처리 결과를 저장·통지하는 end-to-end 파이프라인입니다."
    document["architecture"] = document.get("architecture") or "Client → Amazon API Gateway → Amazon SQS → AWS Lambda → Amazon DynamoDB / Amazon SNS"
    data["document"] = document
    return TaskDraft.model_validate(data)

def complete_assignment(raw: str, draft: TaskDraft) -> TaskDraft:
    if _secret_requested(raw): return _complete_secret_bundle(raw, draft)
    if _event_requested(raw): return _complete_event_bundle(raw, draft)
    if not (_has(raw, "vpc") and _has(raw, "ec2") and (_has(raw, "alb") or _has(raw, "elb"))): return draft
    data = draft.model_dump(mode="json"); document = data.get("document") or {}; modules = document.setdefault("modules", [])
    existing = " ".join(json.dumps(module, ensure_ascii=False) for module in modules).lower()
    for service, resource_type, title, description, specs in VPC_WEB_PROFILE["modules"]:
        if service.lower() not in existing and title.lower() not in existing:
            modules.append({"service": service, "resourceType": resource_type, "title": title, "description": description, "fixedSpecs": [{"label": label, "value": value} for label, value in specs]})
    document["overview"] = document.get("overview") or "두 가용 영역의 웹 서버를 Application Load Balancer로 연결하여 정상 대상에 HTTP 요청을 분산하는 고가용성 웹 서비스를 구성합니다."
    document["architecture"] = document.get("architecture") or "Client\n  ↓ HTTP\nApplication Load Balancer\n  ↓\nTarget Group\n  ├── EC2 web-server-01 / AZ-a\n  └── EC2 web-server-02 / AZ-c"
    document["requirements"] = list(dict.fromkeys((document.get("requirements") or []) + ["두 가용 영역의 웹 서버", "ALB를 통한 HTTP 동작 검증", "ALB 보안 그룹을 통한 EC2 접근 제어"]))
    data["document"] = document
    files = data.setdefault("deployment_files", [])
    if not any(str(item.get("path", "")) == "userdata.sh" for item in files if isinstance(item, dict)): files.append({"path": "userdata.sh", "content": USERDATA})
    provided = data.get("provided_files") or []
    if not any(isinstance(item, dict) and item.get("name") == "userdata.sh" for item in provided): provided.append({"name": "userdata.sh", "description": "EC2 웹 서버와 /health 응답을 구성하는 User Data 스크립트"})
    data["provided_files"] = provided
    data["checks"] = data.get("checks") or []
    if not any("HTTP" in str(check.get("label", "")).upper() or "동작" in str(check.get("label", "")) for check in data["checks"] if isinstance(check, dict)):
        data["checks"].append({"id": "ALB-HTTP-01", "module": "Application Load Balancer", "label": "ALB HTTP 동작 및 대상 분산", "requirement": "ALB DNS에 HTTP 요청을 보내 정상 대상의 응답을 확인합니다.", "behaviorExpectation": "HTTP 200 응답과 두 EC2 식별 응답을 확인합니다.", "expected": {}, "score": 1.0, "required": True, "scriptCheck": "check_alb_http_behavior"})
    return TaskDraft.model_validate(data)
