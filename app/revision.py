"""Structured Blueprint validation issues and safe deterministic repairs."""
from copy import deepcopy
from dataclasses import dataclass
from .blueprint import AssignmentBlueprint

@dataclass
class ValidationIssue:
    error_type: str
    component_id: str = ""
    json_path: str = "$
    current_value: object = None
    expected_condition: str = ""
    required_action: str = ""
    severity: str = "error"
    description: str = ""

    def as_dict(self):
        return {"errorType": self.error_type, "componentId": self.component_id, "jsonPath": self.json_path, "currentValue": self.current_value, "expectedCondition": self.expected_condition, "requiredAction": self.required_action, "severity": self.severity, "description": self.description}

def normalize_issues(values) -> list[dict]:
    result = []
    for value in values or []:
        if isinstance(value, dict):
            result.append({"errorType": value.get("errorType", value.get("issue", value.get("category", "ReviewerIssue"))), "componentId": value.get("componentId", value.get("component", "")), "jsonPath": value.get("jsonPath", "$"), "currentValue": value.get("currentValue"), "expectedCondition": value.get("expectedCondition", value.get("detail", "")), "requiredAction": value.get("requiredAction", value.get("description", "")), "severity": value.get("severity", "error"), "description": value.get("description", value.get("detail", ""))})
        else:
            result.append({"errorType": "ReviewerIssue", "componentId": "", "jsonPath": "$", "currentValue": None, "expectedCondition": str(value), "requiredAction": str(value), "severity": "warning", "description": str(value)})
    return result

def _component(blueprint, service):
    return next((c for c in blueprint.components if c.service.lower() == service.lower() or service.lower() in c.service.lower()), None)

def deterministic_autofix(blueprint: AssignmentBlueprint, issues: list[dict]) -> tuple[AssignmentBlueprint, list[dict]]:
    """Apply only issue-scoped edits; never replace component/module arrays."""
    result = blueprint.model_copy(deep=True); resolved = []
    all_text = " ".join((i.get("errorType", "") + " " + i.get("description", "") + " " + i.get("requiredAction", "")).lower() for i in issues)
    lam = _component(result, "AWS Lambda"); s3 = _component(result, "Amazon S3")
    if lam and any(x in all_text for x in ("environment", "환경 변수", "bucket_name", "sns_topic_arn")):
        names = {item.get("name") for item in lam.environment_variables}
        for name in ("BUCKET_NAME", "SNS_TOPIC_ARN", "LOG_LEVEL", "VALID_TAG_KEY", "AWS_REGION"):
            if name.lower() in all_text or name in {"BUCKET_NAME", "AWS_REGION"}:
                if name not in names: lam.environment_variables.append({"name": name, "source": "Blueprint dependency"})
        resolved.append("Missing_Environment_Variables")
    if s3 and any(x in all_text for x in ("lifecycle", "glacier", "수명 주기")):
        if "Lifecycle Configuration" not in s3.included_resources: s3.included_resources.append("Lifecycle Configuration")
        s3.configurations.setdefault("lifecycleRules", []).append({"transitionAfterDays": 30, "storageClass": "GLACIER"})
        resolved.append("Missing_S3_Lifecycle_Configuration_Component")
    if s3 and any(x in all_text for x in ("s3 bucket fixed", "bucket name", "fixedspec")):
        for field in ("Bucket Name/Pattern", "Region", "Versioning", "Public Access Block", "Encryption"):
            if field not in {x.get("field") for x in result.fixed_specs}: result.fixed_specs.append({"moduleId": "amazon-s3", "field": field})
        resolved.append("Missing_S3_Bucket_FixedSpecs")
    if any(x in all_text for x in ("fixedspec", "least-privilege", "iam action")):
        moved = [x for x in result.fixed_specs if any(k in str(x).lower() for k in ("least-privilege", "iam action"))]
        result.fixed_specs = [x for x in result.fixed_specs if x not in moved]
        for item in moved: result.permission_specs.append({"principal": "lambda-execution-role", "actions": [item.get("field", "")], "resources": [], "conditions": {}})
        if moved: resolved.append("FixedSpec_Mixed_With_Validation")
    if any(x in all_text for x in ("dataflow", "end-to-end", "비논리적", "ambiguous")) and s3 and lam:
        result.data_flow = [x for x in result.data_flow if not ("iam" in x.from_node.lower() or "iam" in x.to_node.lower())]
        result.data_flow.extend([{"fromNode": "Amazon S3", "toNode": "AWS Lambda", "action": "ObjectCreated Event Notification"}, {"fromNode": "AWS Lambda", "toNode": "Amazon CloudWatch", "action": "Logs"}])
        resolved.append("Ambiguous_End_to_End_Flow")
    return result, resolved

def apply_patch(blueprint: AssignmentBlueprint, patches: list[dict]) -> AssignmentBlueprint:
    data = blueprint.model_dump(mode="json")
    original_components = data.get("components", []); original_modules = data.get("logicalModules", [])
    for patch in patches:
        op, path = patch.get("op"), patch.get("path", "")
        if op not in {"add", "replace", "remove"}: raise ValueError("지원하지 않는 JSON Patch 연산")
        if path in {"/components", "/logicalModules", "/components/", "/logicalModules/"}: raise ValueError("전체 배열 교체 patch는 금지됩니다")
        if path.startswith("/components/") and path.count("/") <= 2 and op in {"replace", "remove"}: raise ValueError("기존 component 삭제/교체는 금지됩니다")
        parts = [x for x in path.strip("/").split("/") if x]
        target = data
        for part in parts[:-1]: target = target[int(part)] if isinstance(target, list) else target[part]
        key = parts[-1] if parts else ""
        if isinstance(target, list):
            if op == "add": target.append(patch.get("value"))
            else: raise ValueError("배열 전체/기존 항목 patch는 금지됩니다")
        elif op == "remove": target.pop(key, None)
        else: target[key] = patch.get("value")
    if data.get("components") != original_components or data.get("logicalModules") != original_modules:
        raise ValueError("정상 component/module 배열 변경이 감지되었습니다")
    return AssignmentBlueprint.model_validate(data)
