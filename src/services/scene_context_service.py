from typing import Any, Dict, Iterable, Optional


class SceneContextService:
    """Builds one compact scene snapshot shared by fast agents and planners."""

    def __init__(self, user_context_service=None):
        self.user_context_service = user_context_service

    def build(
        self,
        *,
        user_input: str,
        context: Dict[str, Any],
        assessment: Optional[Any] = None,
        care_plan: Optional[Any] = None,
        source: str = "chat",
        action_outcome: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        profile = context.get("user_profile") or {}
        history = context.get("recent_history") or []
        latest_user = self._latest_text(history, "user", exclude=user_input)
        latest_assistant = self._latest_text(history, "assistant")
        plan_data = self._model_to_dict(care_plan) if care_plan is not None else dict(context.get("care_plan") or {})
        assessment_data = self._model_to_dict(assessment) if assessment is not None else dict(context.get("risk_assessment") or {})
        display_name = self._display_name(profile)
        last_assistant_used_name = bool(display_name and display_name in latest_assistant)

        scene = {
            "turn": {
                "turn_id": context.get("turn_id"),
                "source": source,
                "user_input": user_input,
                "audio_transcript": context.get("audio_transcript") or "",
            },
            "current_scene": {
                "risk_tier": assessment_data.get("risk_tier") or plan_data.get("risk_tier") or "safe",
                "primary_state": assessment_data.get("primary_state") or "unknown",
                "next_response_mode": assessment_data.get("next_response_mode") or "",
                "visual_emotion": self._visual_emotion(context.get("visual_analysis")),
                "voice_emotion": context.get("voice_emotion") or "",
            },
            "dialogue_state": {
                "last_user_utterance": latest_user,
                "last_assistant_reply": latest_assistant,
                "recent_history_text": context.get("recent_history_text") or "",
                "open_question": self._infer_open_question(latest_assistant),
            },
            "care_plan": {
                "version": plan_data.get("version", 0),
                "active_domain": plan_data.get("active_domain") or "general",
                "risk_tier": plan_data.get("risk_tier") or "safe",
                "current_stage": plan_data.get("current_stage") or "companionship",
                "stage_goal": plan_data.get("stage_goal") or "",
                "next_turn_goal": plan_data.get("next_turn_goal") or "",
                "target_agent": plan_data.get("target_agent") or "",
                "allowed_interventions": plan_data.get("allowed_interventions") or [],
                "blocked_interventions": plan_data.get("blocked_interventions") or [],
            },
            "retrieval": {
                "memory_context": context.get("memory_context") or "",
                "semantic_memory_context": context.get("semantic_memory_context") or "",
            },
            "libraries": {
                "music_library_summary": context.get("music_library_summary") or [],
                "photo_library_summary": context.get("photo_library_summary") or "",
            },
            "action_outcome": dict(action_outcome or context.get("last_action_outcome") or {}),
            "addressing_policy": {
                "display_name": display_name,
                "last_assistant_used_name": last_assistant_used_name,
                "allow_name_once": bool(display_name and not last_assistant_used_name),
            },
        }
        return scene

    def compact_for_prompt(self, scene_context: Dict[str, Any], *, max_chars: int = 1800) -> str:
        if not scene_context:
            return ""
        parts = [
            f"turn: {scene_context.get('turn', {})}",
            f"current_scene: {scene_context.get('current_scene', {})}",
            f"dialogue_state: {scene_context.get('dialogue_state', {})}",
            f"care_plan: {scene_context.get('care_plan', {})}",
            f"action_outcome: {scene_context.get('action_outcome', {})}",
            f"addressing_policy: {scene_context.get('addressing_policy', {})}",
        ]
        return "\n".join(parts)[:max_chars]

    def _latest_text(self, history: Iterable[Dict[str, Any]], role: str, *, exclude: str = "") -> str:
        excluded = str(exclude or "").strip()
        for item in reversed(list(history or [])):
            if not isinstance(item, dict) or item.get("role") != role:
                continue
            content = str(item.get("content") or "").strip()
            if content and content != excluded:
                return content
        return ""

    def _display_name(self, profile: Dict[str, Any]) -> str:
        if self.user_context_service is not None:
            return self.user_context_service.display_name(profile, fallback="")
        name = str((profile or {}).get("name") or "").strip()
        return "" if name.lower() in {"unknown", "none", "null"} else name

    def _visual_emotion(self, visual: Any) -> str:
        if isinstance(visual, dict):
            return str(visual.get("emotion") or "").strip()
        return ""

    def _infer_open_question(self, text: str) -> str:
        value = str(text or "").strip()
        if value.endswith(("?", "？")):
            return value
        return ""

    def _model_to_dict(self, model: Any) -> Dict[str, Any]:
        if isinstance(model, dict):
            return dict(model)
        if hasattr(model, "model_dump"):
            return model.model_dump(mode="python")
        if hasattr(model, "dict"):
            return model.dict()
        return {}
