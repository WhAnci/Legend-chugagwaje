from app.models import TaskDraft, TaskDocument, TaskMeta, TaskModule
from app.validate import validate

def test_script_rules():
    draft = TaskDraft(title="x", document=TaskDocument(meta=TaskMeta(title="x"), modules=[TaskModule(title="x")]), assignment_markdown="# 과제\n" + "\n".join(f"R-{i:02d}: 요구사항" for i in range(1, 9)) + " ALB", rubric_markdown="총점 6.0점\n" + "\n".join(f"C-{i:02d} 핵심 1점" for i in range(1, 6)), grading_script='#!/usr/bin/env bash\nset -Eeuo pipefail\ncase "$1" in --dry-run|--region|--output) ;; esac\n')
    result = validate(draft)
    assert result.ok

def test_secret_rejected():
    draft = TaskDraft(title="x", document=TaskDocument(meta=TaskMeta(title="x"), modules=[TaskModule(title="x")]), assignment_markdown="AKIA1234567890123456", rubric_markdown="# 표", grading_script='#!/bin/bash\nset -Eeuo pipefail\n')
    assert not validate(draft).ok
