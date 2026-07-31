from app.blueprint import create_blueprint, validate_blueprint
from app.models import TaskRequest


def test_blueprint_extracts_runtime_dependencies_and_file_owner():
    blueprint = create_blueprint(TaskRequest(raw="S3 이벤트로 Lambda가 KMS 암호화 후 SNS 발행"))
    services = {component.service for component in blueprint.components}
    assert {"Amazon S3", "AWS Lambda", "AWS KMS", "Amazon SNS"} <= services
    assert blueprint.provided_files[0].path == "lambda_function.py"
    assert blueprint.provided_files[0].used_by_module == ["aws-lambda"]
    assert not validate_blueprint(blueprint)


def test_blueprint_requires_file_owner():
    blueprint = create_blueprint(TaskRequest(raw="AWS Lambda 과제"))
    blueprint.provided_files[0].used_by_module = []
    assert any("사용 module" in error for error in validate_blueprint(blueprint))
