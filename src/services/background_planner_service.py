import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from src.agents.planning_agent import PlanningAgent
from src.schemas.mental_health import CarePlan, MentalRiskAssessment
from src.schemas.planner import PlannerJob, PlannerResult, PlannerStatus
from src.services.action_session_service import ActionSessionService
from src.services.care_plan_service import CarePlanService
from src.services.data_store import DataStore
from src.services.relay_message_service import RelayMessageService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BackgroundPlannerService:
    """Background planner with per-user task isolation and stale protection."""

    JOB_AUDIT_FILE = "planner_jobs.jsonl"
    ACTION_AUDIT_FILE = "planner_actions.jsonl"
    STATUS_FILE = "planner_status.json"

    def __init__(
        self,
        store: Optional[DataStore] = None,
        care_plan_service: Optional[CarePlanService] = None,
        *,
        planning_agent: Optional[PlanningAgent] = None,
        relay_message_service: Optional[RelayMessageService] = None,
        action_session_service: Optional[ActionSessionService] = None,
        safe_low_debounce_seconds: float = 0.3,
        on_job_event: Optional[Callable[[PlannerJob, str], None]] = None,
    ):
        self.store = store or DataStore()
        self.care_plan_service = care_plan_service or CarePlanService(self.store)
        self.planning_agent = planning_agent or PlanningAgent(self.care_plan_service)
        self.relay_message_service = relay_message_service or RelayMessageService(self.store)
        self.action_session_service = action_session_service or ActionSessionService(self.store)
        self.safe_low_debounce_seconds = safe_low_debounce_seconds
        self.on_job_event = on_job_event

        self.planner_tasks: Dict[str, asyncio.Task] = {}
        self.planner_latest_turn: Dict[str, str] = {}
        self.planner_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.active_jobs: Dict[str, PlannerJob] = {}
        self.job_contexts: Dict[str, Dict[str, Any]] = {}
        self.all_tasks = set()
        self.task_owners: Dict[asyncio.Task, str] = {}
        self.cancel_reasons: Dict[str, str] = {}

    def get_status(self, elder_user_id: str) -> PlannerStatus:
        raw = self.store.read_user_json(elder_user_id, self.STATUS_FILE, default=None)
        if not isinstance(raw, dict):
            return PlannerStatus(elder_user_id=elder_user_id)
        if hasattr(PlannerStatus, "model_validate"):
            return PlannerStatus.model_validate(raw)
        return PlannerStatus.parse_obj(raw)

    def list_jobs(self, elder_user_id: str):
        rows = self.store.read_user_jsonl(elder_user_id, self.JOB_AUDIT_FILE)
        jobs = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if hasattr(PlannerJob, "model_validate"):
                jobs.append(PlannerJob.model_validate(row))
            else:
                jobs.append(PlannerJob.parse_obj(row))
        return jobs

    def schedule_from_assessment(
        self,
        assessment: MentalRiskAssessment,
        context: Optional[Dict[str, Any]] = None,
    ) -> PlannerJob:
        user_id = assessment.elder_user_id
        old_task = self.planner_tasks.get(user_id)
        old_job = self.active_jobs.get(user_id)
        if old_job is not None and old_job.status in {"queued", "running"}:
            self._transition_job(old_job, "cancel_requested")
            if assessment.risk_tier in {"high", "crisis"} and old_task and not old_task.done():
                self.cancel_reasons[old_job.job_id] = "cancelled_by_newer_priority_turn"
                old_task.cancel()

        self.planner_latest_turn[user_id] = assessment.turn_id
        job = PlannerJob(
            job_id=f"planner_{uuid.uuid4().hex}",
            elder_user_id=user_id,
            assessment_id=assessment.id,
            base_turn_id=assessment.turn_id,
            base_care_plan_version=self.care_plan_service.current_version(user_id),
            priority=assessment.risk_tier,
        )
        self.active_jobs[user_id] = job
        self.job_contexts[job.job_id] = self._snapshot_context(context)
        self._append_job(job)
        self._write_status(
            user_id,
            status="queued",
            latest_turn_id=assessment.turn_id,
            running_job_id=job.job_id,
        )
        self._notify(job, "queued")

        task = asyncio.create_task(self._run_job(job, assessment))
        self.planner_tasks[user_id] = task
        self.all_tasks.add(task)
        self.task_owners[task] = user_id
        task.add_done_callback(lambda done_task, uid=user_id, jid=job.job_id: self._clear_task(uid, jid, done_task))
        return job

    async def wait_for_idle(self, elder_user_id: str) -> None:
        tasks = [
            task
            for task in self.all_tasks
            if not task.done()
            and self.task_owners.get(task) == elder_user_id
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel_user_jobs(self, elder_user_id: str, reason: str = "cancelled_by_user_state_reset") -> Dict[str, Any]:
        """Cancel queued/running planner work for one user before state reset."""

        tasks = [
            task
            for task in self.all_tasks
            if not task.done()
            and self.task_owners.get(task) == elder_user_id
        ]
        active_job = self.active_jobs.get(elder_user_id)
        if active_job is not None and active_job.status in {"queued", "running"}:
            self._transition_job(active_job, "cancel_requested")
            self.cancel_reasons[active_job.job_id] = reason

        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self.planner_latest_turn.pop(elder_user_id, None)
        return {
            "elder_user_id": elder_user_id,
            "cancelled_tasks": len(tasks),
            "active_job_id": active_job.job_id if active_job is not None else None,
            "reason": reason,
        }

    async def shutdown(self) -> None:
        tasks = [task for task in self.all_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_job(self, job: PlannerJob, assessment: MentalRiskAssessment) -> None:
        try:
            async with self.planner_locks[job.elder_user_id]:
                if job.priority in {"safe", "low"} and self.safe_low_debounce_seconds > 0:
                    await asyncio.sleep(self.safe_low_debounce_seconds)

                if self._is_superseded(job):
                    self._mark_stale(job, "newer_turn_arrived_before_start")
                    return

                self._transition_job(job, "running", started_at=utc_now())
                self._write_status(
                    job.elder_user_id,
                    status="running",
                    latest_turn_id=self.planner_latest_turn.get(job.elder_user_id),
                    running_job_id=job.job_id,
                )

                current_plan = self.care_plan_service.get_plan(job.elder_user_id)
                planner_context = self.job_contexts.get(job.job_id, {})
                planner_result = await self._run_rule_planner(job, assessment, current_plan, planner_context)
                job.review_status = planner_result.review.status
                job.used_fallback = planner_result.used_fallback

                if self._is_superseded(job):
                    self._mark_stale(job, "newer_turn_arrived_before_commit")
                    return

                committed = self.care_plan_service.compare_and_swap(
                    elder_user_id=job.elder_user_id,
                    expected_version=job.base_care_plan_version,
                    patch=planner_result.care_plan_patch,
                    source_turn_id=job.base_turn_id,
                    updated_by="planner",
                )
                if not committed:
                    self._mark_stale(job, "care_plan_version_changed")
                    return

                self._persist_review_snapshot(assessment, planner_result)
                self._persist_actions(job, assessment, planner_result)
                self._transition_job(job, "completed", finished_at=utc_now())
                self._write_status(
                    job.elder_user_id,
                    status="completed",
                    latest_turn_id=self.planner_latest_turn.get(job.elder_user_id),
                    running_job_id=None,
                    last_completed_job_id=job.job_id,
                    last_review_status=planner_result.review.status,
                    last_used_fallback=planner_result.used_fallback,
                )
        except asyncio.CancelledError:
            reason = self.cancel_reasons.pop(job.job_id, "cancelled_by_newer_priority_turn")
            self._mark_stale(job, reason)
            raise
        except Exception as exc:
            self._transition_job(job, "failed", finished_at=utc_now(), error=str(exc))
            self._write_status(
                job.elder_user_id,
                status="failed",
                latest_turn_id=self.planner_latest_turn.get(job.elder_user_id),
                running_job_id=None,
                last_error=str(exc),
            )

    async def _run_rule_planner(
        self,
        _job: PlannerJob,
        assessment: MentalRiskAssessment,
        current_plan: CarePlan,
        context: Optional[Dict[str, Any]] = None,
    ) -> PlannerResult:
        return await self.planning_agent.arun(assessment, current_plan, context=context)

    def _snapshot_context(self, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(context, dict):
            return {}
        keys = {
            "user_id",
            "turn_id",
            "audio_transcript",
            "risk_assessment",
            "care_plan",
            "scene_context",
            "recent_history",
            "recent_history_text",
            "memory_context",
            "semantic_memory_context",
            "emotion_trend",
            "user_profile",
            "music_library_summary",
            "photo_library_summary",
        }
        return {key: context.get(key) for key in keys if key in context}

    def _persist_review_snapshot(
        self,
        assessment: MentalRiskAssessment,
        planner_result: PlannerResult,
    ) -> None:
        assessment.llm_review = self._model_to_dict(planner_result.review)
        self.store.append_user_jsonl(
            assessment.elder_user_id,
            "mental_assessments.jsonl",
            assessment,
        )

    def _persist_actions(
        self,
        job: PlannerJob,
        assessment: MentalRiskAssessment,
        planner_result: PlannerResult,
    ) -> None:
        actions = planner_result.queued_actions
        for action in actions:
            payload = self._model_to_dict(action)
            action_payload = dict(payload.get("payload") or {})
            contract = self._action_contract_payload(action)
            action_payload.update(
                {
                    "planner_job_id": job.job_id,
                    "assessment_id": assessment.id,
                    "source_turn_id": assessment.turn_id,
                    "created_at": utc_now(),
                }
            )
            action_payload.update(contract)
            session = self._create_frontend_action_session(
                job,
                assessment,
                action,
                contract,
                action_payload,
            )
            if session is not None:
                payload["action_session_id"] = session.action_id
                action_payload["action_session_id"] = session.action_id
            payload.update(contract)
            payload["action_type"] = payload.get("action_type") or payload.get("type")
            payload["payload"] = action_payload
            self.store.append_user_jsonl(
                assessment.elder_user_id,
                self.ACTION_AUDIT_FILE,
                payload,
            )

        action_types = {action.type for action in actions}
        if {"family_message", "community_alert"} & action_types:
            self.relay_message_service.create_from_assessment(assessment)
        for action in actions:
            if action.type != "quiet_message":
                continue
            self.relay_message_service.create_quiet_message(
                assessment.elder_user_id,
                action.content,
                title=str(action.payload.get("title") or ""),
                target=str(action.target or "elder"),
                actor_role=str(action.payload.get("actor_role") or "system"),
                direction=str(action.payload.get("direction") or "system_to_elder"),
                payload=dict(action.payload),
            )

    def _action_contract_payload(self, action: Any) -> Dict[str, Any]:
        target_channel = getattr(action, "target_channel", None) or self._default_target_channel(action)
        visibility_scope = getattr(action, "visibility_scope", None) or self._default_visibility_scope(action)
        consent_required = bool(
            getattr(action, "consent_required", False)
            or self._default_consent_required(action)
        )
        approval_required = bool(getattr(action, "approval_required", False))
        idempotency_key = getattr(action, "idempotency_key", None)
        if not idempotency_key:
            idempotency_key = str((getattr(action, "payload", {}) or {}).get("idempotency_key") or "")
        return {
            "contract_version": "target19.v1",
            "target_channel": target_channel,
            "visibility_scope": visibility_scope,
            "consent_required": consent_required,
            "approval_required": approval_required,
            "idempotency_key": idempotency_key,
        }

    def _create_frontend_action_session(
        self,
        job: PlannerJob,
        assessment: MentalRiskAssessment,
        action: Any,
        contract: Dict[str, Any],
        action_payload: Dict[str, Any],
    ):
        action_type_map = {
            "schedule_music": "music",
            "schedule_story": "story",
        }
        session_action_type = action_type_map.get(getattr(action, "type", ""))
        if session_action_type is None:
            return None
        session_payload = dict(action_payload)
        session_payload.update(
            {
                "planner_job_id": job.job_id,
                "assessment_id": assessment.id,
                "turn_id": assessment.turn_id,
                "source_turn_id": assessment.turn_id,
                "content": getattr(action, "content", ""),
                "display_type": getattr(action, "display_type", None),
                "target_channel": contract["target_channel"],
                "visibility_scope": contract["visibility_scope"],
                "consent_required": contract["consent_required"],
                "approval_required": contract["approval_required"],
                "risk_tier": assessment.risk_tier,
            }
        )
        return self.action_session_service.create_session(
            assessment.elder_user_id,
            session_action_type,
            payload=session_payload,
            post_reply=str((getattr(action, "payload", {}) or {}).get("post_reply") or "") or None,
            idempotency_key=str(contract.get("idempotency_key") or ""),
            status="pending" if contract["consent_required"] else "started",
        )

    def _default_target_channel(self, action: Any) -> str:
        action_type = getattr(action, "type", "")
        if action_type == "family_message":
            return "family"
        if action_type == "community_alert":
            return "community"
        if action_type in {"schedule_music", "schedule_story"}:
            return "frontend"
        if action_type == "quiet_message":
            target = str(getattr(action, "target", None) or "elder")
            return target if target in {"elder", "family", "community", "frontend"} else "elder"
        return "background"

    def _default_visibility_scope(self, action: Any) -> str:
        action_type = getattr(action, "type", "")
        if action_type == "family_message":
            return "family"
        if action_type == "community_alert":
            return "community"
        if action_type in {"quiet_message", "schedule_music", "schedule_story"}:
            return "elder"
        return "internal"

    def _default_consent_required(self, action: Any) -> bool:
        action_type = getattr(action, "type", "")
        if action_type in {"schedule_music", "schedule_story"}:
            return True
        if action_type == "quiet_message":
            payload = getattr(action, "payload", {}) or {}
            return (
                str(payload.get("actor_role") or "system") == "family"
                or str(payload.get("direction") or "system_to_elder") == "family_to_elder"
            )
        return False

    def _mark_stale(self, job: PlannerJob, reason: str) -> None:
        self._transition_job(
            job,
            "stale_discarded",
            finished_at=utc_now(),
            stale_reason=reason,
        )
        active_job = self.active_jobs.get(job.elder_user_id)
        if active_job is not None and active_job.job_id != job.job_id:
            current = self.get_status(job.elder_user_id)
            self._write_status(
                job.elder_user_id,
                status=current.status,
                latest_turn_id=current.latest_turn_id,
                running_job_id=current.running_job_id,
                last_discarded_job_id=job.job_id,
            )
        else:
            self._write_status(
                job.elder_user_id,
                status="stale_discarded",
                latest_turn_id=self.planner_latest_turn.get(job.elder_user_id),
                running_job_id=None,
                last_discarded_job_id=job.job_id,
            )

    def _is_superseded(self, job: PlannerJob) -> bool:
        return self.planner_latest_turn.get(job.elder_user_id) != job.base_turn_id

    def _transition_job(self, job: PlannerJob, status: str, **updates: Any) -> None:
        for key, value in updates.items():
            setattr(job, key, value)
        job.status = status
        if job.started_at and job.finished_at:
            job.latency_ms = max(
                0,
                int((job.finished_at - job.started_at).total_seconds() * 1000),
            )
        self._append_job(job)
        self._notify(job, status)

    def _write_status(
        self,
        elder_user_id: str,
        *,
        status: str,
        latest_turn_id: Optional[str],
        running_job_id: Optional[str],
        last_completed_job_id: Optional[str] = None,
        last_discarded_job_id: Optional[str] = None,
        last_error: Optional[str] = None,
        last_review_status: Optional[str] = None,
        last_used_fallback: Optional[bool] = None,
    ) -> None:
        current = self.get_status(elder_user_id)
        data = self._model_to_dict(current)
        data.update(
            {
                "elder_user_id": elder_user_id,
                "status": status,
                "latest_turn_id": latest_turn_id,
                "running_job_id": running_job_id,
                "updated_at": utc_now(),
            }
        )
        if last_completed_job_id is not None:
            data["last_completed_job_id"] = last_completed_job_id
        if last_discarded_job_id is not None:
            data["last_discarded_job_id"] = last_discarded_job_id
        if last_error is not None:
            data["last_error"] = last_error
        if last_review_status is not None:
            data["last_review_status"] = last_review_status
        if last_used_fallback is not None:
            data["last_used_fallback"] = last_used_fallback
        status_model = PlannerStatus(**data)
        self.store.write_user_json(elder_user_id, self.STATUS_FILE, status_model)

    def _append_job(self, job: PlannerJob) -> None:
        self.store.append_user_jsonl(job.elder_user_id, self.JOB_AUDIT_FILE, job)

    def _notify(self, job: PlannerJob, status: str) -> None:
        if self.on_job_event is not None:
            self.on_job_event(job, status)

    def _clear_task(self, elder_user_id: str, job_id: str, _task: asyncio.Task) -> None:
        self.all_tasks.discard(_task)
        self.task_owners.pop(_task, None)
        current = self.planner_tasks.get(elder_user_id)
        if current is _task:
            self.planner_tasks.pop(elder_user_id, None)
        if current is _task and current.done():
            active_job = self.active_jobs.get(elder_user_id)
            if active_job is not None and active_job.job_id == job_id:
                self.active_jobs.pop(elder_user_id, None)
        self.cancel_reasons.pop(job_id, None)

    def _model_to_dict(self, model: Any) -> Dict[str, Any]:
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="python")
        if hasattr(model, "dict"):
            return model.dict()
        return dict(model or {})
