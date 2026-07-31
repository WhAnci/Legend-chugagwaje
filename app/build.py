import html, zipfile
from pathlib import Path
import markdown
from weasyprint import HTML, CSS
from .models import TaskDraft, TaskDocument
from .consistency import check_consistency

CSS_TEXT = r'''
@page { size: A4; margin: 16mm 19mm 17mm; @bottom-right { content: counter(page); color:#68717d; font-size:8pt; } }
* { box-sizing:border-box; }
body { font-family:'Noto Sans CJK KR','Noto Sans',Arial,sans-serif; color:#111; font-size:9.5pt; line-height:1.48; word-break:keep-all; overflow-wrap:anywhere; }
.document { width:100%; }
.doc-title { text-align:center; font-size:17pt; font-weight:700; margin:0 0 9px; letter-spacing:-.2px; }
.meta-table, .files-table { width:100%; border-collapse:collapse; table-layout:fixed; margin:0 0 15px; page-break-inside:avoid; }
.meta-table td { border:1px solid #555; padding:5px 7px; height:22px; vertical-align:middle; }
.meta-table .label { width:15%; background:#f1f1f1; font-weight:700; text-align:center; }
.meta-table .wide-label { width:18%; background:#f1f1f1; font-weight:700; text-align:center; }
.meta-table .value { width:35%; }
.common-title { font-size:11.5pt; font-weight:700; margin:13px 0 5px; padding-bottom:3px; border-bottom:1.3px solid #222; page-break-after:avoid; }
.explicit-list { margin:3px 0 8px; }
.numbered-item { display:flex; align-items:flex-start; margin:3px 0; page-break-inside:avoid; }
.numbered-item .number { flex:0 0 22px; text-align:right; margin-right:7px; }
.numbered-item .content { flex:1; min-width:0; }
.files-table th, .files-table td { border:1px solid #777; padding:5px 7px; text-align:left; }
.files-table th { width:32%; background:#f0f0f0; font-weight:700; }
.code-name { font-family:'DejaVu Sans Mono',monospace; background:#f1f3f5; padding:1px 3px; }
.module-title { color:#007c91; font-size:14pt; font-weight:700; border-bottom:1.7px solid #008c9e; padding:4px 0 4px; margin:18px 0 8px; page-break-after:avoid; }
.module-title .no { margin-right:8px; }
.lead-label { font-weight:700; display:inline-block; min-width:83px; }
.lead { margin:3px 0; }
.flow { margin:7px 0 11px; padding:7px 10px; border:1px solid #9aa7ad; background:#f7f9fa; text-align:center; font-family:'DejaVu Sans Mono',monospace; font-size:8.5pt; page-break-inside:avoid; }
.architecture { margin:6px 0 16px; padding:10px 12px; border:1px solid #9aa7ad; background:#f7f9fa; font-family:'DejaVu Sans Mono',monospace; font-size:8.5pt; line-height:1.35; white-space:pre-wrap; page-break-inside:avoid; }
.section { margin:24px 0 0; padding-top:4px; page-break-inside:auto; }
.section + .section { margin-top:28px; }
.section-title { font-size:11.5pt; font-weight:700; margin:0 0 5px; color:#161616; page-break-after:avoid; }
.section-title .section-no { margin-right:5px; }
.description { margin:0 0 5px; }
.task-list, .note-list, .verify-list { margin:4px 0 7px 20px; padding:0; }
.task-list li, .note-list li, .verify-list li { margin:2px 0; page-break-inside:avoid; }
.specs { margin:6px 0 8px; padding:2px 0; page-break-inside:avoid; }
.spec-row { display:flex; width:100%; border-bottom:1px dotted #c4c8ca; min-height:20px; page-break-inside:avoid; }
.spec-label { flex:0 0 34%; font-weight:700; color:#252525; padding:2px 8px 2px 0; }
.spec-value { flex:1; padding:2px 0; }
.spec-value code, .inline-code { font-family:'DejaVu Sans Mono',monospace; background:#f0f2f3; padding:1px 3px; word-break:break-all; }
.sub-label { font-weight:700; margin:6px 0 2px; }
.bottom-footer { margin-top:18px; padding-top:7px; border-top:1px solid #555; text-align:center; font-size:8.5pt; color:#444; page-break-inside:avoid; }
.markdown-content h1 { color:#007c91; font-size:15pt; border-bottom:1.5px solid #008c9e; padding-bottom:4px; page-break-after:avoid; }
.markdown-content h2 { color:#007c91; font-size:12pt; border-bottom:1px solid #008c9e; padding-bottom:3px; page-break-after:avoid; }
.markdown-content h3 { font-size:10.5pt; page-break-after:avoid; }
.markdown-content table { width:100%; border-collapse:collapse; page-break-inside:avoid; }
.markdown-content th,.markdown-content td { border:1px solid #777; padding:4px 6px; }
.markdown-content th { background:#f1f1f1; }
pre { white-space:pre-wrap; overflow-wrap:anywhere; font-family:'DejaVu Sans Mono',monospace; font-size:8pt; background:#f4f4f4; padding:7px; border:1px solid #d0d0d0; page-break-inside:avoid; }
blockquote { margin:7px 0; padding:5px 9px; border-left:3px solid #008c9e; background:#f5f8f8; }
'''

def esc(value: object) -> str:
    return html.escape(str(value or ""))

def spec_value_html(value: str | list[str]) -> str:
    values = value if isinstance(value, list) else [value]
    values = clean_items(values)
    return "<br>".join(f"<span class='inline-code'>{esc(item)}</span>" for item in values)

def clean_items(items) -> list[str]:
    return [str(item).strip() for item in (items or []) if item is not None and str(item).strip()]

def list_html(items: list[str], cls: str = "common-list") -> str:
    values = clean_items(items)
    if not values: return ""
    return "<div class='explicit-list'>" + "".join(f"<div class='numbered-item'><span class='number'>{i}.</span><span class='content'>{esc(x)}</span></div>" for i, x in enumerate(values, 1)) + "</div>"

def document_html(doc: TaskDocument) -> str:
    m = doc.meta
    document_title = m.document_title or (f"{m.year}년도 {m.occupation} 직종 연습 과제" if m.year and m.occupation else m.title)
    out = ["<main class='document'>", f"<div class='doc-title'>{esc(document_title)}</div>"]
    out.append("<table class='meta-table'><tr>" +
        f"<td class='label'>직종명</td><td class='value'>{esc(m.occupation)}</td>" +
        f"<td class='wide-label'>과제명</td><td class='value'>{esc(m.title)}</td></tr><tr>" +
        f"<td class='label'>과제번호</td><td>{esc(m.assignment_number)}</td>" +
        f"<td class='wide-label'>경기시간</td><td>{esc(m.duration)}</td></tr><tr>" +
        f"<td class='label'>비번호</td><td>{esc(m.candidate_number or '________')}</td>" +
        f"<td class='wide-label'>심사위원 확인</td><td>{esc(m.judge_confirmation)}</td></tr></table>")
    if doc.overview:
        out.append("<div class='common-title'>1. 과제 개요</div><p class='description'>" + esc(doc.overview).replace("\n", "<br>") + "</p>")
    if doc.architecture:
        out.append("<div class='common-title'>2. 아키텍처 구성</div><pre class='architecture'>" + esc(doc.architecture) + "</pre>")
    if doc.requirements:
        out.append("<div class='common-title'>3. 요구사항</div>" + list_html(doc.requirements))
    if doc.precautions:
        out.append("<div class='common-title'>4. 유의사항</div>" + list_html(doc.precautions))
    if doc.provided_files:
        out.append("<div class='common-title'>5. 지급파일</div><table class='files-table'><tr><th>파일</th><th>설명</th></tr>")
        out.extend(f"<tr><td><span class='code-name'>{esc(f.name)}</span></td><td>{esc(f.description)}</td></tr>" for f in doc.provided_files)
        out.append("</table>")
    for module in doc.modules:
        out.append(f"<h1 class='module-title'><span class='no'>No {module.number}.</span>{esc(module.title)}</h1>")
        if module.subtitle and module.subtitle.strip().lower() != module.title.strip().lower():
            out.append(f"<p class='lead'><span class='lead-label'>역할</span>{esc(module.subtitle)}</p>")
        if module.region_notice: out.append(f"<p class='lead'><span class='lead-label'>리전 안내</span>{esc(module.region_notice)}</p>")
        if module.scenario or module.description: out.append(f"<p class='lead'><span class='lead-label'>과제 시나리오</span>{esc(module.scenario or module.description)}</p>")
        visible_specs = module.fixed_specs or module.specs
        if visible_specs:
            out.append("<div class='specs'>")
            for spec in visible_specs:
                out.append(f"<div class='spec-row'><div class='spec-label'>{esc(spec.label)}</div><div class='spec-value'>{spec_value_html(spec.value)}</div></div>")
            out.append("</div>")
        if module.architecture_flow: out.append(f"<div class='flow'>{esc(module.architecture_flow)}</div>")
        for section in module.sections:
            out.append(f"<section class='section'><h2 class='section-title'><span class='section-no'>{section.number}.</span>{esc(section.title)}</h2>")
            if section.description: out.append(f"<p class='description'>{esc(section.description)}</p>")
            # 과제지는 절차형 작업 목록이 아니라 최종 상태 명세서다.
            # legacy tasks/notes/section verification은 데이터 호환만 유지하고 렌더링하지 않는다.
            if section.specs:
                out.append("<div class='specs'>")
                for spec in section.specs:
                    out.append(f"<div class='spec-row'><div class='spec-label'>{esc(spec.label)}</div><div class='spec-value'>{spec_value_html(spec.value)}</div></div>")
                out.append("</div>")
            out.append("</section>")
    verification = clean_items(doc.verification) or [item for module in doc.modules for item in clean_items(module.verification)]
    cleanup = clean_items(doc.cleanup) or [item for module in doc.modules for item in clean_items(module.cleanup)]
    if verification: out.append("<div class='common-title'>검증 기준</div>" + list_html(verification, "verify-list"))
    if cleanup: out.append("<div class='common-title'>정리</div>" + list_html(cleanup, "note-list"))
    if doc.footer: out.append(f"<div class='bottom-footer'>{esc(doc.footer)}</div>")
    out.append("</main>")
    return "".join(out)

def pdf_from_html(content: str, target: Path) -> None:
    HTML(string=f"<html><meta charset='utf-8'><body>{content}</body></html>").write_pdf(str(target), stylesheets=[CSS(string=CSS_TEXT)])

def pdf(text: str, target: Path, title: str, kind: str, document: TaskDocument | None = None):
    if kind == "assignment" and document:
        pdf_from_html(document_html(document), target)
        return
    rendered = markdown.markdown(text, extensions=["tables", "fenced_code"])
    pdf_from_html(f"<main class='markdown-content'>{rendered}</main>", target)

def checks_rubric(draft: TaskDraft) -> str:
    lines = [f"# {draft.title} 채점기준표", "", "총점: 6.0점", ""]
    for check in draft.checks:
        expected = ", ".join(f"{key}={value}" for key, value in check.expected.items()) or "없음"
        required = "필수" if check.required else "선택"
        lines.extend([f"## [{check.id}] {check.label}", f"- 모듈: {check.module}", f"- 기대값: {expected}", f"- 배점: {check.score:g}점", f"- 여부: {required}", f"- 검사 함수: `{check.script_check}`", ""])
    return "\n".join(lines)

def safe_filename(title: str) -> str:
    value = " ".join(str(title).strip().split())
    value = "".join(ch for ch in value if ch not in '<>:"/\\|?*')
    value = value.replace(" ", "-").strip(".-")
    return (value[:100] or "aws-task") + ".zip"

def build(draft: TaskDraft, root: Path) -> list[Path]:
    if draft.checks:
        consistency_errors = check_consistency(draft)
        if consistency_errors:
            raise ValueError("산출물 정합성 검증 실패: " + "; ".join(consistency_errors[:8]))
    root.mkdir(parents=True, exist_ok=True); dep = root / "deployment"; dep.mkdir(exist_ok=True)
    pdf(draft.assignment_markdown, root / "assignment.pdf", draft.title, "assignment", draft.document)
    rubric_text = checks_rubric(draft) if draft.checks else draft.rubric_markdown
    pdf(rubric_text, root / "rubric.pdf", draft.title, "rubric")
    script = root / "grading.sh"; script.write_text(draft.grading_script, encoding="utf-8"); script.chmod(0o755)
    outputs = [root / "assignment.pdf", root / "rubric.pdf", script]
    for item in draft.deployment_files:
        path = dep / item.path; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(item.content, encoding="utf-8")
    if draft.deployment_files:
        archive = root / "deployment.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            for f in dep.rglob("*"):
                if f.is_file(): z.write(f, f.relative_to(dep))
        outputs.append(archive)
    (root / "README.md").write_text(f"# {draft.title}\n\n{draft.summary}\n\n{draft.notes}\n", encoding="utf-8")
    outputs.append(root / "README.md")
    bundle = root / safe_filename(draft.document.meta.title if draft.document else draft.title)
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for path in outputs: z.write(path, path.name)
    return [bundle]
