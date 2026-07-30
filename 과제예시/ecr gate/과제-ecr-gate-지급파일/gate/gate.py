"""
gate — 이미지 스캔 결과를 판정해 승격 태그를 부여하는 게이트.

Inspector 스캔 완료 이벤트를 받아 해당 이미지의 스캔 결과를 조회하고,
CRITICAL 취약점이 하나도 없으면 같은 다이제스트에 승격 태그(PROMOTE_TAG)를 부여한다.
하나라도 있으면 태그를 부여하지 않는다.

환경변수
  REPO         대상 ECR 리포지토리 이름 (필수)
  PROMOTE_TAG  승격 태그 (기본: prod)

핸들러: gate.handler
런타임: python3.12
"""

import json
import os

import boto3

ecr = boto3.client("ecr")

REPO = os.environ["REPO"]
PROMOTE_TAG = os.environ.get("PROMOTE_TAG", "prod")


def _severity_counts(digest):
    """이미지의 심각도별 취약점 개수를 반환한다."""
    res = ecr.describe_image_scan_findings(
        repositoryName=REPO, imageId={"imageDigest": digest}
    )
    findings = res.get("imageScanFindings", {})
    counts = findings.get("findingSeverityCounts", {})
    return {k: int(v) for k, v in counts.items()}


def _promote(digest):
    """같은 다이제스트에 승격 태그를 추가로 부여한다(이미지 재업로드 아님)."""
    img = ecr.batch_get_image(
        repositoryName=REPO, imageIds=[{"imageDigest": digest}]
    )["images"][0]
    ecr.put_image(
        repositoryName=REPO,
        imageManifest=img["imageManifest"],
        imageManifestMediaType=img.get("imageManifestMediaType"),
        imageTag=PROMOTE_TAG,
    )


def _repo_name(raw):
    """이벤트의 repository-name 은 리포지토리 이름일 수도, 다이제스트까지 붙은 ARN 일 수도 있다."""
    if not raw:
        return ""
    if "repository/" in raw:
        raw = raw.split("repository/", 1)[1]
    return raw.split("/sha256:", 1)[0]


def handler(event, context):
    detail = event.get("detail", {})
    repo = _repo_name(detail.get("repository-name") or detail.get("repositoryName"))
    digest = detail.get("image-digest") or detail.get("imageDigest")
    tags = detail.get("image-tags") or detail.get("imageTags") or []

    if repo != REPO or not digest:
        print(json.dumps({"skipped": True, "repo": repo, "digest": digest}))
        return {"ok": True, "skipped": True}

    counts = _severity_counts(digest)
    critical = counts.get("CRITICAL", 0)
    decision = "PROMOTE" if critical == 0 else "BLOCK"

    # 채점·감사 근거로 판정을 남긴다.
    print(
        json.dumps(
            {
                "repo": repo,
                "digest": digest,
                "tags": tags,
                "severity": counts,
                "critical": critical,
                "decision": decision,
                "promote_tag": PROMOTE_TAG,
            }
        )
    )

    if decision == "PROMOTE":
        _promote(digest)

    return {"ok": True, "decision": decision, "digest": digest}
