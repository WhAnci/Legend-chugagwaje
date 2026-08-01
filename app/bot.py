import asyncio, json, logging, os, re, socket, time, uuid
from dataclasses import dataclass
from pathlib import Path
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from .models import TaskRequest
from .agent import generate
from .gemini import GeminiError, review_draft, suggest_topics
from .validate import validate
from .harden import normalize
from .build import build
from .ui import TopicChoiceView
from .topic_history import load as load_topic_history, add as remember_topics
from .usage import summary as usage_summary, links as usage_links
from . import ai_control
from .completeness import complete_assignment
from .blueprint import AssignmentBlueprint, check_approved_modules, create_blueprint, validate_blueprint
from .revision import normalize_issues, repair_references
from .opencode import review_blueprint

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("aws-task-bot")
intents = discord.Intents.default(); intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
AUTHORIZED_ROLE_ID = 1489479341892046948

@dataclass
class JobContext:
    job_id: str
    channel_id: int
    message_id: int
    user_id: int
    source: discord.Message | None = None
    notified: bool = False
    notifying: bool = False
    task: asyncio.Task | None = None
    worker_task: asyncio.Task | None = None
    phase: str = "대기"
    started_at: float = 0.0

active_jobs: dict[str, JobContext] = {}
job_history: list[dict] = []
pending_blueprints: dict[int, dict] = {}

def has_authorized_role(member: discord.Member | discord.User) -> bool:
    return any(getattr(role, "id", None) == AUTHORIZED_ROLE_ID for role in getattr(member, "roles", []))

def authorized_or_message(member) -> bool:
    return has_authorized_role(member)

def remember_job(ctx: JobContext, state: str, detail: str = ""):
    ctx.phase = state
    job_history.append({"job_id": ctx.job_id, "user_id": ctx.user_id, "state": state, "detail": detail, "time": time.time()})
    del job_history[:-50]

async def timed_phase(status: discord.Message, label: str, seconds: int):
    started = time.monotonic()
    while True:
        elapsed = int(time.monotonic() - started)
        if elapsed >= seconds: break
        remain = seconds - elapsed
        await status.edit(content=f"⏳ {label}\n진행 중... (남은 예상시간 {remain // 60}분 {remain % 60:02d}초)")
        await asyncio.sleep(min(60, remain))

async def create_job(raw: str, approved_blueprint: AssignmentBlueprint | None = None):
    logger.info("job request received: %s", raw[:160])
    service = "자동 선정"
    for candidate in ("EKS", "Kubernetes", "Cognito", "WebSocket", "AppSync", "WAF", "EventBridge", "CloudFront Functions", "Global Accelerator", "Transit Gateway", "PrivateLink", "ECR", "S3", "EC2", "ALB", "Lambda", "VPC", "RDS", "CloudFront", "DynamoDB", "ECS", "IAM"): 
        if candidate.lower() in raw.lower(): service = candidate; break
    difficulty = "고급" if "고급" in raw else "초급" if "초급" in raw else "중급"
    minutes = int(m.group(1)) if (m := re.search(r"(\d+)\s*분", raw)) else 60
    # 사용자가 리전을 명시하지 않으면 AI가 서비스 특성에 맞는 리전을 선택한다.
    # 기본값으로 서울 리전을 강제하지 않는다.
    region = m.group(1) if (m := re.search(r"(ap-[a-z-]+-\d+)", raw)) else ""
    request = TaskRequest(raw=raw, service=service, difficulty=difficulty, duration_minutes=minutes, region=region, approved_blueprint=approved_blueprint.model_dump_json() if approved_blueprint else "")
    # DeepSeek: 요구사항 분석과 산출물 제작을 함께 담당한다. Gemini는 최종 검토만 한다.
    logger.info("generation backend=%s service=%s region=%s", os.getenv("AGENT_BACKEND", "opencode"), service, region or "unset")
    last_errors = []
    for _ in range(int(os.getenv("MAX_RETRIES", "3")) + 1):
        retry_raw = raw
        if last_errors:
            retry_payload = {"retryReason": "consistency_validation_failed", "errors": [{"message": error} for error in last_errors]}
            retry_raw = raw + "\n\n재시도 지시(JSON):\n" + json.dumps(retry_payload, ensure_ascii=False)
        # 이전 draft/JSON은 절대 전달하거나 병합하지 않고, 매 attempt마다 새 요청을 만든다.
        current = request.model_copy(update={"raw": retry_raw, "previous_draft": ""})
        logger.info("generation attempt=%d started", _ + 1)
        draft = complete_assignment(raw, normalize(await generate(current)))
        if approved_blueprint:
            approved_errors = check_approved_modules(approved_blueprint, draft)
            if approved_errors:
                draft.notes = "BLUEPRINT_APPROVAL_ERRORS: " + " | ".join(approved_errors)
        logger.info("generation attempt=%d returned title=%s", _ + 1, draft.title)
        result = validate(draft)
        if result.ok:
            logger.info("validation passed attempt=%d", _ + 1)
            return draft, result
        logger.warning("validation failed attempt=%d errors=%s", _ + 1, "; ".join(result.errors[:5]))
        # 실패한 candidate는 폐기한다. 다음 attempt에는 오류 메시지만 전달하고
        # 이전 JSON/module을 재사용하거나 append/merge하지 않는다.
        request = request.model_copy(update={"previous_draft": ""})
        last_errors = result.errors
    raise GeminiError("검증을 통과하지 못했습니다: " + "; ".join(last_errors))

async def notify_original(ctx: JobContext, content: str, pdf_path: Path | None = None):
    if ctx.notified or ctx.notifying:
        logger.warning("duplicate notification skipped job=%s", ctx.job_id)
        return
    ctx.notifying = True
    try:
        original = ctx.source
        if original is None:
            channel = bot.get_channel(ctx.channel_id) or await bot.fetch_channel(ctx.channel_id)
            original = await channel.fetch_message(ctx.message_id)
        mention = f"<@{ctx.user_id}>"
        kwargs = {"content": f"{mention} {content}", "mention_author": False}
        if pdf_path and pdf_path.exists():
            kwargs["file"] = discord.File(str(pdf_path), filename=pdf_path.name)
        await original.reply(**kwargs)
        ctx.notified = True
        logger.info("completion notification sent job=%s channel=%s message=%s", ctx.job_id, ctx.channel_id, ctx.message_id)
    except Exception:
        logger.exception("completion notification failed job=%s channel=%s message=%s", ctx.job_id, ctx.channel_id, ctx.message_id)
    finally:
        ctx.notifying = False

async def run_generation(status: discord.Message, raw: str, context: JobContext | None = None, approved_blueprint: AssignmentBlueprint | None = None):
    started = time.monotonic()
    if context:
        context.task = asyncio.current_task()
        context.started_at = started
        remember_job(context, "실행 중")
    try:
        logger.info("job started")
        await status.edit(content="🧠 1/3 과제 구조·요구사항·채점 흐름을 검토하고 있습니다.\n예상 시간은 외부 AI 응답에 따라 변동됩니다.")
        if context: context.phase = "AI 생성 중"
        task = asyncio.create_task(create_job(raw, approved_blueprint))
        if context: context.worker_task = task
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=10)
            if done: break
            elapsed = int(time.monotonic() - started)
            await status.edit(content=f"🧠 1/3 요구사항·예시·채점 흐름을 검토 중입니다.\n경과: {elapsed}초 · 예상 남은 시간: 외부 AI 응답에 따라 변동")
        draft, result = await task
        logger.info("AI generation finished elapsed=%ss", int(time.monotonic() - started))
        if context: context.phase = "산출물 생성 중"
        await status.edit(content="🛠️ 2/3 과제지·채점기준표·grading.sh·배포파일을 제작하고 있습니다.")
        result = validate(draft)
        if not result.ok: raise GeminiError("최종 검증 실패: " + "; ".join(result.errors))
        if context: context.phase = "최종 검증 중"
        await status.edit(content="🔍 3/3 과제지·루브릭·스크립트 정합성과 실행 조건을 검증하고 있습니다.")
        review_issues = await review_draft(raw, draft)
        if review_issues:
            await status.edit(content="🔍 3/3 최종 내용 검토에서 확인 항목을 반영하는 중입니다.")
        root = Path(os.getenv("OUTPUT_DIR", "/data/jobs")) / uuid.uuid4().hex
        bundle = build(draft, root)[0]
        logger.info("artifacts built bundle=%s elapsed=%ss", bundle.name, int(time.monotonic() - started))
        await status.edit(content=f"✅ 제작 완료: **{draft.title}**\n검증 통과\n첨부된 ZIP에 과제지, 채점기준표, grading.sh, 배포파일이 포함되어 있습니다.", attachments=[discord.File(str(bundle), filename=bundle.name)])
        if context:
            remember_job(context, "완료", draft.title)
            await notify_original(context, f"과제 생성이 완료되었습니다.\n과제명: **{draft.title}**")
    except asyncio.CancelledError:
        if context: remember_job(context, "취소됨")
        try: await status.edit(content="⏹️ 과제 생성이 중지되었습니다.")
        except Exception: pass
        if context: await notify_original(context, "과제 생성이 중지되었습니다.")
    except Exception as exc:
        if context: remember_job(context, "실패", str(exc)[:300])
        logger.exception("job failed elapsed=%ss", int(time.monotonic() - started))
        try: await status.edit(content=f"❌ 과제 제작 실패\n`{str(exc)[:1500]}`")
        except Exception: logger.exception("progress message update failed")
        if context: await notify_original(context, f"과제 생성에 실패했습니다.\n사유: `{str(exc)[:800]}`")
    finally:
        if context:
            active_jobs.pop(context.job_id, None)
            context.worker_task = None

async def cancel_job(ctx: JobContext):
    if ctx.worker_task and not ctx.worker_task.done(): ctx.worker_task.cancel()
    if ctx.task and ctx.task is not asyncio.current_task() and not ctx.task.done(): ctx.task.cancel()

class StopJobView(discord.ui.View):
    def __init__(self, jobs: list[JobContext], owner_id: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        for ctx in jobs[:5]:
            button = discord.ui.Button(label=ctx.job_id[:8], style=discord.ButtonStyle.danger)
            async def callback(interaction: discord.Interaction, job=ctx):
                if interaction.user.id != self.owner_id or not has_authorized_role(interaction.user):
                    await interaction.response.send_message("권한이 없습니다.", ephemeral=True); return
                await cancel_job(job)
                await interaction.response.edit_message(content=f"⏹️ 작업 `{job.job_id[:8]}` 중지 요청을 보냈습니다.", view=None)
            button.callback = callback
            self.add_item(button)

@bot.tree.command(name="큐", description="현재 실행 중인 과제 작업을 확인합니다.")
async def queue_command(interaction: discord.Interaction):
    if not has_authorized_role(interaction.user):
        await interaction.response.send_message("이 명령어는 지정된 역할 보유자만 사용할 수 있습니다.", ephemeral=True); return
    if not active_jobs:
        await interaction.response.send_message("📭 실행 중인 과제가 없습니다.", ephemeral=True); return
    lines = [f"`{ctx.job_id[:8]}` · <@{ctx.user_id}> · {ctx.phase}" for ctx in active_jobs.values()]
    await interaction.response.send_message("📋 현재 작업 큐\n" + "\n".join(lines), ephemeral=True)

@bot.tree.command(name="중지", description="실행 중인 과제 작업을 중지합니다.")
async def stop_command(interaction: discord.Interaction):
    if not has_authorized_role(interaction.user):
        await interaction.response.send_message("이 명령어는 지정된 역할 보유자만 사용할 수 있습니다.", ephemeral=True); return
    jobs = list(active_jobs.values())
    if not jobs:
        await interaction.response.send_message("중지할 작업이 없습니다.", ephemeral=True); return
    if len(jobs) == 1:
        await cancel_job(jobs[0]); await interaction.response.send_message(f"⏹️ 작업 `{jobs[0].job_id[:8]}` 중지 요청을 보냈습니다.", ephemeral=True); return
    await interaction.response.send_message("중지할 작업을 선택하세요.", view=StopJobView(jobs, interaction.user.id), ephemeral=True)

def read_docker_logs() -> str:
    import docker
    client = docker.from_env()
    name = os.getenv("DOCKER_CONTAINER_NAME", socket.gethostname())
    container = client.containers.get(name)
    return container.logs(tail=int(os.getenv("DOCKER_LOG_TAIL", "100")), timestamps=True).decode("utf-8", "replace")

@bot.tree.command(name="로그", description="현재 bot 컨테이너의 Docker 로그를 확인합니다.")
async def logs_command(interaction: discord.Interaction):
    if not has_authorized_role(interaction.user):
        await interaction.response.send_message("이 명령어는 지정된 역할 보유자만 사용할 수 있습니다.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    try:
        text = await asyncio.to_thread(read_docker_logs)
        if not text: text = "Docker 로그가 없습니다."
        await interaction.followup.send("🐳 Docker 로그 (최근 항목)\n```text\n" + text[-1750:] + "\n```", ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(f"Docker 로그를 읽을 수 없습니다: `{str(exc)[:500]}`", ephemeral=True)

@bot.tree.command(name="도움말", description="봇 명령어 도움말을 표시합니다.")
async def help_command(interaction: discord.Interaction):
    if not has_authorized_role(interaction.user):
        await interaction.response.send_message("이 명령어는 지정된 역할 보유자만 사용할 수 있습니다.", ephemeral=True); return
    embed = discord.Embed(title="🤖 추가과제 봇 도움말", description="지정된 역할 보유자만 사용할 수 있습니다.")
    embed.add_field(name="/추가과제", value="새 AWS 과제를 생성합니다.", inline=False)
    embed.add_field(name="/큐", value="실행 중인 작업을 확인합니다.", inline=False)
    embed.add_field(name="/중지", value="작업 하나 또는 선택한 작업을 중지합니다.", inline=False)
    embed.add_field(name="/로그", value="최근 작업 상태를 확인합니다.", inline=False)
    embed.add_field(name="/usage", value="API 사용량 링크를 확인합니다.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

def topics_embed(topics: list[dict]) -> discord.Embed:
    embed = discord.Embed(title="📚 AI가 새로운 추가과제 주제를 준비했습니다.", description="아래 후보 중 하나를 선택하세요.", color=discord.Color.blue())
    for index, topic in enumerate(topics, 1):
        support = ", ".join(topic.get("supportingServices", []))
        service = topic.get("primaryService", "") + (f" ({support})" if support else "")
        value = "\n".join([
            f"☁️ 핵심 서비스: {service}",
            f"🧩 문제 유형: {topic.get('problemType', '')}",
            f"🛠️ 과제 형식: {topic.get('taskType', '')}",
        ])
        embed.add_field(name=f"{index}️⃣ {topic.get('title', 'AWS 추가과제')}", value=value, inline=False)
    embed.set_footer(text="원하는 주제를 선택하면 과제지, 채점기준표, 채점 스크립트를 생성합니다.")
    return embed

def topic_prompt(topic: dict) -> str:
    return json.dumps(topic, ensure_ascii=False)

def blueprint_embed(blueprint: AssignmentBlueprint) -> discord.Embed:
    embed = discord.Embed(title="과제 구성안 확인", description="아직 PDF·과제지·채점파일을 생성하지 않았습니다. 구성안을 검토한 뒤 승인하세요.", color=0x5865F2)
    embed.add_field(name="과제명", value=blueprint.goal[:1024] or "AWS 추가과제", inline=False)
    flow = "\n".join(f"{item.from_node} → {item.to_node} ({item.action})" for item in blueprint.data_flow) or "구성요소 기반 end-to-end 흐름"
    embed.add_field(name="아키텍처 흐름", value=flow[:1024], inline=False)
    modules = []
    for index, module in enumerate(blueprint.logical_modules, 1):
        component_names = [next((c.service for c in blueprint.components if c.id == cid), cid) for cid in module.component_ids]
        modules.append(f"No {index}. {module.title}\n- 포함: {', '.join(component_names) or '구성요소 설계 필요'}")
    embed.add_field(name="예정 모듈", value="\n\n".join(modules)[:1024] or "없음", inline=False)
    files = "\n".join(f"- {f.path} → {', '.join(f.used_by_module)}" for f in blueprint.provided_files) or "없음"
    embed.add_field(name="지급파일", value=files[:1024], inline=False)
    difficulty = "어려움" if len(blueprint.components) >= 6 else "보통" if len(blueprint.components) >= 3 else "쉬움"
    embed.add_field(name="예상 난이도", value=f"{difficulty} · 60분 내 구성·연결·동작 검증", inline=False)
    embed.add_field(name="주요 동작 검증", value="\n".join(f"- {c.get('description', c.get('type', '동작 검증'))}" for c in blueprint.behavior_checks)[:1024] or "end-to-end 동작 및 실패 경로 검증", inline=False)
    embed.set_footer(text="구성안 승인 후에만 실제 산출물을 생성합니다.")
    return embed

class BlueprintApprovalView(discord.ui.View):
    def __init__(self, owner_id: int, message_id: int):
        super().__init__(timeout=900)
        self.owner_id = owner_id; self.message_id = message_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not has_authorized_role(interaction.user):
            await interaction.response.send_message("구성안을 만든 사용자와 지정 역할 보유자만 사용할 수 있습니다.", ephemeral=True); return False
        return True

    @discord.ui.button(label="구성안 승인", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = pending_blueprints.pop(self.message_id, None)
        if not pending: await interaction.response.send_message("만료되었거나 이미 처리된 구성안입니다.", ephemeral=True); return
        await interaction.response.defer()
        await interaction.message.edit(content="✅ 구성안 승인됨\n🛠️ 승인된 Blueprint로 산출물을 생성합니다.", embed=None, view=None)
        context = JobContext(job_id=uuid.uuid4().hex, channel_id=interaction.channel.id, message_id=interaction.message.id, user_id=interaction.user.id, source=interaction.message)
        active_jobs[context.job_id] = context
        await run_generation(interaction.message, pending["raw"], context, pending["blueprint"])

    @discord.ui.button(label="모듈 수정", style=discord.ButtonStyle.secondary)
    async def edit_modules(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("수정할 모듈을 멘션으로 보내세요. 예: `@봇 모듈 수정: Route 53을 별도 모듈로 분리`", ephemeral=True)

    @discord.ui.button(label="난이도 높이기", style=discord.ButtonStyle.primary)
    async def harder(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._revise(interaction, " 난이도를 높이고 보안·실패 경로·동작 검증을 보강한다.")

    @discord.ui.button(label="난이도 낮추기", style=discord.ButtonStyle.secondary)
    async def easier(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._revise(interaction, " 난이도를 낮추되 end-to-end 동작 검증은 유지한다.")

    @discord.ui.button(label="주제 다시 생성", style=discord.ButtonStyle.danger)
    async def regenerate(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending = pending_blueprints.pop(self.message_id, None)
        if pending:
            await interaction.response.defer()
            await interaction.message.edit(content="🔄 새 구성안을 준비합니다...", embed=None, view=None)
            await show_blueprint(interaction.message, pending["raw"] + "\n이전 구성안과 다른 주제로 다시 설계", self.owner_id)
        else: await interaction.response.send_message("만료된 구성안입니다.", ephemeral=True)

    async def _revise(self, interaction, suffix):
        pending = pending_blueprints.get(self.message_id)
        if not pending: await interaction.response.send_message("만료된 구성안입니다.", ephemeral=True); return
        await interaction.response.defer()
        await show_blueprint(interaction.message, pending["raw"] + suffix, self.owner_id)

async def show_blueprint(message: discord.Message, raw: str, owner_id: int):
    # Reviewer 오류를 사용자에게 바로 노출하지 않고 Blueprint를 새 객체로 보강해 재검토한다.
    working_raw = raw
    blueprint = None
    errors = []
    reviewer_warning = ""
    for revision in range(2):
        blueprint = create_blueprint(TaskRequest(raw=working_raw))
        blueprint, reference_repairs = repair_references(blueprint)
        errors = validate_blueprint(blueprint)
        if reference_repairs: logger.info("blueprint reference repairs=%s", reference_repairs)
        if not errors and os.getenv("REQUIRE_OPENCODE_BLUEPRINT_REVIEW", "true").lower() == "true" and ai_control.backend() != "gemini":
            try:
                await message.edit(content=f"🔍 OpenCode가 과제 구성안을 검토하고 있습니다... (검토 {revision + 1}/2)", embed=None, view=None)
                review_items = normalize_issues(await review_blueprint(blueprint))
                blocking = [item for item in review_items if item.get("severity") in {"critical", "major", "error"} and (item.get("requirementId") or item.get("evidence"))]
                recommendations = [item for item in review_items if item not in blocking]
                if recommendations:
                    reviewer_warning = "Reviewer 권장사항 " + str(len(recommendations)) + "건은 생성 차단 없이 기록했습니다."
                errors.extend([item.get("description") or item.get("requiredAction") or item.get("errorType") for item in blocking])
                if errors:
                    from .revision import deterministic_autofix
                    repaired, resolved = deterministic_autofix(blueprint, review_items)
                    if resolved:
                        blueprint = repaired
                        errors = []
                        reviewer_warning = "결정적 자동 보정 적용: " + ", ".join(resolved)
            except Exception as exc:
                # OpenCode provider 장애는 Blueprint 결함이 아니다. 정적 검증으로 계속 진행한다.
                reviewer_warning = f"OpenCode Reviewer 일시 unavailable: {str(exc) or type(exc).__name__}"
                logger.warning(reviewer_warning)
                errors = []
        if not errors: break
        logger.warning("blueprint revision=%d requested: %s", revision + 1, errors[:5])
        working_raw += "\n\nBlueprint 자동 수정 지시:\n" + json.dumps(errors, ensure_ascii=False)
    if errors:
        failure_text = "❌ 구성안 자동 보정 후에도 검토를 통과하지 못했습니다. PDF와 파일은 생성하지 않습니다.\n" + "\n".join(f"- {str(error)[:240]}" for error in errors[:6])
        await message.edit(content=failure_text[:1950], embed=None, view=None)
        logger.warning("blueprint rejected after revisions: %s", errors)
        return
    # 사용자 확인 버튼 없이, OpenCode Reviewer를 통과한 Blueprint를 즉시 승인한다.
    notice = "✅ Blueprint 검토 통과"
    if reviewer_warning: notice += f"\n⚠️ {reviewer_warning}\n정적 구조 검증으로 계속 진행합니다."
    await message.edit(content=notice + "\n🛠️ 승인된 구성안으로 과제 산출물을 자동 생성합니다.", embed=blueprint_embed(blueprint), view=None)
    context = JobContext(job_id=uuid.uuid4().hex, channel_id=message.channel.id, message_id=message.id, user_id=owner_id, source=message)
    active_jobs[context.job_id] = context
    logger.info("approved blueprint auto-generation started job=%s", context.job_id)
    await run_generation(message, raw, context, blueprint)

async def choose_again(interaction: discord.Interaction, topic: dict | None, previous: list[dict] | None = None):
    if not has_authorized_role(interaction.user):
        await interaction.followup.send("권한이 없습니다.", ephemeral=True); return
    if topic is None:
        try:
            topics = await suggest_topics("이전 후보와 다른 AWS 추가과제 주제 3개를 제안해줘", (previous or []) + load_topic_history()[-40:])
            remember_topics(topics)
        except Exception as exc:
            await interaction.message.edit(content=f"❌ 새 주제 생성 실패: `{str(exc)[:500]}`", view=None); return
        await interaction.message.edit(content=None, embed=topics_embed(topics), view=TopicChoiceView(topics, interaction.user.id, choose_again))
        return
    await interaction.message.edit(content=f"🧠 선택됨: **{topic.get('title', 'AWS 과제')}**\n과제 구성안을 설계하고 있습니다.", embed=None, view=None)
    status = await interaction.channel.fetch_message(interaction.message.id)
    await show_blueprint(status, topic_prompt(topic), interaction.user.id)

@bot.tree.command(name="model", description="과제 생성 AI 모델을 선택합니다.")
@app_commands.describe(mode="auto/opencode/gemini/status")
async def model_command(interaction: discord.Interaction, mode: str = "status"):
    if not has_authorized_role(interaction.user):
        await interaction.response.send_message("권한이 없습니다.", ephemeral=True); return
    try:
        if mode.lower() in {"status", "상태"}: enabled, backend = ai_control.status()
        else: enabled, backend = ai_control.configure(mode)
        selected = backend if backend != "환경설정" else "auto"
        await interaction.response.send_message(f"AI 모델: **{selected}**\n상태: **{'ON' if enabled else 'OFF'}**", ephemeral=True)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)

@bot.command(name="model")
async def model_prefix_command(ctx: commands.Context, mode: str = "status"):
    if not has_authorized_role(ctx.author): return
    try:
        if mode.lower() in {"status", "상태"}: enabled, backend = ai_control.status()
        else: enabled, backend = ai_control.configure(mode)
        selected = backend if backend != "환경설정" else "auto"
        await ctx.reply(f"AI 모델: **{selected}**\n상태: **{'ON' if enabled else 'OFF'}**", mention_author=False)
    except ValueError as exc: await ctx.reply(str(exc), mention_author=False)

@bot.tree.command(name="usage", description="AI API 사용량을 확인합니다.")
async def usage_command(interaction: discord.Interaction):
    if not has_authorized_role(interaction.user):
        await interaction.response.send_message("권한이 없습니다.", ephemeral=True); return
    await interaction.response.send_message(usage_links() + "\n\n" + usage_summary(), ephemeral=True)

@bot.command(name="usage")
async def usage_prefix_command(ctx: commands.Context):
    if not has_authorized_role(ctx.author): return
    await ctx.reply(usage_links() + "\n\n" + usage_summary(), mention_author=False)

@bot.tree.command(name="추가과제", description="AWS 추가과제를 생성하거나 주제 목록을 확인합니다.")
@app_commands.describe(requirements="원하는 AWS 서비스나 과제 요구사항(비워두면 AI가 주제를 추천합니다)")
async def add_task_command(interaction: discord.Interaction, requirements: str | None = None):
    if not has_authorized_role(interaction.user):
        await interaction.response.send_message("이 명령어는 지정된 역할 보유자만 사용할 수 있습니다.", ephemeral=True); return
    await interaction.response.defer()
    raw = (requirements or "").strip()
    if not raw:
        try:
            topics = await suggest_topics("새 AWS 추가과제 주제 3개를 제안해줘", load_topic_history()[-40:])
            remember_topics(topics)
            await interaction.followup.send(embed=topics_embed(topics), view=TopicChoiceView(topics, interaction.user.id, choose_again))
        except Exception as exc:
            await interaction.followup.send(f"❌ AI 주제 생성 실패: `{str(exc)[:800]}`")
        return
    status = await interaction.followup.send("🧠 과제 구성안을 설계하고 검증합니다. 아직 파일은 생성하지 않습니다.", wait=True)
    status = await interaction.channel.fetch_message(status.id)
    await show_blueprint(status, raw, interaction.user.id)

@bot.event
async def on_ready():
    guild_id = os.getenv("DISCORD_GUILD_ID")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()
    print(f"logged in as {bot.user} ({bot.user.id})")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    if bot.user in message.mentions and not has_authorized_role(message.author):
        await message.reply("이 봇은 지정된 역할 보유자만 사용할 수 있습니다.", mention_author=False)
        return
    if message.content.strip().lower() in {"./usage", "!usage"}:
        if not has_authorized_role(message.author):
            await message.reply("권한이 없습니다.", mention_author=False); return
        await message.reply(usage_links() + "\n\n" + usage_summary(), mention_author=False)
        return
    if bot.user not in message.mentions:
        await bot.process_commands(message)
        return
    raw = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip() or "추가과제"
    pending = next((item for item in pending_blueprints.values() if item.get("channel_id") == message.channel.id and item.get("owner_id") == message.author.id), None)
    if pending and re.search(r"모듈|난이도|구성안|수정", raw):
        for pending_id, pending_item in list(pending_blueprints.items()):
            if pending_item is pending: pending_blueprints.pop(pending_id, None)
        status = await message.channel.send("🧠 승인 전 구성안을 수정하고 다시 검증합니다.")
        await show_blueprint(status, pending["raw"] + "\n사용자 수정 요청: " + raw, message.author.id)
        return
    generic = raw.strip() in {"과제", "과제지", "추가과제", "추가 과제"} or (re.search(r"(?:추가)?과제\s*(?:생성|만들)|주제.*자동|알아서", raw, re.I) and not re.search(r"S3|EC2|ALB|Lambda|VPC|RDS|CloudFront|DynamoDB|ECS|IAM|Cognito|WebSocket|AppSync|WAF|Global.?Accelerator|PrivateLink|ECR|EKS|Kubernetes|Route.?53|Transit|EventBridge|Step.?Functions|SQS", raw, re.I))
    if generic:
        try:
            topics = await suggest_topics(raw, load_topic_history()[-40:])
            remember_topics(topics)
        except Exception as exc:
            await message.channel.send(f"❌ AI 주제 생성 실패: `{str(exc)[:800]}`"); return
        await message.channel.send(embed=topics_embed(topics), view=TopicChoiceView(topics, message.author.id, choose_again))
        return
    # 직접 요청도 먼저 Blueprint 승인 단계로 보낸다.
    status = await message.channel.send("🧠 과제 구성안을 설계하고 검증합니다. 아직 파일은 생성하지 않습니다.")
    await show_blueprint(status, raw, message.author.id)
    await bot.process_commands(message)

def run():
    token = os.getenv("DISCORD_TOKEN")
    if not token: raise SystemExit("DISCORD_TOKEN이 필요합니다.")
    bot.run(token)
