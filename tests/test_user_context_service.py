import tempfile

from src.services.data_store import DataStore
from src.services.profile_service import ProfileService
from src.services.user_context_service import UserContextService
from src.tools.professional_skills import ProfessionalSkills


def test_profile_service_scopes_profiles_by_user():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = ProfileService(DataStore(temp_dir))

        service.update_profile("elder_a", {"name": "A", "preferences": "music"})
        service.update_profile("elder_b", {"name": "B", "preferences": "chess"})

        assert service.get_profile("elder_a")["name"] == "A"
        assert service.get_profile("elder_a")["preferences"] == ["music"]
        assert service.get_profile("elder_b")["name"] == "B"
        assert service.get_profile("elder_b")["preferences"] == ["chess"]


def test_profile_update_ignores_body_user_id_and_dedupes_lists():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = ProfileService(DataStore(temp_dir))

        profile = service.update_profile(
            "elder_a",
            {"user_id": "elder_b", "health_condition": ["anxiety", "anxiety"]},
        )
        profile = service.update_profile("elder_a", {"health_condition": "insomnia"})

        assert profile["health_condition"] == ["anxiety", "insomnia"]
        assert service.get_profile("elder_b")["name"] == "unknown"


def test_user_context_history_and_status_are_per_user():
    with tempfile.TemporaryDirectory() as temp_dir:
        context = UserContextService(DataStore(temp_dir))

        context.add_memory("elder_a", "hi", "hello")
        context.add_memory("elder_b", "question", "answer")
        context.update_agent_status("elder_a", agent_type="mental_health")

        assert [item["content"] for item in context.get_recent_history("elder_a")] == ["hi", "hello"]
        assert [item["content"] for item in context.get_recent_history("elder_b")] == ["question", "answer"]
        assert context.get_agent_status("elder_a")["agent_last_update"]["mental_health"] != "2000-01-01 00:00:00"
        assert context.get_agent_status("elder_b")["agent_last_update"]["mental_health"] == "2000-01-01 00:00:00"


def test_user_context_emotion_log_and_snapshot():
    with tempfile.TemporaryDirectory() as temp_dir:
        context = UserContextService(DataStore(temp_dir))
        context.update_profile("elder_a", {"name": "A", "preferences": ["music"]})
        context.log_emotion("elder_a", "sad", "medium")
        context.log_emotion("elder_a", "sad", "medium")
        profile = context.get_profile("elder_a")

        snapshot = context.build_context_snapshot(
            "elder_a",
            {
                "user_profile": profile,
                "recent_history_text": "recent text",
                "memory_context": "memory text",
            },
        )

        assert "unstable mood signal" in context.get_emotion_trend("elder_a")
        assert snapshot["user_id"] == "elder_a"
        assert snapshot["profile_name"] == "A"
        assert snapshot["preferences"] == ["music"]


def test_health_complaint_tool_helper_uses_user_context_service():
    with tempfile.TemporaryDirectory() as temp_dir:
        context = UserContextService(DataStore(temp_dir))

        result = ProfessionalSkills.record_health_complaint_to_service(
            "leg pain",
            elder_user_id="elder_health",
            user_context_service=context,
        )

        profile = context.get_profile("elder_health")
        assert result["status"] == "success"
        assert result["elder_user_id"] == "elder_health"
        assert result["profile_health_condition"] == ["leg pain"]
        assert profile["health_condition"] == ["leg pain"]
