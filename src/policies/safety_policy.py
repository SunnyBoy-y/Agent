from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SafetyResult:
    sanitized_text: str
    blocked: bool = False
    violations: List[str] = field(default_factory=list)


class SafetyPolicy:
    """Centralized red-line policy for agent text before it reaches clients."""

    DIAGNOSIS_TERMS = [
        "\u6291\u90c1\u75c7",
        "\u7126\u8651\u75c7",
        "\u53cc\u76f8\u60c5\u611f\u969c\u788d",
        "\u53cc\u5411\u60c5\u611f\u969c\u788d",
        "\u8e81\u90c1\u75c7",
    ]
    MEDICAL_ADVICE_TERMS = [
        "\u53bb\u533b\u9662",
        "\u770b\u533b\u751f",
        "\u5e26\u60a8\u53bb\u533b\u9662\u770b\u770b",
        "\u5efa\u8bae\u5403",
        "\u53ef\u4ee5\u5403",
        "\u52a0\u91cf",
        "\u51cf\u91cf",
        "\u505c\u836f",
        "\u8865\u670d",
    ]
    SELF_HARM_INSTRUCTION_TERMS = [
        "\u600e\u4e48\u81ea\u6740",
        "\u600e\u4e48\u6b7b",
        "\u81ea\u6740\u65b9\u6cd5",
        "\u8f7b\u751f\u65b9\u6cd5",
    ]
    MEDICATION_ALLOWED_HINTS = [
        "\u6309\u533b\u5631",
        "\u5df2\u8bb0\u5f55",
        "\u7167\u62a4\u8005\u5f55\u5165",
        "\u6309\u8bb0\u5f55",
    ]

    def sanitize_response(self, text: str, risk_tier: Optional[str] = None) -> str:
        return self.check_response(text, risk_tier=risk_tier).sanitized_text

    def check_response(self, text: str, risk_tier: Optional[str] = None) -> SafetyResult:
        original = text or ""
        sanitized = original
        violations: List[str] = []

        if risk_tier == "crisis":
            sanitized = self._safe_crisis_text(sanitized)

        for term in self.DIAGNOSIS_TERMS:
            if term in sanitized:
                sanitized = sanitized.replace(term, "\u60c5\u7eea\u56f0\u6270")
                violations.append("diagnosis_label")

        if not self._is_allowed_medication_reminder(sanitized):
            for term in self.MEDICAL_ADVICE_TERMS:
                if term in sanitized:
                    sanitized = sanitized.replace(term, self._medical_boundary_text())
                    violations.append("medical_advice")

        for term in self.SELF_HARM_INSTRUCTION_TERMS:
            if term in sanitized:
                sanitized = sanitized.replace(term, self._crisis_boundary_text())
                violations.append("self_harm_instruction")

        return SafetyResult(
            sanitized_text=self._dedupe_spaces(sanitized),
            blocked=bool(violations),
            violations=sorted(set(violations)),
        )

    def _is_allowed_medication_reminder(self, text: str) -> bool:
        return any(hint in text for hint in self.MEDICATION_ALLOWED_HINTS)

    def _safe_crisis_text(self, text: str) -> str:
        safe_prefix = "\u6211\u5728\u8fd9\u91cc\u966a\u7740\u60a8\uff0c\u6211\u4eec\u5148\u628a\u5f53\u4e0b\u7a33\u4f4f\u3002"
        if safe_prefix in text:
            return text
        return safe_prefix + text

    def _medical_boundary_text(self) -> str:
        return "\u6211\u4e0d\u505a\u533b\u7597\u5904\u7f6e\u6216\u7528\u836f\u8c03\u6574\u5efa\u8bae"

    def _crisis_boundary_text(self) -> str:
        return "\u8fd9\u7c7b\u7ec6\u8282\u6211\u4e0d\u4f1a\u63d0\u4f9b\uff0c\u6211\u4f1a\u5148\u966a\u60a8\u56de\u5230\u5b89\u5168\u611f"

    def _dedupe_spaces(self, text: str) -> str:
        return " ".join(text.split()) if "  " in text else text
