
import requests
from typing import Any, Optional, List, Dict
from langchain_core.tools import tool
import json
import random
import re
from difflib import SequenceMatcher
from src.config import Config
from src.services.user_context_service import UserContextService
from src.utils.logger import logger

class ProfessionalSkills:
    """
    空巢老人陪伴系统 - 专业技能库 (Skills)
    这些工具将被绑定到 Agent 上，使其具备“行动能力”。
    """

    photo_library_service = None
    music_library_service = None

    @classmethod
    def register_photo_library_service(cls, service) -> None:
        cls.photo_library_service = service

    @classmethod
    def register_music_library_service(cls, service) -> None:
        cls.music_library_service = service

    PHOTO_ACTION_WORDS = [
        "请帮我", "麻烦你", "麻烦", "帮我", "给我", "替我", "我想看看", "我想看", "我想找", "我想",
        "想看看", "想看", "看看", "看下", "看一下", "找找", "找一下", "找一找", "找",
        "搜搜", "搜一下", "搜", "翻翻", "翻一下", "来点", "我要", "想要"
    ]
    PHOTO_MEDIA_WORDS = ["照片", "相片", "图片", "视频", "相册", "影集", "那张", "这些", "这个", "一下"]
    PHOTO_LEADING_NOISE = ["关于", "有关", "关于我", "我家", "我们家", "咱家", "我的", "我"]
    PHOTO_SEMANTIC_FIELDS = [
        "description",
        "caption",
        "tags",
        "people",
        "location",
        "time_text",
        "taken_at",
        "event",
        "album",
        "originalFileName",
    ]
    PHOTO_SCORE_WEIGHTS = {
        "people": 180.0,
        "tags": 150.0,
        "description": 140.0,
        "caption": 130.0,
        "location": 110.0,
        "time_text": 95.0,
        "taken_at": 70.0,
        "event": 90.0,
        "album": 70.0,
        "originalFileName": 80.0,
    }

    @staticmethod
    def normalize_photo_keyword(text: str) -> str:
        t = (text or "").strip()
        if not t:
            return ""
        t = re.sub(r"[“”\"'`]+", "", t)
        t = re.sub(r"[，。,！!？?、；;：:\s]+", " ", t).strip()

        changed = True
        while changed and t:
            changed = False
            for w in sorted(ProfessionalSkills.PHOTO_ACTION_WORDS, key=len, reverse=True):
                if t.startswith(w):
                    t = t[len(w):].strip()
                    changed = True
            for w in sorted(ProfessionalSkills.PHOTO_LEADING_NOISE, key=len, reverse=True):
                if t.startswith(w):
                    t = t[len(w):].strip()
                    changed = True

        for w in ProfessionalSkills.PHOTO_MEDIA_WORDS:
            t = t.replace(w, "")

        t = re.sub(r"\b(一下|一张|一些|一个|这张|那张)\b", "", t)
        t = re.sub(r"[，。,！!？?、；;：:\s]+", " ", t).strip()
        t = t.rstrip("的地得 ").strip()
        return t

    @staticmethod
    def flatten_photo_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, tuple, set)):
            return " ".join(ProfessionalSkills.flatten_photo_value(v) for v in value if v is not None)
        if isinstance(value, dict):
            return " ".join(ProfessionalSkills.flatten_photo_value(v) for v in value.values() if v is not None)
        return str(value)

    @staticmethod
    def normalize_photo_match_text(value) -> str:
        text = ProfessionalSkills.flatten_photo_value(value)
        text = ProfessionalSkills.normalize_photo_keyword(text).lower()
        text = text.replace("的", "")
        text = re.sub(r"\s+", "", text)
        return text

    @staticmethod
    def photo_metadata_available(item: Dict) -> bool:
        return any(
            ProfessionalSkills.flatten_photo_value(item.get(field)).strip()
            for field in ["description", "caption", "tags", "people", "location", "time_text", "taken_at", "event", "album"]
        )

    @staticmethod
    def score_photo_record(item: Dict, query_text: str) -> float:
        if not query_text:
            return 0.0

        q = ProfessionalSkills.normalize_photo_match_text(query_text)
        if not q:
            return 0.0

        score = 0.0
        searchable_parts = []
        for field in ProfessionalSkills.PHOTO_SEMANTIC_FIELDS:
            field_text = ProfessionalSkills.normalize_photo_match_text(item.get(field))
            if not field_text:
                continue
            searchable_parts.append(field_text)
            weight = ProfessionalSkills.PHOTO_SCORE_WEIGHTS.get(field, 60.0)
            if q in field_text:
                score += weight
            elif field_text in q:
                score += weight * 0.5

        bag = "|".join(searchable_parts)
        if not bag:
            return 0.0

        score += SequenceMatcher(None, q, bag).ratio() * 60.0
        token_parts = [
            p for p in re.split(r"[^\w\u4e00-\u9fff]+", ProfessionalSkills.normalize_photo_keyword(query_text))
            if p
        ]
        for part in token_parts:
            p = ProfessionalSkills.normalize_photo_match_text(part)
            if not p:
                continue
            for field in ProfessionalSkills.PHOTO_SEMANTIC_FIELDS:
                field_text = ProfessionalSkills.normalize_photo_match_text(item.get(field))
                if not field_text:
                    continue
                if p in field_text:
                    score += ProfessionalSkills.PHOTO_SCORE_WEIGHTS.get(field, 60.0) * 0.25
                elif SequenceMatcher(None, p, field_text).ratio() >= 0.6:
                    score += ProfessionalSkills.PHOTO_SCORE_WEIGHTS.get(field, 60.0) * 0.1

        return score

    @staticmethod
    def build_photo_result(item: Dict) -> Dict:
        file_url = item.get("url")
        if not file_url:
            file_uuid = item.get("uuid")
            if file_uuid:
                file_url = f"{Config.FILE_SERVICE_BASE_URL}/api/file/download/{file_uuid}"

        description = (
            ProfessionalSkills.flatten_photo_value(item.get("description")).strip()
            or ProfessionalSkills.flatten_photo_value(item.get("caption")).strip()
        )
        original_file_name = item.get("originalFileName", "未知照片")

        return {
            "url": file_url,
            "desc": description or original_file_name,
            "type": item.get("fileType"),
            "tags": item.get("tags"),
            "description": description,
            "people": item.get("people"),
            "location": item.get("location"),
            "time_text": item.get("time_text") or item.get("taken_at"),
            "caption_source": item.get("caption_source"),
            "original_file_name": original_file_name,
            "metadata_available": ProfessionalSkills.photo_metadata_available(item),
        }

    @staticmethod
    @tool
    def search_family_photos(keyword: str, username: Optional[str] = None) -> str:
        """
        搜索家庭相册或视频。
        当老人提到“想看孙子”、“记得那年去公园”、“给我找找照片”、“看看全家福”时使用。
        如果不确定具体关键词，可以尝试用 "all" 或 "recent" 来展示最近的照片。
        本工具只检索已有照片元数据，不在实时对话中调用视觉模型生成图片描述。
        
        Args:
            keyword: 搜索关键词，如 "孙女", "全家福", "公园", "吃饭"。
                     如果用户只说"看看照片"，可以使用 "random" 或 "all"。
            username: 用户名（可选），用于过滤特定用户的照片。通常从用户画像中获取。
            
        Returns:
            JSON 格式的照片/视频列表字符串，包含 URL (可直接访问的MinIO链接) 和文件名。
        """
        logger.info(f"[Skill: SearchPhotos] 正在搜索相册: 关键词='{keyword}', 用户='{username}'")
        
        try:
            raw_keyword = (keyword or "").strip()
            lowered = raw_keyword.lower()

            def build_query_variants(text: str) -> List[str]:
                base = ProfessionalSkills.normalize_photo_keyword(text)
                if not base:
                    return [""]
                variants = [base, base.replace("的", ""), base.rstrip("的")]
                alias_map = {
                    "孙子的": "孙子",
                    "孙女的": "孙女",
                    "儿子的": "儿子",
                    "女儿的": "女儿",
                    "老伴的": "老伴"
                }
                for k, v in alias_map.items():
                    if k in base:
                        variants.append(base.replace(k, v))
                seen = set()
                ordered = []
                for v in variants:
                    vv = v.strip()
                    if vv and vv not in seen:
                        ordered.append(vv)
                        seen.add(vv)
                return ordered[:4] if ordered else [""]

            def request_files(search_key: str) -> List[Dict]:
                params = {"keyword": search_key}
                if username:
                    params["username"] = username
                response = requests.get(
                    f"{Config.FILE_SERVICE_BASE_URL}/api/file/search",
                    params=params,
                    timeout=Config.EXTERNAL_REQUEST_TIMEOUT
                )
                if response.status_code != 200:
                    raise RuntimeError(f"search_failed_status_{response.status_code}")
                data = response.json()
                return data if isinstance(data, list) else []

            list_all_hints = {"all", "recent", "照片", "看看", "相册", "影集", "图库"}
            random_hints = {"random", "随机", "来点随机", "随便"}

            if lowered in random_hints or any(h in raw_keyword for h in random_hints):
                raw_keyword = "random"

            normalized_keyword = ProfessionalSkills.normalize_photo_keyword(raw_keyword)
            pure_list_request = (
                lowered in list_all_hints
                or (not normalized_keyword and any(h in raw_keyword for h in list_all_hints))
            )
            search_param = "" if pure_list_request else normalized_keyword

            local_service = getattr(ProfessionalSkills, "photo_library_service", None)
            if local_service is not None and username:
                try:
                    local_query = "" if raw_keyword == "random" else search_param
                    local_photos = local_service.search_for_agent(username, local_query, limit=5)
                    if raw_keyword == "random" and local_photos:
                        local_photos = random.sample(local_photos, min(len(local_photos), 5))
                    if local_photos:
                        return json.dumps(
                            {
                                "status": "success",
                                "photos": local_photos,
                                "source": "local_photo_library",
                            },
                            ensure_ascii=False,
                        )
                except Exception as local_exc:
                    logger.warning(f"Local photo library search failed: {local_exc}")

            variants = build_query_variants(search_param)
            if raw_keyword == "random":
                files = request_files("")
            elif search_param == "":
                files = request_files("")
            else:
                merged_map: Dict[str, Dict] = {}
                for q in variants + [""]:
                    for item in request_files(q):
                        key = str(item.get("uuid") or item.get("url") or item.get("filePath") or item.get("originalFileName") or id(item))
                        old = merged_map.get(key)
                        score = ProfessionalSkills.score_photo_record(item, search_param)
                        if old is None:
                            merged_map[key] = item
                            merged_map[key]["_score"] = score
                        else:
                            old["_score"] = max(float(old.get("_score", 0.0)), score)
                files = [
                    item for item in merged_map.values()
                    if float(item.get("_score", 0.0)) >= 30.0
                ]
                files.sort(key=lambda x: float(x.get("_score", 0.0)), reverse=True)

            if files is not None:
                if not files:
                    return json.dumps({
                        "status": "empty", 
                        "message": "哎呀，小暖翻遍了相册也没找到这张照片呢。是不是孩子们还没传上来呀？要不您跟我讲讲那时候的事儿？"
                    }, ensure_ascii=False)
                
                results = []
                if raw_keyword == "random":
                    files = random.sample(files, min(len(files), 5))

                for f in files:
                    results.append(ProfessionalSkills.build_photo_result(f))
                return json.dumps({"status": "success", "photos": results}, ensure_ascii=False)
            return json.dumps({
                "status": "error", 
                "message": "哎呀，相册柜好像卡住了，打不开了。稍微等会儿小暖再试试，您别急。" 
            }, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"搜索相册失败: {e}")
            return json.dumps({
                "status": "error", 
                "message": "哎呀，相册柜好像卡住了，打不开了。稍微等会儿小暖再试试，您别急。"
            }, ensure_ascii=False)

    @staticmethod
    @tool
    def emergency_contact(reason: str, level: str = "medium") -> str:
        """
        紧急联系子女、社区或前端 SOS 流程。当检测到老人身体不适、跌倒风险
        或情绪极度崩溃时使用。

        注意：该工具只返回“要触发哪些渠道”的结构化请求，不模拟已经拨打
        120 或已经完成上门处置；社区侧默认只看到脱敏摘要，不暴露老人原话。
        
        Args:
            reason: 触发紧急联系的原因，如 "老人表示胸闷", "检测到摔倒", "情绪失控哭泣"
            level: 紧急等级，"low" (仅发消息), "medium" (发消息+语音), "high" (直接电话+社区联动)
            
        Returns:
            JSON 字符串，包含 trigger_sos、level、recommended_channels、
            family_message、community_message 等字段
        """
        logger.info(f"[Skill: Emergency] 触发紧急联系: 原因={reason}, 等级={level}")

        normalized_level = (level or "medium").lower()
        if normalized_level not in {"low", "medium", "high"}:
            normalized_level = "medium"

        actions = []
        recommended_channels = ["family"]
        trigger_sos = normalized_level in {"medium", "high"}

        if normalized_level == "low":
            actions.append("notify_family_message")
        elif normalized_level == "medium":
            actions.append("notify_family_message")
            actions.append("frontend_voice_prompt")
        elif normalized_level == "high":
            actions.append("trigger_frontend_sos")
            actions.append("notify_family_call")
            actions.append("notify_community_watch")
            recommended_channels.append("community")

        reason_text = (reason or "").strip()
        family_message = (
            f"老人发出紧急求助：{reason_text}"
            if reason_text
            else "老人发出紧急求助，请尽快确认情况。"
        )
        community_message = "有老人发出紧急求助，请社区值守端关注。"

        return json.dumps({
            "status": "success",
            "trigger_sos": trigger_sos,
            "level": normalized_level,
            "reason_summary": "elder_reported_emergency",
            "family_message": family_message,
            "community_message": community_message,
            "community_raw_quote_visible": False,
            "recommended_channels": recommended_channels,
            "actions": actions
        }, ensure_ascii=False)

    @staticmethod
    @tool
    def play_music(query: str, elder_user_id: Optional[str] = None) -> str:
        """
        触发前端音乐播放。
        当前后端不真正播放音乐，只返回 trigger_music=true 供前端拉起播放器。

        Args:
            query: 用户想听的歌曲、歌手或音乐描述

        Returns:
            JSON 字符串，包含 trigger_music 和 query 字段
        """
        logger.info(f"[Skill: Music] 触发音乐播放: {query}")
        payload = {
            "status": "success",
            "intent": "play_music",
            "trigger_music": True,
            "query": query,
            "source": "agent"
        }
        local_service = getattr(ProfessionalSkills, "music_library_service", None)
        if local_service is not None and elder_user_id:
            try:
                match = local_service.match_song(elder_user_id, query or "", limit=1)
                song = match.get("song") if isinstance(match, dict) else None
                if song:
                    payload.update(
                        {
                            "music_id": song.get("music_id"),
                            "music_name": song.get("name") or query,
                            "playable_ref": song.get("playable_ref"),
                            "music_description": song.get("description", ""),
                            "library_match": match,
                            "source": "music_library",
                        }
                    )
            except Exception as exc:
                logger.warning(f"Local music library match failed: {exc}")
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    @tool
    def record_health_complaint(symptom: str, elder_user_id: str = "user_001") -> str:
        """
        记录健康主诉。当老人提到“头疼”、“腿疼”、“睡不着”时使用。    
        
        Args:
            symptom: 症状描述
            
        Returns:
            记录结果
        """
        logger.info(f"[Skill: HealthLog] 记录健康日志: {symptom}")
        
        try:
            result = ProfessionalSkills.record_health_complaint_to_service(
                symptom,
                elder_user_id=elder_user_id,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error(f"记录健康主诉失败: {e}")
            return json.dumps({
                "status": "error",
                "message": f"记录症状失败: {str(e)}，请稍后再试。",
            }, ensure_ascii=False)

    @staticmethod
    def record_health_complaint_to_service(
        symptom: str,
        *,
        elder_user_id: str = "user_001",
        user_context_service: UserContextService | None = None,
    ) -> Dict[str, Any]:
        service = user_context_service or UserContextService()
        user_id = str(elder_user_id or "user_001").strip() or "user_001"
        symptom_text = str(symptom or "").strip()
        profile = service.update_profile(user_id, {"health_condition": symptom_text})
        return {
            "status": "success",
            "elder_user_id": user_id,
            "record_type": "health_condition",
            "symptom": symptom_text,
            "profile_health_condition": profile.get("health_condition", []),
        }
