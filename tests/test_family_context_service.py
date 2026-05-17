import json

from src.agents.family_agent import FamilyAgent
from src.services.assessment_service import AssessmentService
from src.services.care_plan_service import CarePlanService
from src.services.data_store import DataStore
from src.services.family_context_service import FamilyContextService
from src.services.family_policy_service import FamilyPolicyService
from src.services.profile_service import ProfileService
from src.services.relay_message_service import RelayMessageService


CRISIS_TEXT = "\u6211\u4e0d\u60f3\u6d3b\u4e86"


def _services(tmp_path):
    store = DataStore(tmp_path)
    profile = ProfileService(store)
    relay = RelayMessageService(store)
    care_plan = CarePlanService(store)
    family_policy = FamilyPolicyService(store, relay)
    assessment = AssessmentService(store)
    context = FamilyContextService(
        store,
        care_plan_service=care_plan,
        family_policy_service=family_policy,
        relay_message_service=relay,
        profile_service=profile,
    )
    return store, profile, relay, care_plan, family_policy, assessment, context


def test_elder_summary_exposes_family_visible_evidence_but_not_community_payload(tmp_path):
    _, profile, relay, care_plan, _, assessment_service, context = _services(tmp_path)
    profile.update_profile("elder_001", {"name": "Mom"})
    assessment = assessment_service.assess_text(
        CRISIS_TEXT,
        {"user_id": "elder_001", "turn_id": "turn_001"},
    )
    care_plan.create_from_assessment(assessment)
    relay.create_from_assessment(assessment)

    summary = context.build_elder_summary("elder_001", "child_001")
    serialized = json.dumps(summary, ensure_ascii=False)

    assert summary["summary"]["profile_name"] == "Mom"
    assert summary["summary"]["risk_tier"] == "crisis"
    assert summary["visible_evidence"][0]["raw_quotes"] == ["\u4e0d\u60f3\u6d3b\u4e86"]
    assert summary["recent_family_alerts"][0]["raw_quotes"] == ["\u4e0d\u60f3\u6d3b\u4e86"]
    assert "community_crisis_summary" not in serialized
    assert "Community SOS" not in serialized


def test_family_chat_memory_is_isolated_from_elder_chat_history(tmp_path):
    store, *_rest, context = _services(tmp_path)

    context.add_family_turn(
        "elder_001",
        "child_001",
        "How is mom?",
        "She needs calm companionship.",
        metadata={"risk_tier": "medium"},
    )

    family_history = context.get_recent_family_history("elder_001", "child_001")
    elder_history = store.read_json("users/elder_001/chat_history.json", default=[])
    child_2_history = context.get_recent_family_history("elder_001", "child_002")
    memory = store.read_jsonl(
        "users/elder_001/family/child_001/family_chat_memory.jsonl",
    )

    assert [item["role"] for item in family_history] == ["child", "assistant"]
    assert family_history[0]["content"] == "How is mom?"
    assert elder_history == []
    assert child_2_history == []
    assert memory[-1]["child_user_id"] == "child_001"


def test_family_agent_response_uses_safe_caregiving_language(tmp_path):
    _, profile, relay, care_plan, family_policy, assessment_service, context = _services(tmp_path)
    profile.update_profile("elder_001", {"name": "Mom"})
    family_policy.update_policy_from_payload(
        "elder_001",
        "child_001",
        {"preferred_tone": "warm and slow"},
    )
    assessment = assessment_service.assess_text(
        "\u6211\u6700\u8fd1\u7126\u8651\uff0c\u5fc3\u614c\uff0c\u4e5f\u7761\u4e0d\u7740",
        {"user_id": "elder_001", "turn_id": "turn_002"},
    )
    care_plan.create_from_assessment(assessment)
    relay.create_from_assessment(assessment)
    agent = FamilyAgent(context)

    response = agent._build_response(
        "\u5979\u662f\u4e0d\u662f\u6291\u90c1\u75c7\uff0c\u8981\u4e0d\u8981\u53bb\u533b\u9662\u770b\u533b\u751f\u5403\u836f\uff1f",
        context.build_family_chat_context("elder_001", "child_001"),
    )
    sanitized = agent.safety_policy.sanitize_response(response)

    assert "\u6291\u90c1\u75c7" not in sanitized
    assert "\u53bb\u533b\u9662" not in sanitized
    assert "\u770b\u533b\u751f" not in sanitized
    assert "\u4e0d\u80fd\u505a\u8bca\u65ad\u547d\u540d" in sanitized
    assert "warm and slow" in sanitized
