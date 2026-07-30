from app.models import TaskDraft


def draft_for(services, specs):
    return TaskDraft.model_validate({
        "document": {"meta": {"title": "분해 테스트"}, "modules": [{
            "number": 1, "title": "전체 구성", "description": " ".join(services), "specs": specs
        }]}
    })


def names(draft):
    return [module.service for module in draft.document.modules]


def test_cloudfront_edge_security_is_split():
    draft = draft_for(["CloudFront", "WAF", "CloudFront Function"], [
        "Region : ap-northeast-2", "Web ACL Name : EdgeSecurityACL",
        "CloudFront Function Name : HeaderValidator", "Required Header : X-Security-Token",
        "Distribution Name : secure-distribution"
    ])
    assert names(draft) == ["CloudFront Functions", "AWS WAF", "CloudFront"]
    assert len(draft.document.modules) == 3
    assert all("CloudFront 및 WAF" not in module.title for module in draft.document.modules)


def test_websocket_is_split_without_topic_specific_normalizer():
    draft = draft_for(["DynamoDB", "Lambda", "API Gateway WebSocket"], [
        "Table Name : Connections", "Function Name : OnMessage", "Route Key : $default"
    ])
    assert set(names(draft)) == {"DynamoDB", "Lambda", "API Gateway WebSocket"}


def test_messaging_is_split():
    draft = draft_for(["Lambda", "SQS", "EventBridge Pipes"], [
        "Function Name : Consumer", "Queue Name : Jobs", "Pipe Name : JobPipe"
    ])
    assert set(names(draft)) == {"Lambda", "SQS", "EventBridge Pipes"}


def test_container_stack_is_split():
    draft = draft_for(["ECR", "ECS", "ALB"], [
        "Repository Name : app", "Service Name : app-service", "Target Group Name : app-target"
    ])
    assert set(names(draft)) == {"ECR", "ECS", "ALB"}


def test_static_web_is_split():
    draft = draft_for(["S3", "CloudFront", "Route 53"], [
        "Bucket Name : site-bucket", "Distribution Name : site-distribution", "Hosted Zone : example.com"
    ])
    assert set(names(draft)) == {"S3", "CloudFront", "Route 53"}


def test_auth_portal_is_split():
    draft = draft_for(["Cognito", "ALB", "ECS"], [
        "User Pool Name : portal-users", "Listener Port : 443", "Cluster Name : portal"
    ])
    assert set(names(draft)) == {"Cognito", "ALB", "ECS"}
