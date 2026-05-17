import tempfile

from src.services.assessment_service import AssessmentService
from src.services.data_store import DataStore


def test_crisis_phrase_directly_maps_to_crisis_and_is_saved():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = AssessmentService(DataStore(temp_dir))
        assessment = service.assess_text(
            "\u6211\u89c9\u5f97\u6d3b\u7740\u6ca1\u610f\u601d",
            {"user_id": "elder_001", "turn_id": "turn_001"},
        )

        assert assessment.risk_tier == "crisis"
        assert assessment.primary_state == "suicidal_ideation"
        assert assessment.safety_flags.explicit_death_wish is True
        saved = service.store.read_user_jsonl("elder_001", "mental_assessments.jsonl")
        assert saved[0]["risk_tier"] == "crisis"


def test_anxiety_headache_prioritizes_anxiety_not_medication():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = AssessmentService(DataStore(temp_dir))
        assessment = service.assess_text(
            "\u6211\u7126\u8651\u5f97\u5934\u75bc\uff0c\u5fc3\u614c\uff0c\u7761\u4e0d\u7740",
            {"user_id": "elder_001"},
        )

        assert assessment.primary_state == "anxiety"
        assert assessment.risk_tier in ("medium", "high")
        assert "breathing" in assessment.next_goal or "grounding" in assessment.next_goal


def test_crisis_is_not_downgraded_by_protective_factor():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = AssessmentService(DataStore(temp_dir))
        assessment = service.assess_text(
            "\u6211\u4e0d\u60f3\u6d3b\u4e86\uff0c\u4f46\u6211\u613f\u610f\u804a",
            {"user_id": "elder_001"},
        )

        assert assessment.risk_tier == "crisis"
        assert assessment.score == 100


def test_community_visibility_only_for_crisis():
    with tempfile.TemporaryDirectory() as temp_dir:
        service = AssessmentService(DataStore(temp_dir))
        low = service.assess_text("\u6211\u6709\u70b9\u62c5\u5fc3", {"user_id": "elder_001"})
        crisis = service.assess_text("\u6b7b\u4e86\u7b97\u4e86", {"user_id": "elder_001"})

        assert low.visibility.community == "none"
        assert crisis.visibility.community == "crisis_summary"
        assert crisis.community_reason_summary is not None
        assert crisis.raw_quotes[0] not in crisis.community_reason_summary
