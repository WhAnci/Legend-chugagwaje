from pathlib import Path
from app.models import *
from app.build import pdf

doc = TaskDocument(
    meta=TaskMeta(document_title="2024년도 클라우드컴퓨팅 직종 연습 과제", year=2024, occupation="Cloud Architect", title="실시간 WebSocket 채팅 시스템 구축", assignment_number="M04", duration="60분", region="ap-northeast-2", candidate_number="", judge_confirmation="(인)"),
    requirements=["AWS Management Console 접근 권한 및 CloudShell 사용 가능 환경", "기본적인 Python 및 AWS SDK 이해도"],
    precautions=["모든 리소스는 ap-northeast-2 리전에 생성합니다.", "IAM 최소 권한 원칙을 준수합니다.", "Lambda 함수는 OnConnect, OnDisconnect, OnMessage의 3개 개별 함수로 구성합니다.", "라우트 키는 $connect, $disconnect, $default만 사용합니다."],
    modules=[TaskModule(number=1, title="실시간 WebSocket 채팅 시스템 구축", region_notice="해당 과제 풀이는 ap-northeast-2 리전에서 진행합니다.", scenario="WebSocket 클라이언트의 연결 상태를 DynamoDB에 저장하고, 메시지를 연결된 클라이언트에 전달하는 실시간 채팅 시스템을 구축합니다.", architecture_flow="User → WebSocket Client → API Gateway WebSocket → Lambda → DynamoDB WebSocketConnections", sections=[
        TaskSection(number=1, title="DynamoDB 연결 정보 테이블", description="WebSocket 연결 정보를 저장할 DynamoDB 테이블을 생성합니다.", tasks=["R-01: 연결 정보를 저장할 테이블을 생성합니다."], specs=[SpecItem(label="Table Name", value="WebSocketConnections"), SpecItem(label="Partition Key", value="ConnectionId (String)"), SpecItem(label="Billing Mode", value="PAY_PER_REQUEST")], verification=["테이블 이름, 키 스키마와 온디맨드 설정을 확인합니다."]),
        TaskSection(number=2, title="Lambda 함수 및 IAM 역할", description="WebSocket 연결과 메시지를 처리하는 세 개의 Lambda 함수를 구성합니다.", tasks=["R-02: OnConnect 함수는 연결 정보를 저장합니다.", "R-03: OnDisconnect 함수는 연결 정보를 삭제합니다.", "R-04: OnMessage 함수는 연결 정보를 조회하고 PostToConnection으로 메시지를 전달합니다.", "R-05: 세 함수는 개별 Lambda 함수로 구성합니다."], specs=[SpecItem(label="Functions", value="OnConnect, OnDisconnect, OnMessage"), SpecItem(label="Runtime", value="Python 3.12"), SpecItem(label="Handler", value="lambda_function.lambda_handler"), SpecItem(label="IAM Role", value="WebSocketLambdaRole"), SpecItem(label="Inline Policy", value="WebSocketLambdaPolicy")], notes=["CloudWatch Logs 기록에 필요한 권한을 포함합니다.", "DynamoDB와 API Gateway Management API에 필요한 최소 권한을 적용합니다."]),
        TaskSection(number=3, title="API Gateway WebSocket API", description="세 개의 라우트 키를 Lambda 함수와 연결합니다.", tasks=["R-06: WebSocket API를 생성합니다.", "R-07: 각 라우트 키를 해당 Lambda 함수와 Lambda 프록시 통합으로 연결합니다.", "R-08: prod 스테이지를 배포합니다."], specs=[SpecItem(label="API Name", value="ChatWebSocketApi"), SpecItem(label="Route Key", value="$connect"), SpecItem(label="Route Key", value="$disconnect"), SpecItem(label="Route Key", value="$default"), SpecItem(label="Integration", value="Lambda proxy"), SpecItem(label="Stage", value="prod")], verification=["각 라우트 키가 올바른 Lambda 함수로 연결되는지 확인합니다."]),
        TaskSection(number=4, title="전체 시스템 검증", description="CloudShell에서 WebSocket 연결과 메시지 흐름을 검증합니다.", tasks=["R-09: 클라이언트 연결 시 DynamoDB에 ConnectionId가 저장되는지 확인합니다.", "R-10: 메시지 전송 시 연결된 클라이언트에 메시지가 전달되는지 확인합니다.", "R-11: 연결 종료 시 DynamoDB에서 ConnectionId가 삭제되는지 확인합니다."], verification=["$connect, $default, $disconnect 순서의 동작 결과를 확인합니다.", "CloudWatch Logs에서 세 Lambda 함수의 실행 결과를 확인합니다."])], cleanup=["생성한 AWS 리소스는 대회 종료 후 자동 정리합니다."])],
    footer="과제지는 AWS Cloud 대회 출제 기준에 따라 작성"
)
out = Path("/mnt/data/assignment.pdf")
out.parent.mkdir(parents=True, exist_ok=True)
pdf("", out, doc.meta.title, "assignment", doc)
print(out)
