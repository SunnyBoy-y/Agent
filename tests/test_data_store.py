import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.schemas.mental_health import CarePlan, MentalRiskAssessment
from src.schemas.timed_events import MedicationPlan, ScheduleEntry, TimedEvent
from src.services.data_store import DataStore


def test_read_json_returns_deep_copied_default():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = DataStore(temp_dir)
        default = {"items": []}

        first = store.read_json("missing.json", default=default)
        first["items"].append("changed")
        second = store.read_json("missing.json", default=default)

        assert second == {"items": []}


def test_write_and_read_json_round_trip():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = DataStore(temp_dir)
        path = store.write_json("users/elder_001/profile.json", {"name": "张奶奶"})

        assert path.exists()
        assert store.read_json("users/elder_001/profile.json") == {"name": "张奶奶"}


def test_append_and_read_jsonl_preserves_order_and_limit():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = DataStore(temp_dir)

        store.append_jsonl("events/log.jsonl", {"idx": 1})
        store.append_jsonl("events/log.jsonl", {"idx": 2})
        store.append_jsonl("events/log.jsonl", {"idx": 3})

        assert store.read_jsonl("events/log.jsonl") == [{"idx": 1}, {"idx": 2}, {"idx": 3}]
        assert store.read_jsonl("events/log.jsonl", limit=2) == [{"idx": 2}, {"idx": 3}]


def test_user_helpers_scope_files_under_elder_id():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = DataStore(temp_dir)

        store.write_user_json("elder_001", "care_plan.json", {"version": 1})
        store.append_user_jsonl("elder_001", "mental_assessments.jsonl", {"risk_tier": "low"})

        assert store.read_user_json("elder_001", "care_plan.json") == {"version": 1}
        assert store.read_user_jsonl("elder_001", "mental_assessments.jsonl") == [{"risk_tier": "low"}]
        assert (Path(temp_dir) / "users" / "elder_001" / "care_plan.json").exists()


def test_path_traversal_is_rejected():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = DataStore(temp_dir)

        with pytest.raises(ValueError):
            store.write_json("../escape.json", {"bad": True})

        with pytest.raises(ValueError):
            store.write_user_json("elder/001", "profile.json", {})


def test_pydantic_models_are_written_as_json():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = DataStore(temp_dir)
        plan = CarePlan(
            elder_user_id="elder_001",
            version=2,
            risk_tier="medium",
            current_stage="anxiety.body_regulation",
            target_agent="mental_health_agent",
        )
        assessment = MentalRiskAssessment(
            id="assess_001",
            turn_id="turn_001",
            elder_user_id="elder_001",
            risk_tier="low",
            confidence=0.7,
        )

        store.write_user_json("elder_001", "care_plan.json", plan)
        store.append_user_jsonl("elder_001", "mental_assessments.jsonl", assessment)

        saved_plan = store.read_user_json("elder_001", "care_plan.json")
        saved_assessment = store.read_user_jsonl("elder_001", "mental_assessments.jsonl")[0]

        assert saved_plan["elder_user_id"] == "elder_001"
        assert saved_plan["version"] == 2
        assert saved_assessment["id"] == "assess_001"
        assert saved_assessment["risk_tier"] == "low"


def test_timed_event_and_medication_schema_dump():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = DataStore(temp_dir)
        med = MedicationPlan(
            medication_id="med_001",
            elder_user_id="elder_001",
            name="recorded medicine",
            dosage_text="once one tablet",
            instruction_text="after breakfast",
            schedule=[ScheduleEntry(time="08:00", label="breakfast")],
        )
        event = TimedEvent(
            event_id="event_001",
            elder_user_id="elder_001",
            event_type="medication_due",
            priority="high",
            scheduled_at=datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc),
            valid_until=datetime(2026, 5, 16, 11, 0, tzinfo=timezone.utc),
            payload={"medication_id": "med_001"},
        )

        store.write_user_json("elder_001", "medication_plans.json", [med])
        store.append_user_jsonl("elder_001", "timed_events.jsonl", event)

        plans = store.read_user_json("elder_001", "medication_plans.json")
        events = store.read_user_jsonl("elder_001", "timed_events.jsonl")

        assert plans[0]["schedule"][0]["time"] == "08:00"
        assert events[0]["event_type"] == "medication_due"
        assert events[0]["payload"]["medication_id"] == "med_001"


def test_concurrent_jsonl_appends_do_not_corrupt_file():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = DataStore(temp_dir)

        def append_item(idx):
            store.append_jsonl("concurrent/items.jsonl", {"idx": idx})

        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(append_item, range(20)))

        records = store.read_jsonl("concurrent/items.jsonl")
        assert sorted(item["idx"] for item in records) == list(range(20))

        raw_lines = (Path(temp_dir) / "concurrent" / "items.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(raw_lines) == 20
        for line in raw_lines:
            json.loads(line)


def test_reset_user_state_removes_only_target_user_directory():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = DataStore(temp_dir)

        store.write_user_json("elder_001", "profile.json", {"name": "target"})
        store.append_user_jsonl("elder_001", "planner_jobs.jsonl", {"job_id": "job_001"})
        store.write_user_json("elder_002", "profile.json", {"name": "other"})
        store.write_json("communities/community_001/announcements.json", [{"id": "ann_001"}])

        result = store.reset_user_state("elder_001")

        assert result["user_id"] == "elder_001"
        assert result["existed"] is True
        assert result["files_removed"] >= 2
        assert not (Path(temp_dir) / "users" / "elder_001").exists()
        assert store.read_user_json("elder_002", "profile.json") == {"name": "other"}
        assert store.read_json("communities/community_001/announcements.json") == [{"id": "ann_001"}]
