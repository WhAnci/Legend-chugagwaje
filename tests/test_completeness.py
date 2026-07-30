from app.completeness import complete_assignment
from app.models import TaskDraft, TaskDocument, TaskMeta


def test_vpc_ec2_alb_request_is_completed():
    draft = complete_assignment(
        "vpc, ec2, alb를 사용하는 간단한 과제",
        TaskDraft(document=TaskDocument(meta=TaskMeta(title="웹 서비스")))
    )
    titles = [module.title for module in draft.document.modules]
    assert any("VPC" in title for title in titles)
    assert any("EC2" in title for title in titles)
    assert any("Load Balancer" in title for title in titles)
    assert any("접근 제어" in title for title in titles)
    assert any(file.path == "userdata.sh" for file in draft.deployment_files)
    assert draft.document.architecture
