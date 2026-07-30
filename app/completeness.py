import json, re
from .models import TaskDraft

VPC_WEB_PROFILE = {
    "keywords": ("vpc", "ec2", "alb"),
    "modules": [
        ("VPC", "VPC", "VPC 및 네트워크", "두 가용 영역에 웹 서버와 ALB를 배치할 네트워크를 구성합니다.", [("VPC Name", "web-vpc"), ("Public Subnet Names", ["web-public-a", "web-public-c"]), ("Internet Gateway Name", "web-igw"), ("Route Table Name", "web-public-rt")]),
        ("EC2", "Instance", "EC2 웹 서버", "서로 다른 가용 영역의 두 웹 서버가 HTTP와 /health 응답을 제공하도록 구성합니다.", [("Instance Names", ["web-server-01", "web-server-02"]), ("User Data File", "userdata.sh"), ("Application Port", "80"), ("Health Check Path", "/health"), ("Required Tag", "Project=WebService")]),
        ("ALB", "Load Balancer", "Application Load Balancer", "두 웹 서버 중 정상 상태인 대상에 HTTP 요청을 분산합니다.", [("ALB Name", "web-alb"), ("Target Group Name", "web-tg"), ("Listener", "HTTP 80"), ("Health Check Path", "/health"), ("Registered Targets", ["web-server-01", "web-server-02"]) ]),
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

def _has(text, word): return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text.lower()) is not None

def complete_assignment(raw: str, draft: TaskDraft) -> TaskDraft:
    if not all(_has(raw, word) for word in VPC_WEB_PROFILE["keywords"]): return draft
    data = draft.model_dump(mode="json")
    document = data.get("document") or {}
    modules = document.setdefault("modules", [])
    existing = " ".join(json.dumps(module, ensure_ascii=False) for module in modules).lower()
    for service, resource_type, title, description, specs in VPC_WEB_PROFILE["modules"]:
        if service.lower() not in existing and title.lower() not in existing:
            modules.append({"service": service, "resourceType": resource_type, "title": title, "description": description, "fixedSpecs": [{"label": label, "value": value} for label, value in specs]})
    document["overview"] = document.get("overview") or "두 가용 영역의 웹 서버를 Application Load Balancer로 연결하여 정상 대상에 HTTP 요청을 분산하는 고가용성 웹 서비스를 구성합니다."
    document["architecture"] = document.get("architecture") or "Client\n  ↓ HTTP\nApplication Load Balancer\n  ↓\nTarget Group\n  ├── EC2 web-server-01 / AZ-a\n  └── EC2 web-server-02 / AZ-c"
    document["requirements"] = list(dict.fromkeys((document.get("requirements") or []) + ["두 가용 영역의 웹 서버", "ALB를 통한 HTTP 동작 검증", "ALB 보안 그룹을 통한 EC2 접근 제어"]))
    data["document"] = document
    files = data.setdefault("deployment_files", [])
    if not any(str(item.get("path", "")) == "userdata.sh" for item in files if isinstance(item, dict)):
        files.append({"path": "userdata.sh", "content": USERDATA})
    provided = data.get("provided_files") or []
    if not any(isinstance(item, dict) and item.get("name") == "userdata.sh" for item in provided):
        provided.append({"name": "userdata.sh", "description": "EC2 웹 서버와 /health 응답을 구성하는 User Data 스크립트"})
    data["provided_files"] = provided
    data["checks"] = data.get("checks") or []
    if not any("HTTP" in str(check.get("label", "")).upper() or "동작" in str(check.get("label", "")) for check in data["checks"] if isinstance(check, dict)):
        data["checks"].append({"id": "ALB-HTTP-01", "module": "Application Load Balancer", "label": "ALB HTTP 동작 및 대상 분산", "requirement": "ALB DNS에 HTTP 요청을 보내 정상 대상의 응답을 확인합니다.", "behaviorExpectation": "HTTP 200 응답과 두 EC2 식별 응답을 확인합니다.", "expected": {}, "score": 1.0, "required": True, "scriptCheck": "check_alb_http_behavior"})
    return TaskDraft.model_validate(data)
