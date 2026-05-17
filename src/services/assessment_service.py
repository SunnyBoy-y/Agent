import uuid
from typing import Any, Dict, List, Optional, Tuple

from src.schemas.mental_health import (
    DetectedState,
    MentalRiskAssessment,
    SafetyFlags,
    VisibilityPolicy,
)
from src.services.data_store import DataStore


class AssessmentService:
    """Fast deterministic mental-health risk assessment for the realtime path."""

    CRISIS_PHRASES = [
        "\u6d3b\u7740\u6ca1\u610f\u601d",
        "\u4e0d\u60f3\u6d3b\u4e86",
        "\u6b7b\u4e86\u7b97\u4e86",
        "\u6211\u60f3\u53bb\u6b7b",
        "\u4e0d\u60f3\u518d\u6491",
    ]
    SIGNALS: Dict[str, List[Tuple[str, int]]] = {
        "anxiety": [
            ("\u7126\u8651", 2),
            ("\u5fc3\u614c", 2),
            ("\u53d1\u614c", 2),
            ("\u5750\u7acb\u4e0d\u5b89", 2),
            ("\u62c5\u5fc3", 2),
            ("\u5bb3\u6015", 2),
            ("\u7761\u4e0d\u7740", 2),
        ],
        "depressive_low_mood": [
            ("\u6ca1\u529b\u6c14", 3),
            ("\u4e0d\u60f3\u52a8", 3),
            ("\u6ca1\u610f\u601d", 3),
            ("\u7a7a\u843d\u843d", 2),
            ("\u7d2f\u8d58", 4),
            ("\u6ca1\u4eba\u9700\u8981\u6211", 4),
        ],
        "manic_activation": [
            ("\u4e00\u591c\u6ca1\u7761\u4e5f\u4e0d\u56f0", 4),
            ("\u505c\u4e0d\u4e0b\u6765", 3),
            ("\u597d\u591a\u8ba1\u5212", 3),
            ("\u82b1\u94b1", 3),
        ],
        "physical_emergency": [
            ("\u80f8\u53e3\u75bc", 6),
            ("\u80f8\u95f7", 5),
            ("\u547c\u5438\u56f0\u96be", 6),
            ("\u5598\u4e0d\u4e0a\u6c14", 6),
            ("\u6454\u5012", 5),
        ],
    }
    PROTECTIVE_PHRASES = [
        "\u613f\u610f\u804a",
        "\u6211\u60f3\u542c\u4f60\u8bf4",
        "\u53ef\u4ee5\u8054\u7cfb\u5bb6\u4eba",
    ]

    def __init__(self, store: Optional[DataStore] = None):
        self.store = store or DataStore()

    def assess_text(self, text: str, context: Optional[Dict[str, Any]] = None) -> MentalRiskAssessment:
        context = context or {}
        user_id = str(context.get("user_id") or context.get("elder_user_id") or "user_001")
        turn_id = str(context.get("turn_id") or f"turn_{uuid.uuid4().hex}")
        normalized = text or ""

        crisis_hits = [phrase for phrase in self.CRISIS_PHRASES if phrase in normalized]
        if crisis_hits:
            assessment = self._build_crisis_assessment(user_id, turn_id, crisis_hits)
            self.save_assessment(assessment)
            return assessment

        state_scores: Dict[str, int] = {}
        evidence: List[Dict[str, Any]] = []
        raw_quotes: List[str] = []
        flags = SafetyFlags()

        for state, patterns in self.SIGNALS.items():
            for phrase, weight in patterns:
                if phrase not in normalized:
                    continue
                state_scores[state] = state_scores.get(state, 0) + weight
                evidence.append({
                    "type": "text_signal",
                    "content": phrase,
                    "weight": weight,
                    "source": "current_turn",
                    "state": state,
                })
                raw_quotes.append(phrase)
                if state == "manic_activation":
                    flags.manic_activation = True
                if state == "physical_emergency":
                    flags.medical_emergency = True

        score = sum(state_scores.values())
        score += self._trend_score(context.get("recent_assessments") or context.get("emotion_trend"))
        score -= self._protective_adjustment(normalized)
        score = max(score, 0)

        primary_state = self._primary_state(state_scores)
        risk_tier = self._tier_from_score(score)
        confidence = min(0.95, 0.45 + score * 0.06) if evidence else 0.2

        detected_states = [
            DetectedState(
                state=state,
                severity=min(10, value),
                confidence=min(0.95, 0.45 + value * 0.07),
                evidence=[
                    item["content"]
                    for item in evidence
                    if item.get("state") == state
                ],
            )
            for state, value in sorted(state_scores.items(), key=lambda item: item[1], reverse=True)
        ]

        assessment = MentalRiskAssessment(
            id=f"assess_{uuid.uuid4().hex}",
            turn_id=turn_id,
            elder_user_id=user_id,
            primary_state=primary_state,
            detected_states=detected_states,
            risk_tier=risk_tier,
            confidence=confidence,
            score=score,
            evidence_summary=self._evidence_summary(primary_state, risk_tier),
            evidence=evidence,
            raw_quotes=raw_quotes,
            safety_flags=flags,
            next_response_mode=self._next_response_mode(risk_tier, primary_state),
            next_goal=self._next_goal(risk_tier, primary_state),
            elder_wording=self._elder_wording(risk_tier, primary_state),
            family_summary=self._family_summary(risk_tier, primary_state, raw_quotes),
            family_suggestion=self._family_suggestion(risk_tier, primary_state),
            community_reason_summary=self._community_summary(risk_tier, primary_state),
            community_suggested_actions=self._community_actions(risk_tier),
            visibility=self._visibility(risk_tier),
        )
        self.save_assessment(assessment)
        return assessment

    def save_assessment(self, assessment: MentalRiskAssessment) -> None:
        self.store.append_user_jsonl(
            assessment.elder_user_id,
            "mental_assessments.jsonl",
            assessment,
        )

    def _build_crisis_assessment(self, user_id: str, turn_id: str, hits: List[str]) -> MentalRiskAssessment:
        evidence = [
            {
                "type": "text_quote",
                "content": hit,
                "weight": 100,
                "source": "current_turn",
                "state": "suicidal_ideation",
            }
            for hit in hits
        ]
        return MentalRiskAssessment(
            id=f"assess_{uuid.uuid4().hex}",
            turn_id=turn_id,
            elder_user_id=user_id,
            primary_state="suicidal_ideation",
            detected_states=[
                DetectedState(
                    state="suicidal_ideation",
                    severity=10,
                    confidence=0.95,
                    evidence=hits,
                )
            ],
            risk_tier="crisis",
            confidence=0.95,
            score=100,
            evidence_summary="explicit crisis expression detected",
            evidence=evidence,
            raw_quotes=hits,
            safety_flags=SafetyFlags(
                self_harm_ideation=True,
                explicit_death_wish=True,
            ),
            next_response_mode="crisis_safety_grounding",
            next_goal="stabilize immediate safety and relay support",
            elder_wording="I am here with you. Let us first steady this moment together.",
            family_summary=f"Detected crisis-level expression. Original quote visible to family only: {hits[0]}",
            family_suggestion="Prioritize calm companionship and confirm current safety arrangement.",
            community_reason_summary="Crisis-level expression detected; original quote is hidden from community view.",
            community_suggested_actions=[
                "Check current safety and companionship arrangement.",
                "Coordinate with family contact if available.",
            ],
            visibility=VisibilityPolicy(elder="supportive", family="quote_summary", community="crisis_summary"),
        )

    def _primary_state(self, state_scores: Dict[str, int]) -> str:
        if not state_scores:
            return "stable_or_general"
        return max(state_scores.items(), key=lambda item: item[1])[0]

    def _tier_from_score(self, score: int) -> str:
        if score >= 8:
            return "high"
        if score >= 5:
            return "medium"
        if score >= 2:
            return "low"
        return "safe"

    def _trend_score(self, trend: Any) -> int:
        if isinstance(trend, list):
            tiers = [str(item.get("risk_tier") or item.get("risk_level") or "") for item in trend if isinstance(item, dict)]
            return (2 if tiers.count("medium") >= 2 else 0) + (3 if "high" in tiers else 0)
        trend_text = str(trend or "")
        if "high" in trend_text or "crisis" in trend_text:
            return 3
        if trend_text.count("medium") >= 2:
            return 2
        return 0

    def _protective_adjustment(self, text: str) -> int:
        return min(2, sum(1 for phrase in self.PROTECTIVE_PHRASES if phrase in text))

    def _evidence_summary(self, primary_state: str, risk_tier: str) -> str:
        if risk_tier == "safe":
            return "no strong mental-health risk signal detected"
        return f"{primary_state} signals mapped to {risk_tier}"

    def _next_response_mode(self, risk_tier: str, primary_state: str) -> str:
        if risk_tier == "high":
            return "high_support_grounding"
        if primary_state == "anxiety":
            return "anxiety_emotional_first_aid"
        if primary_state == "depressive_low_mood":
            return "low_energy_companionship"
        if primary_state == "manic_activation":
            return "low_stimulation_grounding"
        return "companionship"

    def _next_goal(self, risk_tier: str, primary_state: str) -> str:
        if risk_tier == "high":
            return "reduce arousal and keep user connected"
        if primary_state == "anxiety":
            return "accept emotion then guide breathing or grounding"
        if primary_state == "depressive_low_mood":
            return "low-energy companionship then tiny action"
        if primary_state == "manic_activation":
            return "lower stimulation and slow pacing"
        return "continue warm companionship"

    def _elder_wording(self, risk_tier: str, primary_state: str) -> str:
        if risk_tier in ("medium", "high"):
            return "I hear this is weighing on you. I will stay with you and keep the pace slow."
        if primary_state == "anxiety":
            return "I hear there is a lot of worry right now. Let us slow down together."
        return "I am here with you."

    def _family_summary(self, risk_tier: str, primary_state: str, quotes: List[str]) -> Optional[str]:
        if risk_tier in ("safe", "low"):
            return None
        quote_part = f" Original signal: {quotes[0]}" if quotes else ""
        return f"{risk_tier} risk tendency: {primary_state}.{quote_part}"

    def _family_suggestion(self, risk_tier: str, primary_state: str) -> Optional[str]:
        if risk_tier in ("safe", "low"):
            return None
        return "Use calm companionship, avoid debate or blame, and confirm current support."

    def _community_summary(self, risk_tier: str, primary_state: str) -> Optional[str]:
        if risk_tier != "crisis":
            return None
        return f"Crisis alert summary: {primary_state} signal detected."

    def _community_actions(self, risk_tier: str) -> List[str]:
        if risk_tier != "crisis":
            return []
        return [
            "Check whether the elder is currently accompanied.",
            "Coordinate with family contact according to local workflow.",
        ]

    def _visibility(self, risk_tier: str) -> VisibilityPolicy:
        if risk_tier == "crisis":
            return VisibilityPolicy(elder="supportive", family="quote_summary", community="crisis_summary")
        if risk_tier in ("medium", "high"):
            return VisibilityPolicy(elder="supportive", family="summary", community="none")
        return VisibilityPolicy(elder="supportive", family="none", community="none")
