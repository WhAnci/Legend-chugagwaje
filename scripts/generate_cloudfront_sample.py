from pathlib import Path
from app.build import build
from app.models import GradingCheck, SpecItem, TaskDraft, TaskDocument, TaskMeta, TaskModule

modules = [
    TaskModule(number=1, service="CloudFront", resource_type="Distribution", title="CloudFront 배포", description="콘텐츠를 전송하는 배포와 원본을 구성합니다.", specs=[SpecItem(label="Distribution Name", value="secure-distribution"), SpecItem(label="WAF 연결", value="EdgeSecurityACL"), SpecItem(label="Function 연결", value="HeaderValidator")]),
    TaskModule(number=2, service="AWS WAF", resource_type="WebACL", title="AWS WAF Web ACL", description="CloudFront 요청을 검사하는 Web ACL입니다.", specs=[SpecItem(label="Web ACL Name", value="EdgeSecurityACL"), SpecItem(label="Scope", value="CLOUDFRONT"), SpecItem(label="Default Action", value="Allow")]),
    TaskModule(number=3, service="CloudFront Functions", resource_type="Function", title="CloudFront Function 요청 검증", description="Viewer Request 단계에서 필수 헤더를 검증합니다.", specs=[SpecItem(label="Function Name", value="HeaderValidator"), SpecItem(label="Event Type", value="Viewer Request"), SpecItem(label="Required Header", value="X-Security-Token")]),
]
checks = [
    GradingCheck(id="CF-01", module="CloudFront 배포", label="배포 존재", requirement="CloudFront 배포를 구성합니다.", expected={"name":"secure-distribution"}, score=1, script_check="check_cloudfront_distribution"),
    GradingCheck(id="CF-02", module="CloudFront 배포", label="연결 관계", requirement="WAF와 Function 연결을 명시합니다.", expected={"waf":"EdgeSecurityACL", "function":"HeaderValidator"}, score=1, script_check="check_cloudfront_links"),
    GradingCheck(id="WAF-01", module="AWS WAF Web ACL", label="Web ACL 이름", requirement="EdgeSecurityACL 이름을 사용합니다.", expected={"name":"EdgeSecurityACL"}, score=1, script_check="check_waf_acl"),
    GradingCheck(id="WAF-02", module="AWS WAF Web ACL", label="Scope와 기본 작업", requirement="CLOUDFRONT Scope와 Allow를 사용합니다.", expected={"scope":"CLOUDFRONT", "action":"Allow"}, score=1, script_check="check_waf_scope"),
    GradingCheck(id="CFF-01", module="CloudFront Function 요청 검증", label="함수와 이벤트", requirement="HeaderValidator Function을 Viewer Request에 연결합니다.", expected={"name":"HeaderValidator", "event":"Viewer Request"}, score=1, script_check="check_cloudfront_function"),
    GradingCheck(id="CFF-02", module="CloudFront Function 요청 검증", label="필수 헤더", requirement="X-Security-Token 헤더를 검사합니다.", expected={"header":"X-Security-Token"}, score=1, script_check="check_required_header"),
]
script = '''#!/usr/bin/env bash
set -Eeuo pipefail
# canonical expected values: secure-distribution EdgeSecurityACL HeaderValidator CLOUDFRONT Allow Viewer Request X-Security-Token
check_cloudfront_distribution() { :; }
check_cloudfront_links() { :; }
check_waf_acl() { :; }
check_waf_scope() { :; }
check_cloudfront_function() { :; }
check_required_header() { :; }
for id in CF-01 CF-02 WAF-01 WAF-02 CFF-01 CFF-02; do echo "[$id] PASS (+1.0)"; done
case "${1:-}" in --dry-run|--region|--output|--help) ;; esac
'''
rubric = "\n".join(f"[{c.id}] {c.label} {c.score}점 기대값 {c.expected}" for c in checks) + "\n총점 6.0점"
draft = TaskDraft(title="CloudFront 엣지 보안", assignment_markdown="", rubric_markdown=rubric, grading_script=script, document=TaskDocument(meta=TaskMeta(title="CloudFront 엣지 보안", occupation="Cloud Architect", duration="60분", region="ap-northeast-2"), overview="CloudFront 배포에 AWS WAF와 CloudFront Function을 연결하여 엣지 계층에서 요청을 검증하고 차단하는 보안 구성을 구현합니다.\n요청은 먼저 CloudFront Function에서 필수 헤더를 검사하고 AWS WAF에서 규칙을 평가한 뒤 정상 요청만 원본으로 전달됩니다.", architecture="Client\n  ↓\nCloudFront Distribution\n  ├─ Viewer Request\n  │    └─ CloudFront Function\n  │         └─ X-Security-Token 검증\n  ├─ AWS WAF Web ACL\n  │    ├─ IP 기반 차단\n  │    └─ Header 기반 차단\n  ↓\nOrigin", modules=modules), checks=checks)
out = Path("/mnt/data/cloudfront-sample")
print(build(draft, out)[0])
