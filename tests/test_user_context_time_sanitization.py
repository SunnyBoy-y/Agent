import tempfile

from src.services.data_store import DataStore
from src.services.user_context_service import UserContextService


def test_current_time_answer_does_not_persist_specific_clock_time():
    with tempfile.TemporaryDirectory() as temp_dir:
        context = UserContextService(DataStore(temp_dir))

        context.add_memory(
            "elder_a",
            "现在几点了？",
            "现在是 2026-06-17 周三 16:35，您可以慢慢准备晚饭了。",
        )

        history = context.get_recent_history("elder_a")
        assert history[-1]["role"] == "assistant"
        assert "16:35" not in history[-1]["content"]
        assert "2026-06-17" not in history[-1]["content"]
        assert "当时" in history[-1]["content"]


def test_layered_chat_context_uses_five_turn_window_and_summary():
    with tempfile.TemporaryDirectory() as temp_dir:
        context = UserContextService(DataStore(temp_dir))

        for index in range(7):
            context.add_memory("elder_a", f"第{index}轮老人说的话", f"第{index}轮小暖回应")

        layered = context.get_layered_chat_context("elder_a", recent_turns=5)

        assert layered["recent_turns"] == 5
        assert len(layered["recent_window"]) == 10
        assert "第0轮老人说的话" in layered["summary"]
        assert "第1轮小暖回应" in layered["summary"]
        assert "第0轮老人说的话" not in layered["recent_window_text"]
        assert "第2轮老人说的话" in layered["recent_window_text"]
        assert "第6轮小暖回应" in layered["recent_window_text"]
