from app.blueprint import create_blueprint, validate_blueprint
from app.models import TaskRequest, TaskDraft, TaskDocument, TaskMeta
from app.completeness import complete_assignment


def test_ecs_blue_green_has_five_required_modules_and_files():
    blueprint = create_blueprint(TaskRequest(raw="ECS Blue Green CodeDeploy"))
    assert [m.title for m in blueprint.logical_modules] == ["Amazon ECS", "AWS CodeDeploy", "Amazon ECR", "Application Load Balancer", "Amazon CloudWatch"]
    assert not validate_blueprint(blueprint)
    assert {"app.py", "Dockerfile", "requirements.txt", "taskdef.json", "appspec.yaml", "deploy.sh"} <= {f.path for f in blueprint.provided_files}


def test_ecs_blue_green_runtime_flow_does_not_use_ecr_as_request_target():
    blueprint = create_blueprint(TaskRequest(raw="ECS Blue Green CodeDeploy"))
    assert not any(flow.from_node == "Amazon ECS" and flow.to_node == "Amazon ECR" for flow in blueprint.data_flow)
    assert any(flow.to_node == "Application Load Balancer" for flow in blueprint.data_flow)
    assert any(check["type"] == "rollback" for check in blueprint.behavior_checks)


def test_completed_draft_contains_code_deploy_module():
    draft = complete_assignment("ECS Blue Green CodeDeploy", TaskDraft(document=TaskDocument(meta=TaskMeta(title="ECS BG"))))
    assert {"Amazon ECR", "Application Load Balancer", "Amazon ECS", "AWS CodeDeploy", "Amazon CloudWatch"} <= {m.title for m in draft.document.modules}
