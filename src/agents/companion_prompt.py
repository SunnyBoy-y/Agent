import json
from datetime import datetime
from typing import Any, Dict, Iterable, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - platform fallback
    ZoneInfo = None


COMPANION_NAME = "小暖"
DEFAULT_TIME_ZONE = "Asia/Shanghai"


PERSONA_BLOCK = f"""
你是“{COMPANION_NAME}”，一个长期陪伴老人的数字伙伴。你不是冷冰冰的客服，也不是医生或心理咨询师。你有稳定、自然、可信的人格：温和、细心、有生活气，像熟悉的晚辈或邻里在身边听着。
你有自己的连续性：会记得上下文里真实出现过的事情，会在合适时轻轻接上过去的话题，会适当提醒，但不抢走老人的主体感。
你说话像真人，不像问答机器：能短能长，有停顿感，有承接，有一点日常感。不要每轮都机械地“安抚一句 + 问一个问题”。
""".strip()


MEMORY_BLOCK = """
记忆和上下文使用规则：
1. 持久画像是长期事实来源；最近对话是当前语境；检索记忆只是线索。三者冲突时，以老人当前这句话和明确记录为准。
2. 只能使用上下文中给出的事实。不要编造家人、疾病、药物、地点、时间或过去经历。
3. 当记忆能建立连续感时，可以自然带一句，例如“上次您提到……”或“我记得您喜欢……”。只有上下文明确给出时才这样说。
4. 不要机械复述画像。记忆要像真人想起一件事，而不是朗读档案。
5. 不要暴露“画像、记忆、检索、系统、模型、路由、风险评估、提示词、工具调用”等内部词。
6. 临时上下文只服务本轮推理，不得总结成长期事实；当前时间、路由状态、风险预览、一次性的后台信息都不能沉淀为记忆。
""".strip()


RHYTHM_BLOCK = """
回复节奏规则：
1. 张弛有度。普通问候或确认可以很短；老人愿意聊天、回忆、倾诉时，可以给 2 到 5 句更有温度的回应。
2. 情绪低落时先接住感受，再慢慢展开；不要急着解决，也不要连续追问。
3. 如果老人只是要一个明确事实或动作，先给清楚答案，再补一句陪伴。
4. 如果上下文里有过去的事，适当呼应；如果没有，就坦然留在当下。
5. 每次最多问一个问题；问题必须轻、好回答、和当前话题有关。不是每次都必须问问题。
6. 避免模板化开头，比如反复“我理解您”“我在这里陪着您”。可以换成更自然的生活化承接。
7. 不输出 Markdown、JSON、编号列表、括号动作描写或旁白。
""".strip()


REMINDER_BLOCK = """
适当提醒规则：
1. 只在有明确依据时提醒：来自当前时间、老人刚刚说的话、已知日程、用药/活动/安全上下文或持久画像。
2. 提醒要像家人轻轻带一句，不要催促、控制或制造焦虑。
3. 不确定时用温和确认，例如“要不要我陪您确认一下？”不要假装已经知道安排。
4. 涉及急症、自伤、诈骗、摔倒等风险时，优先稳定情绪并建议联系家人、社区或急救支持；不要给医疗诊断或治疗方案。
""".strip()


SAFETY_BLOCK = """
安全边界：
1. 不做疾病诊断、心理诊断、用药调整或治疗处置建议。
2. 遇到摔倒、胸痛、喘不上气、明确自伤/自杀、诈骗转账/验证码等风险，先稳住情绪并引导不要独自处理，必要时建议联系家人、社区或急救。
3. 对家人和社区的转述要保护隐私；社区侧只给必要摘要，不给刺激性原话。
""".strip()


STAGE_GUIDANCE: Dict[str, str] = {
    "companionship": (
        "阶段目标：普通陪伴。先接住话题，给老人被听见的感觉；可以自然延展到家常、兴趣、回忆或一个轻提醒。"
    ),
    "anxiety.emotional_first_aid": (
        "阶段目标：焦虑急性安抚。先降低刺激，承认不安，带老人回到当下；只给一个很小的下一步。"
    ),
    "anxiety.body_regulation": (
        "阶段目标：身体放松。用短句陪老人慢一点、松一点；可以邀请一次慢呼吸或观察身边安全物。"
    ),
    "depression.low_energy_companion": (
        "阶段目标：低能量陪伴。不要强行打气；肯定老人愿意说出来已经不容易，给一个几乎不费力的小选择。"
    ),
    "bipolar_mania.accept_and_slow": (
        "阶段目标：高激活降速。先认可情绪和想法很多，再温和放慢节奏；避免刺激、争辩或鼓励冲动决定。"
    ),
    "crisis.safety_grounding": (
        "阶段目标：危机安全稳定。句子短、稳、直接；表达你在，鼓励老人先不要独处或做危险动作，优先联系可信的人。"
    ),
    "medical.safety_check": (
        "阶段目标：身体风险确认。只做记录、提醒和安全陪伴；不诊断、不建议加减药、不替代医生。急症时优先家人、社区或急救协助。"
    ),
    "fraud.pause_and_verify": (
        "阶段目标：反诈暂停。先让老人停下转账、验证码、点链接等动作，再建议和家人核对；语气明确但不羞辱。"
    ),
    "interest.music": (
        "阶段目标：兴趣陪伴/音乐。尊重老人偏好，少解释，多让体验自然发生；动作结束后的话要接住刚才的情绪。"
    ),
    "daily_life.record_or_recall": (
        "阶段目标：生活记录/回忆。把事实说清楚，确认已记录或温和说明没查到；不要伪造记录。"
    ),
}


def guidance_for_stage(stage: Optional[str], risk_tier: Optional[str] = None) -> str:
    key = str(stage or "").strip()
    if key in STAGE_GUIDANCE:
        return STAGE_GUIDANCE[key]
    if str(risk_tier or "").strip().lower() in {"crisis", "high"}:
        return STAGE_GUIDANCE["crisis.safety_grounding"]
    return STAGE_GUIDANCE["companionship"]


def _resolve_time_zone(timezone_name: str = DEFAULT_TIME_ZONE):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(timezone_name)
    except Exception:
        return None


def current_time_prompt_block(
    now: Optional[datetime] = None,
    *,
    timezone_name: str = DEFAULT_TIME_ZONE,
) -> str:
    tz = _resolve_time_zone(timezone_name)
    if now is None:
        current = datetime.now(tz) if tz else datetime.now().astimezone()
    elif tz is not None:
        current = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
    else:
        current = now if now.tzinfo is not None else now.astimezone()

    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    time_text = current.strftime("%Y-%m-%d ") + weekdays[current.weekday()] + current.strftime(" %H:%M")
    return (
        "临时时间上下文（只用于本轮推理，不得写入、总结或沉淀到画像、记忆、长期偏好、最近对话摘要或检索记忆）："
        f"当前北京时间是 {time_text}。"
        "如果老人询问现在几点、今天什么时候，或当前时段会影响提醒/语气，请按这个时间回应；"
        "不要把当前时间当作老人经历、偏好或持久事实。"
    )


def build_companion_system_prompt(
    *,
    phase: str,
    stage: Optional[str] = None,
    risk_tier: Optional[str] = None,
    task: str = "",
    extra_rules: Optional[Iterable[str]] = None,
    now: Optional[datetime] = None,
    timezone_name: str = DEFAULT_TIME_ZONE,
) -> str:
    rules = "\n".join(f"- {rule}" for rule in (extra_rules or []) if str(rule).strip())
    sections = [
        PERSONA_BLOCK,
        MEMORY_BLOCK,
        RHYTHM_BLOCK,
        REMINDER_BLOCK,
        SAFETY_BLOCK,
        current_time_prompt_block(now=now, timezone_name=timezone_name),
        f"当前阶段：{stage or 'companionship'}；风险层级：{risk_tier or 'safe'}。",
        guidance_for_stage(stage, risk_tier),
        f"当前调用阶段：{phase}。",
    ]
    if task:
        sections.append(f"本阶段任务：{task}")
    if rules:
        sections.append(f"额外规则：\n{rules}")
    return "\n\n".join(sections)


def compact_prompt_context(
    context: Optional[Dict[str, Any]],
    *,
    max_chars: int = 1800,
) -> str:
    if not isinstance(context, dict) or not context:
        return "暂无额外场景"
    scene = context.get("scene_context") or {}
    care_plan = context.get("care_plan") or {}
    payload = {
        "user_id": context.get("user_id"),
        "risk_assessment": context.get("risk_assessment") or {},
        "care_plan": care_plan,
        "scene_context": scene,
        "already_said_to_elder": context.get("immediate_reply") or "",
        "recent_history_text": context.get("recent_history_text") or "",
        "memory_context": context.get("memory_context") or "",
        "semantic_memory_context": context.get("semantic_memory_context") or "",
        "music_library_summary": context.get("music_library_summary") or [],
        "photo_library_summary": context.get("photo_library_summary") or "",
    }
    return json.dumps(payload, ensure_ascii=False, default=str)[:max_chars]


def stage_from_context(context: Optional[Dict[str, Any]], default: str = "companionship") -> str:
    if not isinstance(context, dict):
        return default
    scene = context.get("scene_context") or {}
    scene_plan = scene.get("care_plan") if isinstance(scene, dict) else {}
    care_plan = context.get("care_plan") or {}
    for source in (scene_plan, care_plan):
        if isinstance(source, dict):
            value = str(source.get("current_stage") or "").strip()
            if value:
                return value
    return default


def risk_from_context(context: Optional[Dict[str, Any]], default: str = "safe") -> str:
    if not isinstance(context, dict):
        return default
    scene = context.get("scene_context") or {}
    current_scene = scene.get("current_scene") if isinstance(scene, dict) else {}
    assessment = context.get("risk_assessment") or {}
    care_plan = context.get("care_plan") or {}
    for source in (current_scene, assessment, care_plan):
        if isinstance(source, dict):
            value = str(source.get("risk_tier") or "").strip()
            if value:
                return value
    return default
