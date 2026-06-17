from datetime import datetime

from src.agents.companion_prompt import build_companion_system_prompt, current_time_prompt_block


def test_companion_prompt_injects_ephemeral_current_time():
    prompt = build_companion_system_prompt(
        phase="realtime_chat",
        now=datetime(2026, 6, 17, 16, 35),
    )

    assert "当前北京时间是 2026-06-17 周三 16:35" in prompt
    assert "只用于本轮推理" in prompt
    assert "不得写入、总结或沉淀" in prompt


def test_companion_prompt_guides_rhythm_memory_and_reminders():
    prompt = build_companion_system_prompt(phase="realtime_chat")

    assert "不要固定短答" in prompt or "张弛有度" in prompt
    assert "过去真实" in prompt or "上次您提到" in prompt
    assert "适当提醒" in prompt
    assert "不要每轮都机械" in prompt


def test_current_time_prompt_block_uses_supplied_timezone_aware_value():
    block = current_time_prompt_block(datetime(2026, 6, 17, 8, 5))

    assert "2026-06-17 周三 08:05" in block
    assert "不要把当前时间当作老人经历" in block
