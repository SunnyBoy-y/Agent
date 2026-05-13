import os
import sys


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.interest_agent import InterestAgent
from src.agents.router_agent import RouterAgent
from src.orchestrator import SystemOrchestrator


def build_interest_agent() -> InterestAgent:
    agent = object.__new__(InterestAgent)
    agent.music_keywords = ["放一首", "听歌", "放歌", "放音乐", "来首歌", "邓丽君", "音乐", "歌曲", "歌单", "播放"]
    return agent


def test_router_music_request_routes_to_interest_agent():
    router = object.__new__(RouterAgent)
    assert router._route_by_rules("给我放一首邓丽君的歌听听") == "interest_agent"


def test_interest_agent_recognizes_music_request():
    agent = build_interest_agent()
    assert agent._is_music_request("想听点音乐，放首老歌吧")


def test_interest_agent_normalizes_music_query():
    agent = build_interest_agent()
    assert agent._normalize_music_query("给我放一首邓丽君的歌听听。") == "给我放一首邓丽君的歌听听"


def test_orchestrator_normalizes_music_payload():
    orchestrator = object.__new__(SystemOrchestrator)
    payload = orchestrator._normalize_music_payload(
        {"trigger_music": True, "query": "邓丽君", "source": "interest_agent"},
        fallback_query="默认歌曲",
        music_flag=True
    )

    assert payload == {
        "status": "success",
        "intent": "play_music",
        "trigger_music": True,
        "query": "邓丽君",
        "source": "interest_agent"
    }
