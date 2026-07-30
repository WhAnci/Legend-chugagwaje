from app.models import GradingCheck, TaskDraft, TaskDocument, TaskMeta, TaskModule, SpecItem
from app.consistency import check_consistency


def test_consistency_rejects_missing_script_and_duplicate_ids():
    document = TaskDocument(meta=TaskMeta(title="x"), modules=[TaskModule(title="CloudFront 배포", specs=[SpecItem(label="Name", value="dist")])])
    draft = TaskDraft(document=document, checks=[
        GradingCheck(id="CF-01", module="CloudFront 배포", label="배포", expected={"name": "dist"}, score=3, script_check="check_dist"),
        GradingCheck(id="CF-01", module="CloudFront 배포", label="중복", expected={}, score=3, script_check="check_other"),
    ], rubric_markdown="[CF-01]", grading_script="#!/bin/bash\n")
    errors = check_consistency(draft)
    assert any("중복" in error for error in errors)
    assert any("scriptCheck" in error for error in errors)


def test_consistency_accepts_single_source_mapping():
    document = TaskDocument(meta=TaskMeta(title="x"), modules=[TaskModule(title="CloudFront 배포", specs=[SpecItem(label="Name", value="dist")])])
    checks = [GradingCheck(id=f"CF-0{i}", module="CloudFront 배포", label=f"검사 {i}", expected={"name": "dist"}, score=1, script_check=f"check_{i}") for i in range(1, 7)]
    rubric = "dist\n" + "\n".join(f"[{c.id}] {c.label}" for c in checks)
    script = "dist\n" + "\n".join(f"# [{c.id}]\n{c.script_check}() {{ :; }}" for c in checks)
    draft = TaskDraft(document=document, checks=checks, rubric_markdown=rubric, grading_script=script)
    assert check_consistency(draft) == []
