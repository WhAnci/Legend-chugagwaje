import re
from .service_catalog import AWS_SERVICE_CATALOG

def catalog_item(name):
    n=str(name).lower()
    return next((x for x in AWS_SERVICE_CATALOG if x["canonicalName"].lower()==n or any(a in n for a in x["aliases"])), None)

def validate_topic_candidate(topic, recent=None):
    errors=[]; expected=topic.get("expectedModules") or []
    services=[x.get("service", "") if isinstance(x,dict) else str(x) for x in expected]
    declared=[topic.get("primaryService", "")] + list(topic.get("supportingServices") or [])
    if not 3 <= len(expected) <= 5: errors.append("expectedModules는 3~5개여야 합니다.")
    if topic.get("estimatedModuleCount") != len(expected): errors.append("estimatedModuleCount와 expectedModules가 다릅니다.")
    if topic.get("primaryService") not in services: errors.append("primaryService가 expectedModules에 없습니다.")
    if len(set(s.lower() for s in services)) != len(services): errors.append("expectedModules에 서비스 중복이 있습니다.")
    if set(s.lower() for s in services) != set(s.lower() for s in declared): errors.append("supportingServices와 expectedModules가 일치하지 않습니다.")
    domains={catalog_item(s)["domain"] for s in services if catalog_item(s)}
    if len(domains)<1: errors.append("architectureDomain을 확인할 수 없습니다.")
    if not topic.get("behaviorValidation") or not topic.get("scenario"): errors.append("scenario/behaviorValidation이 없습니다.")
    if not topic.get("scenario") or not topic.get("behaviorValidation"): errors.append("end-to-end 시나리오 또는 동작 검증이 없습니다.")
    if recent:
        primary=topic.get("primaryService")
        if sum(1 for x in recent if isinstance(x,dict) and x.get("primaryService")==primary)>=2: errors.append("최근 primaryService 반복")
        if any(isinstance(x,dict) and x.get("architecturePattern")==topic.get("architecturePattern") for x in recent[-2:]): errors.append("최근 architecturePattern 반복")
        all_recent = [s for x in recent[-10:] if isinstance(x,dict) for s in [x.get("primaryService", "")] + list(x.get("supportingServices") or [])]
        for service, limit in (("AWS Lambda",4),("Amazon S3",3),("Amazon EventBridge",3)):
            if all_recent.count(service) >= limit and service in declared: errors.append(f"최근 서비스 사용 한도 초과: {service}")
    return errors

def filter_topic_candidates(items, recent=None):
    valid=[]
    for item in items or []:
        errors=validate_topic_candidate(item,recent)
        if not errors: valid.append(item)
    selected=[]; domains=set()
    for item in valid:
        domain=item.get("architectureDomain", "")
        if domain not in domains or len(selected) >= 3:
            selected.append(item); domains.add(domain)
        if len(selected) == 3: break
    return selected
