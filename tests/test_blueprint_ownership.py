from app.blueprint import create_blueprint, validate_blueprint
from app.models import TaskRequest
from app.revision import repair_references


def test_api_lambda_children_are_owned_by_modules():
    blueprint = create_blueprint(TaskRequest(raw="API Gateway Lambda 연동"))
    modules = {m.id: m for m in blueprint.logical_modules}
    assert "api-lambda-integration" in modules["amazon-api-gateway"].component_ids
    assert "api-lambda-permission" in modules["aws-lambda"].component_ids
    assert not validate_blueprint(blueprint)


def test_every_component_has_exactly_one_owner():
    blueprint = create_blueprint(TaskRequest(raw="API Gateway Lambda SQS"))
    owners = {component.id: 0 for component in blueprint.components}
    for module in blueprint.logical_modules:
        for component_id in module.component_ids:
            owners[component_id] = owners.get(component_id, 0) + 1
    assert all(count == 1 for count in owners.values())


def test_lifecycle_is_owned_by_s3_role_module():
    blueprint = create_blueprint(TaskRequest(raw="S3 Versioning Lifecycle 30일 Glacier"))
    s3 = next(module for module in blueprint.logical_modules if module.title == "Amazon S3")
    assert "s3-lifecycle-configuration" in s3.component_ids


def test_invalid_owner_is_repaired_or_reported():
    blueprint = create_blueprint(TaskRequest(raw="API Gateway Lambda"))
    blueprint.components[0].owner_module_id = "missing-module"
    repaired, repairs = repair_references(blueprint)
    assert repairs
    assert repaired.components[0].owner_module_id in {module.id for module in repaired.logical_modules} or "unresolved" in repairs[-1]
