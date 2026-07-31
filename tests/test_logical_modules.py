from app.module_decomposition import decompose_document


def titles(modules):
    return [module["title"] for module in decompose_document({"modules": modules})["modules"]]


def test_alb_children_are_one_logical_module():
    assert titles([
        {"title": "Application Load Balancer"},
        {"title": "HTTP Listener"},
        {"title": "Target Group"},
        {"title": "Health Check"},
    ]) == ["Application Load Balancer"]


def test_network_firewall_children_are_one_logical_module():
    assert titles([
        {"title": "Stateful Rule Group"},
        {"title": "Firewall Policy"},
        {"title": "Firewall Endpoint"},
    ]) == ["AWS Network Firewall"]


def test_lattice_children_are_one_logical_module():
    assert titles([
        {"title": "VPC Lattice Service Network"},
        {"title": "Service Listener and Target Group"},
    ]) == ["Amazon VPC Lattice"]


def test_different_architecture_roles_remain_separate():
    result = decompose_document({"modules": [
        {"service": "Amazon VPC", "title": "Amazon VPC"},
        {"service": "AWS Lambda", "title": "AWS Lambda"},
        {"service": "Application Load Balancer", "title": "Application Load Balancer"},
    ]})
    assert [module["title"] for module in result["modules"]] == [
        "Amazon VPC", "AWS Lambda", "Application Load Balancer"
    ]
