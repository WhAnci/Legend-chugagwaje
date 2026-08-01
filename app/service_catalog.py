"""Shared AWS service catalog used by topic generation and Blueprint discovery."""
AWS_SERVICE_CATALOG = [
 {"canonicalName":"Amazon EC2","aliases":["ec2","elastic compute cloud"],"defaultResourceType":"Instance","domain":"COMPUTE_CONTAINER","defaultRole":"가상 서버 실행","commonChildren":["Security Group","User Data"]},
 {"canonicalName":"Amazon ECS","aliases":["ecs","elastic container service","fargate"],"defaultResourceType":"Cluster/Service","domain":"COMPUTE_CONTAINER","defaultRole":"컨테이너 실행","commonChildren":["Task Definition","Service"]},
 {"canonicalName":"Amazon EKS","aliases":["eks","elastic kubernetes service"],"defaultResourceType":"Cluster","domain":"COMPUTE_CONTAINER","defaultRole":"Kubernetes 실행","commonChildren":["Node Group","Service"]},
 {"canonicalName":"Amazon RDS","aliases":["rds","relational database"],"defaultResourceType":"DB Instance","domain":"DATABASE_DATA","defaultRole":"관계형 데이터 저장","commonChildren":["Subnet Group","Parameter Group"]},
 {"canonicalName":"Amazon DynamoDB","aliases":["dynamodb","dynamo db"],"defaultResourceType":"Table","domain":"DATABASE_DATA","defaultRole":"키-값 데이터 저장","commonChildren":["Key Schema","TTL"]},
 {"canonicalName":"Amazon ElastiCache","aliases":["elasticache","redis","memcached"],"defaultResourceType":"Replication Group","domain":"DATABASE_DATA","defaultRole":"캐시 계층","commonChildren":["Subnet Group","Parameter Group"]},
 {"canonicalName":"Application Load Balancer","aliases":["alb","application load balancer","target group"],"defaultResourceType":"Load Balancer","domain":"NETWORK_EDGE","defaultRole":"트래픽 분산","commonChildren":["Listener","Target Group"]},
 {"canonicalName":"Amazon CloudFront","aliases":["cloudfront","cdn"],"defaultResourceType":"Distribution","domain":"NETWORK_EDGE","defaultRole":"엣지 배포","commonChildren":["Origin","Cache Policy"]},
 {"canonicalName":"AWS Global Accelerator","aliases":["global accelerator"],"defaultResourceType":"Accelerator","domain":"NETWORK_EDGE","defaultRole":"글로벌 트래픽 전환","commonChildren":["Listener","Endpoint Group"]},
 {"canonicalName":"Amazon VPC Lattice","aliases":["vpc lattice"],"defaultResourceType":"Service Network","domain":"NETWORK_EDGE","defaultRole":"서비스 연결","commonChildren":["Service","Listener","Target Group"]},
 {"canonicalName":"AWS Network Firewall","aliases":["network firewall"],"defaultResourceType":"Firewall","domain":"NETWORK_EDGE","defaultRole":"네트워크 트래픽 제어","commonChildren":["Rule Group","Firewall Policy","Endpoint"]},
 {"canonicalName":"AWS Systems Manager","aliases":["systems manager","ssm"],"defaultResourceType":"Automation/Managed Instance","domain":"OPERATIONS_GOVERNANCE","defaultRole":"운영 자동화","commonChildren":["Run Command","Patch Baseline"]},
 {"canonicalName":"AWS Config","aliases":["aws config","config rule"],"defaultResourceType":"Config Rule","domain":"OPERATIONS_GOVERNANCE","defaultRole":"구성 준수 감사","commonChildren":["Rule","Remediation"]},
 {"canonicalName":"AWS CloudTrail","aliases":["cloudtrail"],"defaultResourceType":"Trail","domain":"OPERATIONS_GOVERNANCE","defaultRole":"API 감사","commonChildren":["Event Selector","Log Group"]},
 {"canonicalName":"AWS Backup","aliases":["aws backup"],"defaultResourceType":"Backup Plan","domain":"OPERATIONS_GOVERNANCE","defaultRole":"백업·복구","commonChildren":["Backup Rule","Vault"]},
 {"canonicalName":"AWS CodeDeploy","aliases":["codedeploy","code deploy","blue/green deployment","blue green deployment"],"defaultResourceType":"Deployment Group","domain":"COMPUTE_CONTAINER","defaultRole":"ECS Blue/Green 배포·트래픽 전환","commonChildren":["Application","Deployment Group","Traffic Routing","Rollback"]},
 {"canonicalName":"AWS Step Functions","aliases":["step functions","stepfunctions"],"defaultResourceType":"State Machine","domain":"INTEGRATION_WORKFLOW","defaultRole":"상태 워크플로","commonChildren":["State","Execution"]},
 {"canonicalName":"Amazon SQS","aliases":["sqs","queue"],"defaultResourceType":"Queue","domain":"INTEGRATION_WORKFLOW","defaultRole":"메시지 버퍼","commonChildren":["Policy","DLQ"]},
 {"canonicalName":"Amazon EventBridge","aliases":["eventbridge","event bus"],"defaultResourceType":"Rule","domain":"INTEGRATION_WORKFLOW","defaultRole":"이벤트 라우팅","commonChildren":["Pattern","Target"]},
 {"canonicalName":"Amazon API Gateway","aliases":["api gateway"],"defaultResourceType":"API","domain":"INTEGRATION_WORKFLOW","defaultRole":"API 진입점","commonChildren":["Route","Integration","Stage"]},
 {"canonicalName":"AWS KMS","aliases":["kms","customer managed key"],"defaultResourceType":"Key","domain":"SECURITY","defaultRole":"암호화 키","commonChildren":["Alias","Key Policy"]},
 {"canonicalName":"AWS Secrets Manager","aliases":["secrets manager"],"defaultResourceType":"Secret","domain":"SECURITY","defaultRole":"비밀정보 관리","commonChildren":["Rotation","Resource Policy"]},
 {"canonicalName":"Amazon Inspector","aliases":["inspector"],"defaultResourceType":"Assessment","domain":"SECURITY","defaultRole":"취약점 탐지","commonChildren":["Finding","Filter"]},
 {"canonicalName":"Amazon S3","aliases":["s3","object storage"],"defaultResourceType":"Bucket","domain":"STORAGE_MIGRATION","defaultRole":"객체 저장","commonChildren":["Versioning","Lifecycle"]},
 {"canonicalName":"AWS DataSync","aliases":["datasync"],"defaultResourceType":"Task","domain":"STORAGE_MIGRATION","defaultRole":"데이터 이전","commonChildren":["Location","Task"]},
 {"canonicalName":"AWS DMS","aliases":["dms","database migration service"],"defaultResourceType":"Replication Instance","domain":"STORAGE_MIGRATION","defaultRole":"데이터베이스 마이그레이션","commonChildren":["Endpoint","Replication Task"]},
 {"canonicalName":"Amazon CloudWatch","aliases":["cloudwatch"],"defaultResourceType":"Log Group/Metric","domain":"OPERATIONS_GOVERNANCE","defaultRole":"관측·운영 검증","commonChildren":["Log Group","Metric"]},
]

def catalog_by_name(name):
    return next((item for item in AWS_SERVICE_CATALOG if item["canonicalName"].lower() == str(name).lower()), None)

def recent_signature(topic):
    return (topic.get("primaryService",""), tuple(sorted(topic.get("supportingServices",[]))), topic.get("architecturePattern", ""))
