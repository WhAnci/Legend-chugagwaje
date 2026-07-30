from pathlib import Path
from app.models import *
from app.build import pdf

doc = TaskDocument(
    meta=TaskMeta(title="서버리스 방문자 카운터", assignment_number="제 1과제", duration="1시간", region="ap-northeast-2"),
    requirements=["DynamoDB Atomic Counter를 활용한 방문자 집계 API 구축", "최소 권한 IAM 정책과 예외 처리 구성"],
    precautions=["문제의 < >는 비번호 또는 사용자가 정하는 값입니다.", "모든 리소스 이름과 변수는 대소문자를 구분합니다.", "CloudShell에서 지정 리전으로 작업합니다."],
    provided_files=[ProvidedFile(name="lambda_function.py", description="방문자 집계 Lambda 함수 골격")],
    modules=[TaskModule(number=1, title="서버리스 방문자 카운터", region_notice="해당 과제 풀이는 서울(ap-northeast-2) 리전에서 진행합니다.", scenario="웹사이트 방문자 수를 실시간으로 집계하는 서버리스 API를 구축합니다.", architecture_flow="Client → API Gateway → Lambda → DynamoDB", sections=[
        TaskSection(number=1, title="DynamoDB", description="방문자 수를 저장할 테이블을 생성합니다.", tasks=["방문자 식별자를 파티션 키로 사용하도록 테이블을 생성합니다.", "온디맨드 용량 모드를 사용합니다."], specs=[SpecItem(label="Table Name", value="VisitorCount-<비번호>"), SpecItem(label="Partition Key", value="visitor_id (String)"), SpecItem(label="Billing Mode", value="PAY_PER_REQUEST")], verification=["테이블의 키 스키마와 BillingMode를 확인합니다."]),
        TaskSection(number=2, title="IAM", description="Lambda가 필요한 DynamoDB 작업만 수행하도록 실행 역할을 구성합니다.", tasks=["DynamoDB 대상 리소스에 한정된 정책을 연결합니다."], specs=[SpecItem(label="Required Actions", value="dynamodb:UpdateItem, dynamodb:GetItem")], notes=["AdministratorAccess와 같은 전체 권한 정책은 사용하지 않습니다."]),
        TaskSection(number=3, title="Lambda", description="방문자 요청을 검증하고 Atomic Counter를 갱신합니다.", tasks=["제공된 코드 골격을 활용하여 요청을 처리합니다.", "오류가 발생한 경우 적절한 HTTP 상태 코드를 반환합니다."], specs=[SpecItem(label="Runtime", value="Python 3.12"), SpecItem(label="Handler", value="lambda_function.lambda_handler"), SpecItem(label="Environment Variable", value="TABLE_NAME")], verification=["정상 요청과 필수 필드가 없는 요청의 응답을 확인합니다."]),
        TaskSection(number=4, title="API Gateway", description="외부 클라이언트가 Lambda를 호출할 수 있는 HTTP API를 구성합니다.", tasks=["POST /visit 경로를 Lambda와 연결합니다."], specs=[SpecItem(label="Method", value="POST"), SpecItem(label="Path", value="/visit")], verification=["정상 응답 본문에 누적 방문자 수가 포함되는지 확인합니다."])], verification=["CloudShell에서 정상 요청과 오류 요청을 각각 실행합니다."], cleanup=["채점 완료 후 생성한 테스트 리소스를 정리합니다."])],
    footer="클라우드컴퓨팅 제1과제 — 연습용 Mock (실제 대회 문제 아님)",
)
output = Path("/mnt/data/assignment.pdf")
output.parent.mkdir(parents=True, exist_ok=True)
pdf("", output, doc.meta.title, "assignment", doc)
print(output)
