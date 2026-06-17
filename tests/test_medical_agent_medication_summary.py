from types import SimpleNamespace

from src.agents.medical_agent import MedicalAgent


class StubMedicationService:
    def __init__(self, plans):
        self._plans = plans

    def list_plans(self, user_id, include_inactive=False):
        return list(self._plans)


def make_agent(plans=None):
    agent = MedicalAgent.__new__(MedicalAgent)
    agent.medication_reminder_service = StubMedicationService(plans or [])
    return agent


def test_recorded_medication_summary_prefers_structured_plans():
    agent = make_agent([
        SimpleNamespace(
            name="阿司匹林",
            dosage_text="每次1片",
            instruction_text="饭后服用",
            schedule=[{"label": "早上", "time": "08:00"}],
        ),
        {
            "name": "维生素D",
            "dosage_text": "每日1粒",
            "instruction_text": "",
            "schedule": [{"time": "20:00"}],
        },
    ])

    summary = agent._recorded_medication_summary(
        {"user_id": "user_001"},
        {"medications": [{"name": "旧药", "time": "09:00"}]},
    )

    assert summary == "阿司匹林；每次1片；饭后服用；时间：早上 08:00；维生素D；每日1粒；时间：20:00"


def test_recorded_medication_summary_falls_back_to_legacy_profile():
    agent = make_agent([])

    summary = agent._recorded_medication_summary(
        {"user_id": "user_001"},
        {
            "medications": [
                {"name": "降压药", "time": "07:30"},
                {"name": "钙片"},
                "鱼油",
            ]
        },
    )

    assert summary == "降压药（07:30）；钙片；鱼油"


def test_recorded_medication_summary_returns_empty_when_nothing_is_recorded():
    agent = make_agent([])

    assert agent._recorded_medication_summary({"user_id": "user_001"}, {}) == ""
