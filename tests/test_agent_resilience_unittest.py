import asyncio
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

from filelock import FileLock


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.interest_agent import InterestAgent
from src.agents.router_agent import RouterAgent
from src.orchestrator import SystemOrchestrator
from src.server import reset_memory, reset_profile
from src.utils.rag_helper import RAGHelper


class FakeVectorStore:
    def __init__(self, ids=None):
        self.ids = list(ids or [])
        self.deleted_ids = []

    def get(self):
        return {"ids": list(self.ids)}

    def delete(self, ids):
        self.deleted_ids.extend(ids)
        self.ids = []


def build_test_rag(temp_dir: str) -> RAGHelper:
    rag = object.__new__(RAGHelper)
    rag.memory_file = os.path.join(temp_dir, "chat_history.json")
    rag.profile_file = os.path.join(temp_dir, "user_profile.json")
    rag.emotion_file = os.path.join(temp_dir, "emotion_log.json")
    rag.agent_status_file = os.path.join(temp_dir, "agent_status.json")
    rag.memory_lock = FileLock(f"{rag.memory_file}.lock")
    rag.profile_lock = FileLock(f"{rag.profile_file}.lock")
    rag.emotion_lock = FileLock(f"{rag.emotion_file}.lock")
    rag.agent_status_lock = FileLock(f"{rag.agent_status_file}.lock")
    rag.vector_stores = {
        "knowledge": FakeVectorStore(["k1", "k2"]),
        "memory": FakeVectorStore(["m1"])
    }

    with open(rag.memory_file, "w", encoding="utf-8") as f:
        json.dump([{"role": "user", "content": "你好"}], f, ensure_ascii=False)
    with open(rag.profile_file, "w", encoding="utf-8") as f:
        json.dump({"name": "张奶奶"}, f, ensure_ascii=False)
    with open(rag.emotion_file, "w", encoding="utf-8") as f:
        json.dump([{"emotion": "sad"}], f, ensure_ascii=False)
    with open(rag.agent_status_file, "w", encoding="utf-8") as f:
        json.dump({"last_user_interaction": "2024-01-01 00:00:00"}, f, ensure_ascii=False)

    return rag


class AgentResilienceTests(unittest.TestCase):
    def test_router_music_request_routes_to_interest_agent(self):
        router = object.__new__(RouterAgent)
        self.assertEqual(router._route_by_rules("给我放一首邓丽君的歌听听"), "interest_agent")

    def test_interest_agent_recognizes_music_request(self):
        agent = object.__new__(InterestAgent)
        agent.music_keywords = ["放一首", "听歌", "放歌", "放音乐", "来首歌", "邓丽君", "音乐", "歌曲", "歌单", "播放"]
        self.assertTrue(agent._is_music_request("想听点音乐，放首老歌吧"))

    def test_orchestrator_normalizes_music_payload(self):
        orchestrator = object.__new__(SystemOrchestrator)
        payload = orchestrator._normalize_music_payload(
            {"trigger_music": True, "query": "邓丽君", "source": "interest_agent"},
            fallback_query="默认歌曲",
            music_flag=True
        )
        self.assertEqual(payload["intent"], "play_music")
        self.assertTrue(payload["trigger_music"])
        self.assertEqual(payload["query"], "邓丽君")
        self.assertEqual(payload["source"], "interest_agent")

    def test_update_user_profile_handles_missing_default_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rag = build_test_rag(temp_dir)
            rag.update_user_profile("health_condition", "头晕")
            with open(rag.profile_file, "r", encoding="utf-8") as f:
                profile = json.load(f)

            self.assertEqual(profile["name"], "张奶奶")
            self.assertIn("头晕", profile["health_condition"])
            self.assertEqual(profile["family_members"], [])
            self.assertEqual(profile["preferences"], [])

    def test_reset_profile_restores_default_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rag = build_test_rag(temp_dir)
            profile = rag.reset_profile()
            self.assertEqual(profile["name"], "未知")
            self.assertEqual(profile["health_condition"], [])
            with open(rag.profile_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["dialect"], "unknown")

    def test_reset_all_memory_clears_files_and_vector_store(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rag = build_test_rag(temp_dir)
            result = rag.reset_all_memory()

            with open(rag.memory_file, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f), [])
            with open(rag.emotion_file, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f), [])
            with open(rag.profile_file, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f)["name"], "未知")

            self.assertEqual(rag.vector_stores["knowledge"].deleted_ids, ["k1", "k2"])
            self.assertEqual(rag.vector_stores["memory"].deleted_ids, ["m1"])
            self.assertIn("profile", result)


class ServerEndpointTests(unittest.TestCase):
    def setUp(self):
        import src.server as server_module
        self.server_module = server_module
        self.original_orchestrator = server_module.orchestrator

    def tearDown(self):
        self.server_module.orchestrator = self.original_orchestrator

    def test_reset_profile_endpoint_uses_rag_helper_method(self):
        calls = {"profile": 0}

        class FakeRag:
            def reset_profile(self):
                calls["profile"] += 1
                return {"name": "未知"}

        self.server_module.orchestrator = SimpleNamespace(
            emotional_agent=SimpleNamespace(rag_helper=FakeRag())
        )

        result = asyncio.run(reset_profile())
        self.assertEqual(result["status"], "success")
        self.assertEqual(calls["profile"], 1)

    def test_reset_memory_endpoint_can_explicitly_use_legacy_rag_helper_method(self):
        calls = {"memory": 0}

        class FakeRag:
            def reset_all_memory(self):
                calls["memory"] += 1
                return {"profile": {}, "agent_status": {}}

        self.server_module.orchestrator = SimpleNamespace(
            emotional_agent=SimpleNamespace(rag_helper=FakeRag())
        )

        result = asyncio.run(reset_memory(user_id="user_001", include_legacy_rag=True))
        self.assertEqual(result["status"], "success")
        self.assertEqual(calls["memory"], 1)
        self.assertEqual(result["data"]["legacy_rag"]["scope"], "global")


if __name__ == "__main__":
    unittest.main()
