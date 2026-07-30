import asyncio, json, logging, os, re, time, uuid
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

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("aws-task-bot")
intents = discord.Intents.default(); intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@dataclass
class JobContext:
    job_id: str
    channel_id: int
    message_id: int
    user_id: int
    source: discord.Message | None = None
    notified: bool = False
    notifying: bool = False

active_jobs: dict[str, JobContext] = {}

async def timed_phase(status: discord.Message, label: str, seconds: int):
    started = time.monotonic()
    while True:
        elapsed = int(time.monotonic() - started)
        if elapsed >= seconds: break
        remain = seconds - elapsed
        await status.edit(content=f"⏳ {label}\n진행 중... (남은 예상시간 {remain // 60}분 {remain % 60:02d}초)")
        await asyncio.sleep(min(60, remain))

async def create_job(raw: str):
    logger.info("job request received: %s", raw[:160])
    service = "자동 선정"
    for candidate in ("EKS", "Kubernetes", "Cognito", "WebSocket", "AppSync", "WAF", "EventBridge", "CloudFront Functions", "Global Accelerator", "Transit Gateway", "PrivateLink", "ECR", "S3", "EC2", "ALB", "Lambda", "VPC", "RDS", "CloudFront", "DynamoDB", "ECS", "IAM"): 
        if candidate.lower() in raw.lower(): service = candidate; break
    difficulty = "고급" if "고급" in raw else "초급" if "초급" in raw else "중급"
    minutes = int(m.group(1)) if (m := re.search(r"(\d+)\s*분", raw)) else 60
    region = m.group(1) if (m := re.search(r"(ap-[a-z-]+-\d+)", raw)) else os.getenv("DEFAULT_AWS_REGION", "")
    request = TaskRequest(raw=raw, service=service, difficulty=difficulty, duration_minutes=minutes, region=region)
    # DeepSeek: 요구사항 분석과 산출물 제작을 함께 담당한다. Gemini는 최종 검토만 한다.
    logger.info("generation backend=%s service=%s region=%s", os.getenv("AGENT_BACKEND", "opencode"), service, region or "unset")
    last_errors = []
    for _ in range(int(os.getenv("MAX_RETRIES", "2")) + 1):
        current = request if not last_errors else request.model_copy(update={"raw": raw + "\n\n이전 검증 오류를 수정하라:\n" + "\n".join(last_errors)})
        logger.info("generation attempt=%d started", _ + 1)
        draft = normalize(await generate(current))
        logger.info("generation attempt=%d returned title=%s", _ + 1, draft.title)
        result = validate(draft)
        if result.ok:
            logger.info("validation passed attempt=%d", _ + 1)
            return draft, result
        logger.warning("validation failed attempt=%d errors=%s", _ + 1, "; ".join(result.errors[:5]))
        # 다음 시도는 전체 재생성이 아니라 방금 결과를 기반으로 최소 수정한다.
        request = request.model_copy(update={"previous_draft": json.dumps(draft.model_dump(), ensure_ascii=False)})
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

async def run_generation(status: discord.Message, raw: str, context: JobContext | None = None):
    started = time.monotonic()
    try:
        logger.info("job started")
        await status.edit(content="🧠 1/3 과제 구조·요구사항·채점 흐름을 검토하고 있습니다.\n예상 시간은 외부 AI 응답에 따라 변동됩니다.")
        task = asyncio.create_task(create_job(raw))
        started = time.monotonic()
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=10)
            if done: break
            elapsed = int(time.monotonic() - started)
            await status.edit(content=f"🧠 1/3 요구사항·예시·채점 흐름을 검토 중입니다.\n경과: {elapsed}초 · 예상 남은 시간: 외부 AI 응답에 따라 변동")
        draft, result = await task
        logger.info("AI generation finished elapsed=%ss", int(time.monotonic() - started))
        await status.edit(content="🛠️ 2/3 과제지·채점기준표·grading.sh·배포파일을 제작하고 있습니다.")
        result = validate(draft)
        if not result.ok: raise GeminiError("최종 검증 실패: " + "; ".join(result.errors))
        await status.edit(content="🔍 3/3 과제지·루브릭·스크립트 정합성과 실행 조건을 검증하고 있습니다.")
        review_issues = await review_draft(raw, draft)
        if review_issues:
            await status.edit(content="🔍 3/3 최종 내용 검토에서 확인 항목을 반영하는 중입니다.")
        root = Path(os.getenv("OUTPUT_DIR", "/data/jobs")) / uuid.uuid4().hex
        bundle = build(draft, root)[0]
        logger.info("artifacts built bundle=%s elapsed=%ss", bundle.name, int(time.monotonic() - started))
        await status.edit(content=f"✅ 제작 완료: **{draft.title}**\n검증 통과\n첨부된 ZIP에 과제지, 채점기준표, grading.sh, 배포파일이 포함되어 있습니다.", attachments=[discord.File(str(bundle), filename=bundle.name)])
        if context:
            await notify_original(context, f"과제 생성이 완료되었습니다.\n과제명: **{draft.title}**")
    except Exception as exc:
        logger.exception("job failed elapsed=%ss", int(time.monotonic() - started))
        try: await status.edit(content=f"❌ 과제 제작 실패\n`{str(exc)[:1500]}`")
        except Exception: logger.exception("progress message update failed")
        if context:
            await notify_original(context, f"과제 생성에 실패했습니다.\n사유: `{str(exc)[:800]}`")
    finally:
        if context: active_jobs.pop(context.job_id, None)

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

async def choose_again(interaction: discord.Interaction, topic: dict | None, previous: list[dict] | None = None):
    if topic is None:
        try:
            topics = await suggest_topics("이전 후보와 다른 AWS 추가과제 주제 3개를 제안해줘", (previous or []) + load_topic_history()[-40:])
            remember_topics(topics)
        except Exception as exc:
            await interaction.message.edit(content=f"❌ 새 주제 생성 실패: `{str(exc)[:500]}`", view=None); return
        await interaction.message.edit(content=None, embed=topics_embed(topics), view=TopicChoiceView(topics, interaction.user.id, choose_again))
        return
    await interaction.message.edit(content=f"⏳ 선택됨: **{topic.get('title', 'AWS 과제')}**\n과제 설계와 검증을 진행합니다.\n진행: 0/3 준비 중", embed=None, view=None)
    status = await interaction.channel.fetch_message(interaction.message.id)
    context = JobContext(job_id=uuid.uuid4().hex, channel_id=status.channel.id, message_id=status.id, user_id=interaction.user.id, source=status)
    active_jobs[context.job_id] = context
    logger.info("job context saved job=%s channel=%s message=%s user=%s", context.job_id, context.channel_id, context.message_id, context.user_id)
    await run_generation(status, topic_prompt(topic), context)

@bot.tree.command(name="usage", description="AI API 사용량을 확인합니다.")
async def usage_command(interaction: discord.Interaction):
    await interaction.response.send_message(usage_links() + "\n\n" + usage_summary(), ephemeral=True)

@bot.command(name="usage")
async def usage_prefix_command(ctx: commands.Context):
    await ctx.reply(usage_links() + "\n\n" + usage_summary(), mention_author=False)

@bot.tree.command(name="추가과제", description="AWS 추가과제를 생성하거나 주제 목록을 확인합니다.")
@app_commands.describe(requirements="원하는 AWS 서비스나 과제 요구사항(비워두면 AI가 주제를 추천합니다)")
async def add_task_command(interaction: discord.Interaction, requirements: str | None = None):
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
    status = await interaction.followup.send("⏳ 과제 제작을 시작했습니다. 과제 설계와 검증을 진행합니다.\n진행: 0/3 준비 중", wait=True)
    # Followup webhook 토큰은 장시간 작업 중 만료될 수 있으므로 일반 채널 메시지로 다시 가져온다.
    status = await interaction.channel.fetch_message(status.id)
    context = JobContext(job_id=uuid.uuid4().hex, channel_id=status.channel.id, message_id=status.id, user_id=interaction.user.id, source=status)
    active_jobs[context.job_id] = context
    logger.info("job context saved job=%s channel=%s message=%s user=%s", context.job_id, context.channel_id, context.message_id, context.user_id)
    await run_generation(status, raw, context)

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
    if message.content.strip().lower() in {"./usage", "!usage"}:
        await message.reply(usage_links() + "\n\n" + usage_summary(), mention_author=False)
        return
    if bot.user not in message.mentions:
        await bot.process_commands(message)
        return
    raw = message.content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip() or "추가과제"
    generic = raw.strip() in {"과제", "과제지", "추가과제", "추가 과제"} or (re.search(r"(?:추가)?과제\s*(?:생성|만들)|주제.*자동|알아서", raw, re.I) and not re.search(r"S3|EC2|ALB|Lambda|VPC|RDS|CloudFront|DynamoDB|ECS|IAM|Cognito|WebSocket|AppSync|WAF|Global.?Accelerator|PrivateLink|ECR|EKS|Kubernetes|Route.?53|Transit|EventBridge|Step.?Functions|SQS", raw, re.I))
    if generic:
        try:
            topics = await suggest_topics(raw, load_topic_history()[-40:])
            remember_topics(topics)
        except Exception as exc:
            await message.channel.send(f"❌ AI 주제 생성 실패: `{str(exc)[:800]}`"); return
        await message.channel.send(embed=topics_embed(topics), view=TopicChoiceView(topics, message.author.id, choose_again))
        return
    job_id = uuid.uuid4().hex
    context = JobContext(job_id=job_id, channel_id=message.channel.id, message_id=message.id, user_id=message.author.id, source=message)
    active_jobs[job_id] = context
    logger.info("job context saved job=%s channel=%s message=%s user=%s", job_id, context.channel_id, context.message_id, context.user_id)
    status = await message.channel.send("⏳ 과제 제작을 시작했습니다. 과제 설계와 검증을 진행합니다.\n진행: 0/3 준비 중")
    await run_generation(status, raw, context)
    await bot.process_commands(message)

def run():
    token = os.getenv("DISCORD_TOKEN")
    if not token: raise SystemExit("DISCORD_TOKEN이 필요합니다.")
    bot.run(token)
