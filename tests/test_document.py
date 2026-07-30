from app.build import document_html
from app.models import TaskDocument, TaskMeta, TaskModule, TaskSection, SpecItem


def websocket_doc():
    return TaskDocument(
        meta=TaskMeta(document_title="2024년도 클라우드컴퓨팅 직종 연습 과제", year=2024, occupation="Cloud Architect", title="실시간 WebSocket 채팅 시스템 구축", assignment_number="M04", duration="60분", candidate_number="", judge_confirmation="(인)"),
        overview="CloudFront 배포에 WAF와 Function을 연결합니다.\n정상 요청만 원본으로 전달합니다.", architecture="Client\n  ↓\nCloudFront Distribution\n  ├─ AWS WAF Web ACL\n  └─ CloudFront Function\n  ↓\nOrigin", requirements=["R-01 requirement"], precautions=[], modules=[TaskModule(
            number=1, title="실시간 WebSocket 채팅 시스템 구축", region_notice="ap-northeast-2", sections=[TaskSection(
                number=1, title="API Gateway", tasks=["R-02 route"], specs=[SpecItem(label="Route Key", value="$connect"), SpecItem(label="Route Key", value="$disconnect"), SpecItem(label="Route Key", value="$default")]
            )]
        )]
    )


def test_metadata_is_preserved():
    output = document_html(websocket_doc())
    assert "2024년도 클라우드컴퓨팅 직종 연습 과제" in output
    assert "Cloud Architect" in output
    assert "M04" in output
    assert "60분" in output
    assert "2026" not in output
    assert "제4과제" not in output


def test_fixed_specs_hide_inferred_values():
    doc = TaskDocument(meta=TaskMeta(title="x"), modules=[TaskModule(title="ALB", description="운영 목적을 설명합니다.", specs=[SpecItem(label="Hidden", value="secret")], fixed_specs=[SpecItem(label="Name", value="public")])])
    output = document_html(doc)
    assert "public" in output
    assert "secret" not in output


def test_routes_and_no_empty_list_markers():
    output = document_html(websocket_doc())
    assert "$connect" in output and "$disconnect" in output and "$default" in output
    assert "<li>" not in output
    assert "<ol" not in output
    assert "<div class='numbered-item'>" in output
    assert "1. 과제 개요" in output
    assert "2. 아키텍처 구성" in output
    assert "CloudFront Distribution" in output
