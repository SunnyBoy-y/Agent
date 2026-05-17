from src.policies.safety_policy import SafetyPolicy


def test_safety_policy_removes_diagnosis_labels():
    policy = SafetyPolicy()
    result = policy.check_response("\u60a8\u662f\u6291\u90c1\u75c7\uff0c\u9700\u8981\u6ce8\u610f\u3002")

    assert result.blocked is True
    assert "diagnosis_label" in result.violations
    assert "\u6291\u90c1\u75c7" not in result.sanitized_text


def test_safety_policy_blocks_hospital_and_medication_advice():
    policy = SafetyPolicy()
    text = "\u6211\u5e26\u60a8\u53bb\u533b\u9662\u770b\u770b\uff0c\u53ef\u4ee5\u5403\u70b9\u836f\u3002"
    result = policy.check_response(text)

    assert result.blocked is True
    assert "medical_advice" in result.violations
    assert "\u53bb\u533b\u9662" not in result.sanitized_text
    assert "\u53ef\u4ee5\u5403" not in result.sanitized_text


def test_safety_policy_allows_recorded_medication_reminder():
    policy = SafetyPolicy()
    text = "\u6309\u5df2\u8bb0\u5f55\u533b\u5631\uff0c\u73b0\u5728\u662f\u63d0\u9192\u60a8\u7528\u836f\u7684\u65f6\u95f4\u3002"
    result = policy.check_response(text)

    assert result.blocked is False
    assert result.sanitized_text == text


def test_safety_policy_crisis_response_adds_grounding_prefix():
    policy = SafetyPolicy()
    result = policy.check_response("\u6211\u4eec\u6162\u6162\u6765\u3002", risk_tier="crisis")

    assert result.sanitized_text.startswith("\u6211\u5728\u8fd9\u91cc\u966a\u7740\u60a8")
