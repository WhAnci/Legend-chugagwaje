import subprocess, tempfile
from pathlib import Path
from .models import TaskDraft

# 모델이 요구한 CLI 옵션을 빠뜨려도 결과가 검증에서 불필요하게 중단되지 않도록
# 채점 스크립트 앞에 표준 CloudShell 실행 래퍼를 붙인다.
WRAPPER = r'''#!/usr/bin/env bash
set -Eeuo pipefail

REGION="${AWS_DEFAULT_REGION:-${DEFAULT_AWS_REGION:-}}"
OUTPUT_FILE="./grading-result.json"
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --help|-h) echo "Usage: $0 [--region REGION] [--output FILE] [--dry-run] [grader options]"; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --region) : ;;
    --output) : ;;
  esac
done
while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) [[ $# -ge 2 ]] || { echo "--region requires a value" >&2; exit 3; }; REGION="$2"; shift 2 ;;
    --output) [[ $# -ge 2 ]] || { echo "--output requires a value" >&2; exit 3; }; OUTPUT_FILE="$2"; shift 2 ;;
    --dry-run) shift ;;
    *) shift ;;
  esac
done
export AWS_DEFAULT_REGION="$REGION"
if [[ "$DRY_RUN" == "1" ]]; then
  printf '{"status":"DRY_RUN","region":"%s","score":0,"total":6.0,"criteria":[]}\n' "$REGION" > "$OUTPUT_FILE"
  cat "$OUTPUT_FILE"
  exit 0
fi

'''

def normalize(draft: TaskDraft) -> TaskDraft:
    script = draft.grading_script.strip()
    # 모델이 붙인 Markdown fence 제거
    if script.startswith("```"):
        script = script.split("\n", 1)[1] if "\n" in script else script
        script = script.rsplit("```", 1)[0].rstrip()
    if not script.startswith("#!") or "--dry-run" not in script or "--region" not in script or "--output" not in script:
        script = WRAPPER + script
    elif not script.startswith("#!/usr/bin/env bash"):
        script = "#!/usr/bin/env bash\n" + script.split("\n", 1)[-1]
    # 손상된 LLM Shell은 최종 산출물에 그대로 넣지 않는다. 원본 대신
    # 문법적으로 안전한 ENVIRONMENT_ERROR 스크립트를 사용한다.
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8") as handle:
        handle.write(script + "\n"); probe = Path(handle.name)
    try:
        syntax_ok = subprocess.run(["bash", "-n", str(probe)], capture_output=True).returncode == 0
    finally:
        probe.unlink(missing_ok=True)
    if not syntax_ok:
        lines = [WRAPPER, 'RESULTS=()', 'echo "grading script syntax was repaired; inspect generated checks" >&2']
        for check in draft.checks:
            check_id = str(check.id)
            function_name = str(check.script_check)
            if not function_name.replace("_", "").isalnum(): function_name = "check_" + check_id.lower().replace("-", "_")
            lines.append(f'{function_name}() {{ echo "[{check_id}] ENVIRONMENT_ERROR (+0)"; return 2; }}')
            lines.append(f'{function_name} || true')
        lines.append('printf \'{"status":"ENVIRONMENT_ERROR","score":0,"total":6.0,"criteria":[]}\\n\' > "$OUTPUT_FILE"')
        script = "\n".join(lines)
    return draft.model_copy(update={"grading_script": script + "\n"})
