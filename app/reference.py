import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = ROOT / "과제예시"

# 과거에 있던 vf 통합본은 더 이상 참고하지 않는다. 각 폴더가 독립 과제 예시다.
ALLOWED_SUFFIXES = {".pdf", ".md", ".sh", ".tf", ".py", ".json", ".yml", ".yaml"}

def _read_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            )
            return result.stdout or "(PDF 텍스트가 없습니다)"
        except (FileNotFoundError, subprocess.SubprocessError):
            return "(PDF 텍스트 추출 실패)"
    return path.read_text(encoding="utf-8", errors="replace")

def load_references() -> str:
    if not EXAMPLE_DIR.exists():
        return "(독립 과제 예시 디렉터리가 없습니다.)"

    chunks: list[str] = []
    # vf 및 과제예시 루트의 구형 통합본은 제외하고, 새 독립 과제 폴더만 읽는다.
    folders = sorted(p for p in EXAMPLE_DIR.iterdir() if p.is_dir() and p.name != "vf")
    for folder in folders:
        chunks.append(f"\n## 독립 과제 예시: {folder.name}")
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or ".terraform" in path.parts:
                continue
            if path.suffix.lower() not in ALLOWED_SUFFIXES and not path.name.startswith("Dockerfile"):
                continue
            text = _read_file(path)
            relative = path.relative_to(EXAMPLE_DIR)
            # 지나치게 큰 파일 하나가 다른 과제의 형식을 밀어내지 않게 한다.
            chunks.append(f"\n### {relative}\n{text[:18000]}")

    # 새 예시 전체(구형 vf 제외)를 유지한다. Gemini의 긴 컨텍스트를 활용해
    # 마지막 폴더의 채점 스크립트가 잘리지 않도록 한다.
    return "\n".join(chunks)[:400000]
