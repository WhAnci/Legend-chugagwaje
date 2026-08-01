from app.fallback_topics import FALLBACK_TOPIC_POOL
from app.topic_validation import validate_topic_candidate
from app.blueprint import create_blueprint
from app.models import TaskRequest


def test_fallback_pool_has_size_and_modules():
    assert len(FALLBACK_TOPIC_POOL) >= 15
    assert all(not validate_topic_candidate(topic, []) for topic in FALLBACK_TOPIC_POOL)


def test_blueprint_catalog_supports_non_serverless_services():
    for request, expected in [("ECS ECR ALB CloudWatch", "Amazon ECS"), ("DMS RDS S3 CloudWatch", "AWS DMS"), ("Systems Manager EC2 EventBridge CloudWatch", "AWS Systems Manager"), ("VPC Lattice ECS IAM CloudWatch", "Amazon VPC Lattice")]:
        blueprint = create_blueprint(TaskRequest(raw=request))
        assert expected in {module.title for module in blueprint.logical_modules}


def test_two_service_topic_is_rejected():
    topic = {"primaryService":"Amazon S3", "supportingServices":["AWS Lambda"], "expectedModules":[{"service":"Amazon S3"},{"service":"AWS Lambda"}], "estimatedModuleCount":2, "scenario":"S3 연결 처리", "behaviorValidation":"검증"}
    assert validate_topic_candidate(topic, [])
