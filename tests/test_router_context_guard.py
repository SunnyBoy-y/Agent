from datetime import datetime, timedelta, timezone

from src.agents.router_agent import RouterAgent
from src.services.context_guard import ContextGuard


def _router():
    return object.__new__(RouterAgent)


def test_anxiety_somatic_expression_routes_to_mental_health():
    router = _router()

    route = router._route_by_rules("\u6211\u4e00\u7d27\u5f20\u5c31\u5934\u75bc")

    assert route == "mental_health_agent"


def test_medication_time_query_routes_to_medication_path():
    router = _router()

    route = router._route_by_rules("\u5230\u4e86\u5403\u836f\u65f6\u95f4\u4e86\u5417")

    assert route == "medical_agent"


def test_physical_emergency_routes_to_medical_path():
    router = _router()

    route = router._route_by_rules("\u5934\u75bc\u5f97\u5389\u5bb3\uff0c\u8fd8\u5598\u4e0d\u4e0a\u6c14")

    assert route == "medical_agent"


def test_crisis_expression_routes_to_mental_health():
    router = _router()

    route = router._route_by_rules("\u6211\u4e0d\u60f3\u6d3b\u4e86")

    assert route == "mental_health_agent"


def test_context_guard_drops_old_system_and_proactive_noise():
    guard = ContextGuard()
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    history = [
        {
            "timestamp": (now - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S"),
            "role": "user",
            "content": "\u8fd9\u662f\u5f88\u65e7\u7684\u8bdd\u9898",
        },
        {
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "role": "system",
            "content": "\u7cfb\u7edf\u4e3b\u52a8\u5173\u6000",
        },
        {
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "role": "assistant",
            "content": "proactive_question: \u8981\u4e0d\u8981\u5531\u6b4c",
        },
        {
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "role": "user",
            "content": "\u6211\u73b0\u5728\u6709\u70b9\u7126\u8651",
        },
        {
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "role": "assistant",
            "content": "\u6211\u966a\u60a8\u6162\u6162\u8bf4",
        },
    ]

    clean = guard.sanitize_context(
        {"recent_history": history, "memory_context": "[\u7cfb\u7edf]\n\u8001\u4eba\u559c\u6b22\u542c\u620f"},
        now=now,
    )

    assert [item["content"] for item in clean["recent_history"]] == [
        "\u6211\u73b0\u5728\u6709\u70b9\u7126\u8651",
        "\u6211\u966a\u60a8\u6162\u6162\u8bf4",
    ]
    assert clean["elder_recent_utterances"] == ["\u6211\u73b0\u5728\u6709\u70b9\u7126\u8651"]
    assert "\u5f88\u65e7" not in clean["recent_history_text"]
    assert "proactive_question" not in clean["recent_history_text"]
    assert "[\u7cfb\u7edf]" not in clean["memory_context"]
    assert clean["context_guard"]["dropped_history_count"] == 3
