import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional

from src.schemas.actions import ActionCompleteRequest, ActionConsentRequest, ActionSession, ActionStatus, ActionType
from src.schemas.mental_health import InterventionLog
from src.services.data_store import DataStore


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ActionSessionService:
    """Durable lifecycle manager for frontend-executed actions such as music playback."""

    SESSIONS_FILE = "action_sessions.json"
    AUDIT_FILE = "action_sessions.jsonl"
    INTERVENTION_AUDIT_FILE = "intervention_log.jsonl"
    TERMINAL_STATUSES = {"completed", "interrupted", "cancelled", "failed"}

    def __init__(self, store: Optional[DataStore] = None):
        self.store = store or DataStore()
        self._locks: Dict[str, RLock] = {}
        self._locks_guard = RLock()

    def create_session(
        self,
        elder_user_id: str,
        action_type: ActionType,
        *,
        payload: Optional[Dict[str, Any]] = None,
        post_reply: Optional[str] = None,
        action_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        status: ActionStatus = "started",
    ) -> ActionSession:
        user_id = self._normalize_user_id(elder_user_id)
        with self._lock_for(user_id):
            payload_data = dict(payload or {})
            if idempotency_key:
                payload_data["idempotency_key"] = idempotency_key
            session = ActionSession(
                action_id=action_id or f"action_{uuid.uuid4().hex}",
                elder_user_id=user_id,
                action_type=action_type,
                status=status,
                payload=payload_data,
                post_reply=post_reply or "",
            )
            sessions = self._load_sessions_unlocked(user_id)
            if idempotency_key:
                existing = next(
                    (
                        item for item in sessions
                        if item.action_type == action_type
                        and item.payload.get("idempotency_key") == idempotency_key
                    ),
                    None,
                )
                if existing is not None:
                    return existing
            sessions.append(session)
            self._save_sessions_unlocked(user_id, sessions)
            self.store.append_user_jsonl(user_id, self.AUDIT_FILE, session)
            return session

    def list_pending_actions(
        self,
        elder_user_id: str,
        *,
        target_channel: Optional[str] = "frontend",
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        user_id = self._normalize_user_id(elder_user_id)
        with self._lock_for(user_id):
            sessions = self._load_sessions_unlocked(user_id)
            pending: List[Dict[str, Any]] = []
            for session in sessions:
                if session.status != "pending":
                    continue
                payload = dict(session.payload or {})
                if target_channel and payload.get("target_channel") != target_channel:
                    continue
                if payload.get("consent_required") is not True:
                    continue
                pending.append(self._build_pending_action(session))
            if limit is not None:
                return pending[: max(0, limit)]
            return pending

    def consent_action(
        self,
        action_id: str,
        request: ActionConsentRequest,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        user_id = self._normalize_user_id(request.elder_user_id)
        target_id = self._normalize_action_id(action_id)
        decided_at = request.decided_at or now or utc_now()
        with self._lock_for(user_id):
            sessions = self._load_sessions_unlocked(user_id)
            index = next((idx for idx, item in enumerate(sessions) if item.action_id == target_id), None)
            if index is None:
                raise ValueError(f"Action session not found: {target_id}")

            session = sessions[index]
            if session.status == "started" and request.accepted:
                return self._build_consent_result(session, idempotent_replay=True)
            if session.status in self.TERMINAL_STATUSES:
                raise ValueError(f"Action session already ended: {target_id}")
            if session.status != "pending":
                raise ValueError(f"Action session is not pending: {target_id}")

            consent_payload = {
                "accepted": request.accepted,
                "text": request.text or "",
                "source": request.source,
                "decided_at": decided_at,
            }
            if request.payload:
                consent_payload["payload"] = dict(request.payload)

            if request.accepted:
                session.status = "started"
            else:
                session.status = "cancelled"
                session.ended_at = decided_at
                session.result = {"status": "cancelled", "consent": consent_payload}
            session.updated_at = decided_at
            session.payload = dict(session.payload or {})
            session.payload["consent"] = consent_payload
            sessions[index] = session

            self._save_sessions_unlocked(user_id, sessions)
            self.store.append_user_jsonl(user_id, self.AUDIT_FILE, session)
            return self._build_consent_result(session, idempotent_replay=False)

    def get_session(self, elder_user_id: str, action_id: str) -> Optional[ActionSession]:
        user_id = self._normalize_user_id(elder_user_id)
        target_id = self._normalize_action_id(action_id)
        with self._lock_for(user_id):
            return self._find_session_unlocked(user_id, target_id)

    def list_sessions(self, elder_user_id: str) -> List[ActionSession]:
        user_id = self._normalize_user_id(elder_user_id)
        with self._lock_for(user_id):
            return self._load_sessions_unlocked(user_id)

    def complete_action(
        self,
        request: ActionCompleteRequest,
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        user_id = self._normalize_user_id(request.elder_user_id)
        target_id = self._normalize_action_id(request.action_id)
        with self._lock_for(user_id):
            sessions = self._load_sessions_unlocked(user_id)
            index = next((idx for idx, item in enumerate(sessions) if item.action_id == target_id), None)
            if index is None:
                raise ValueError(f"Action session not found: {target_id}")

            session = sessions[index]
            if session.action_type != request.action_type:
                raise ValueError(
                    f"Action type mismatch for {target_id}: expected {session.action_type}, got {request.action_type}"
                )

            if session.status in self.TERMINAL_STATUSES:
                return self._build_completion_result(session, idempotent_replay=True)

            finished_at = request.finished_at or now or utc_now()
            session.status = request.status
            session.updated_at = finished_at
            session.ended_at = finished_at
            session.completed_at = finished_at if request.status == "completed" else None
            session.completed_intervention = request.status == "completed"
            session.result = self._build_result_payload(request)
            sessions[index] = session

            self._save_sessions_unlocked(user_id, sessions)
            self.store.append_user_jsonl(user_id, self.AUDIT_FILE, session)
            self.store.append_user_jsonl(user_id, self.INTERVENTION_AUDIT_FILE, self._build_intervention_log(session))
            return self._build_completion_result(session, idempotent_replay=False)

    def _build_result_payload(self, request: ActionCompleteRequest) -> Dict[str, Any]:
        payload = dict(request.payload or {})
        if request.music_name is not None:
            payload["music_name"] = request.music_name
        if request.played_seconds is not None:
            payload["played_seconds"] = request.played_seconds
        if request.total_seconds is not None:
            payload["total_seconds"] = request.total_seconds
        if request.interrupt_reason:
            payload["interrupt_reason"] = request.interrupt_reason
        payload["status"] = request.status
        return payload

    def _build_pending_action(self, session: ActionSession) -> Dict[str, Any]:
        payload = dict(session.payload or {})
        content = (
            payload.get("content")
            or payload.get("confirmation_text")
            or payload.get("reason_summary")
            or ""
        )
        return {
            "action_id": session.action_id,
            "action_type": session.action_type,
            "status": session.status,
            "target_channel": payload.get("target_channel"),
            "visibility_scope": payload.get("visibility_scope"),
            "consent_required": bool(payload.get("consent_required")),
            "approval_required": bool(payload.get("approval_required")),
            "source": "background_planner" if payload.get("planner_job_id") else payload.get("source", "unknown"),
            "source_turn_id": payload.get("source_turn_id") or payload.get("turn_id"),
            "content": content,
            "payload": payload,
            "post_reply": session.post_reply,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    def _build_consent_result(self, session: ActionSession, *, idempotent_replay: bool) -> Dict[str, Any]:
        return {
            "session": session,
            "action_id": session.action_id,
            "action_type": session.action_type,
            "status": session.status,
            "payload": dict(session.payload or {}),
            "post_reply": session.post_reply,
            "idempotent_replay": idempotent_replay,
        }

    def _build_intervention_log(self, session: ActionSession) -> InterventionLog:
        payload = dict(session.payload or {})
        payload.update(
            {
                "action_id": session.action_id,
                "action_type": session.action_type,
                "status": session.status,
                "completed_intervention": session.completed_intervention,
                "result": dict(session.result or {}),
            }
        )
        risk_tier = str(payload.get("risk_tier") or "safe")
        if risk_tier not in {"safe", "low", "medium", "high", "crisis"}:
            risk_tier = "safe"
        return InterventionLog(
            id=f"intervention_{uuid.uuid4().hex}",
            turn_id=str(payload.get("turn_id") or session.action_id),
            elder_user_id=session.elder_user_id,
            risk_tier=risk_tier,
            intervention_type=session.action_type,
            stage=str(payload.get("stage") or ""),
            goal=str(payload.get("goal") or ""),
            payload=payload,
            result=session.status,
        )

    def _build_completion_result(self, session: ActionSession, *, idempotent_replay: bool) -> Dict[str, Any]:
        next_turn_goal = self._next_turn_goal(session)
        return {
            "session": session,
            "post_reply": session.post_reply if session.status == "completed" else None,
            "next_turn_goal": next_turn_goal,
            "care_plan_patch": {},
            "completed_intervention": session.completed_intervention,
            "idempotent_replay": idempotent_replay,
        }

    def _next_turn_goal(self, session: ActionSession) -> str:
        if session.action_type == "music":
            if session.status == "completed":
                return "gently check whether the music helped"
            if session.status == "interrupted":
                return "gently ask whether to switch songs or change support mode"
        if session.status == "completed":
            return "gently continue after the completed action"
        return "gently continue after the action ended"

    def _load_sessions_unlocked(self, elder_user_id: str) -> List[ActionSession]:
        raw_sessions = self.store.read_user_json(elder_user_id, self.SESSIONS_FILE, default=[])
        if not isinstance(raw_sessions, list):
            return []
        sessions: List[ActionSession] = []
        for item in raw_sessions:
            if not isinstance(item, dict):
                continue
            sessions.append(self._parse_session(item))
        return sessions

    def _save_sessions_unlocked(self, elder_user_id: str, sessions: List[ActionSession]) -> None:
        self.store.write_user_json(elder_user_id, self.SESSIONS_FILE, sessions)

    def _find_session_unlocked(self, elder_user_id: str, action_id: str) -> Optional[ActionSession]:
        sessions = self._load_sessions_unlocked(elder_user_id)
        return next((item for item in sessions if item.action_id == action_id), None)

    def _parse_session(self, item: Dict[str, Any]) -> ActionSession:
        if hasattr(ActionSession, "model_validate"):
            return ActionSession.model_validate(item)
        return ActionSession.parse_obj(item)

    def _lock_for(self, elder_user_id: str) -> RLock:
        with self._locks_guard:
            return self._locks.setdefault(elder_user_id, RLock())

    def _normalize_user_id(self, elder_user_id: str) -> str:
        text = str(elder_user_id or "").strip()
        if not text:
            raise ValueError("elder_user_id is required")
        return text

    def _normalize_action_id(self, action_id: str) -> str:
        text = str(action_id or "").strip()
        if not text:
            raise ValueError("action_id is required")
        return text
