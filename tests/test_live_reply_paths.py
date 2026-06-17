import asyncio

from src.agents.daily_life_agent import DailyLifeAgent
from src.agents.interest_agent import InterestAgent


class StubRag:
    def get_user_profile(self):
        return {"name": "Alice"}

    def search_daily_events(self, _query, k=3):
        return []


def test_daily_life_chat_uses_live_generation_path():
    agent = DailyLifeAgent.__new__(DailyLifeAgent)
    agent.rag_helper = StubRag()

    async def fake_analyze_intent(_text):
        return {"intent": "chat"}

    async def fake_generate_chat_reply(input_text, profile, recent_history_text):
        return f"live chat reply for {input_text}"

    agent._analyze_intent = fake_analyze_intent
    agent._generate_chat_reply = fake_generate_chat_reply

    result = asyncio.run(
        agent.arun(
            "今天有点闷",
            {
                "user_profile": {"name": "Alice"},
                "recent_history_text": "elder: 前面聊过天气",
            },
        )
    )

    assert result["content"] == "live chat reply for 今天有点闷"


def test_daily_life_missing_event_uses_live_task_reply():
    agent = DailyLifeAgent.__new__(DailyLifeAgent)
    agent.rag_helper = StubRag()

    async def fake_analyze_intent(_text):
        return {"intent": "query_event"}

    async def fake_generate_task_reply(**kwargs):
        return f"live task reply for {kwargs['task_result']}"

    agent._analyze_intent = fake_analyze_intent
    agent._generate_task_reply = fake_generate_task_reply

    result = asyncio.run(
        agent.arun(
            "我昨天做了什么",
            {
                "user_profile": {"name": "Alice"},
                "recent_history_text": "",
            },
        )
    )

    assert result["content"] == "live task reply for 没有检索到相关生活记录。"


def test_interest_music_request_uses_live_generation_path():
    agent = InterestAgent.__new__(InterestAgent)
    agent.music_keywords = ["听歌"]

    def fake_trigger_music(query, elder_user_id=None):
        return {
            "status": "success",
            "trigger_music": True,
            "query": query,
            "intent": "play_music",
        }

    async def fake_generate_music_reply(**kwargs):
        return f"live music reply for {kwargs['normalized_query']}"

    agent._trigger_music = fake_trigger_music
    agent._generate_music_reply = fake_generate_music_reply

    result = asyncio.run(
        agent.arun(
            "我想听歌",
            {
                "user_id": "elder_001",
                "user_profile": {},
                "recent_history_text": "",
            },
        )
    )

    assert result["content"] == "live music reply for 我想听歌"
    assert result["action"] == "play_music"
    assert result["music_result"]["post_reply"]
