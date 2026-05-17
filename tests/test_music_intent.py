import os
import sys


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.interest_agent import InterestAgent
from src.agents.router_agent import RouterAgent
from src.orchestrator import SystemOrchestrator


def build_interest_agent() -> InterestAgent:
    agent = object.__new__(InterestAgent)
    agent.music_keywords = [
        "听歌",
        "音乐",
        "歌曲",
        "唱歌",
        "放首",
        "点歌",
        "听听",
        "一首歌",
        "来首",
        "播放",
    ]
    return agent


def test_router_music_request_routes_to_interest_agent():
    router = object.__new__(RouterAgent)
    assert router._route_by_rules("我想听首歌，给我放点音乐") == "interest_agent"


def test_interest_agent_recognizes_music_request():
    agent = build_interest_agent()
    assert agent._is_music_request("今天想听歌，你给我唱首歌吧")


def test_interest_agent_normalizes_music_query():
    agent = build_interest_agent()
    assert agent._normalize_music_query("我想听首歌，给我放点音乐吧。") == "我想听首歌，给我放点音乐吧"


def test_orchestrator_normalizes_music_payload():
    orchestrator = object.__new__(SystemOrchestrator)
    payload = orchestrator._normalize_music_payload(
        {"trigger_music": True, "query": "月亮代表我的心", "source": "interest_agent"},
        fallback_query="随便来首歌",
        music_flag=True,
    )

    assert payload == {
        "status": "success",
        "intent": "play_music",
        "trigger_music": True,
        "query": "月亮代表我的心",
        "source": "interest_agent",
        "music_name": "月亮代表我的心",
        "post_reply": "这首歌先到这里。您现在心里有没有松一点？",
    }
