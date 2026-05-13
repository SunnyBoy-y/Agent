
import requests
from typing import Optional, List, Dict
from langchain_core.tools import tool
import json
import random
import re
from difflib import SequenceMatcher
from src.utils.rag_helper import RAGHelper  # 导入 RAGHelper 以操作用户画像
from src.config import Config
from src.utils.logger import logger

class ProfessionalSkills:
    """
    空巢老人陪伴系统 - 专业技能库 (Skills)
    这些工具将被绑定到 Agent 上，使其具备“行动能力”。
    """

    PHOTO_ACTION_WORDS = [
        "请帮我", "麻烦你", "麻烦", "帮我", "给我", "替我", "我想看看", "我想看", "我想找", "我想",
        "想看看", "想看", "看看", "看下", "看一下", "找找", "找一下", "找一找", "找",
        "搜搜", "搜一下", "搜", "翻翻", "翻一下", "来点", "我要", "想要"
    ]
    PHOTO_MEDIA_WORDS = ["照片", "相片", "图片", "视频", "相册", "影集", "那张", "这些", "这个", "一下"]
    PHOTO_LEADING_NOISE = ["关于", "有关", "关于我", "我家", "我们家", "咱家", "我的", "我"]

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
    @tool
    def search_family_photos(keyword: str, username: Optional[str] = None) -> str:
        """
        搜索家庭相册或视频。
        当老人提到“想看孙子”、“记得那年去公园”、“给我找找照片”、“看看全家福”时使用。
        如果不确定具体关键词，可以尝试用 "all" 或 "recent" 来展示最近的照片。
        
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

            def normalize_for_match(text: str) -> str:
                t = ProfessionalSkills.normalize_photo_keyword(text).lower()
                t = t.replace("的", "")
                t = re.sub(r"\s+", "", t)
                return t

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

            def score_file(item: Dict, query_text: str) -> float:
                if not query_text:
                    return 0.0
                q = normalize_for_match(query_text)
                if not q:
                    return 0.0
                name = normalize_for_match(str(item.get("originalFileName", "")))
                tags = normalize_for_match(str(item.get("tags", "")))
                bag = f"{name}|{tags}"
                score = 0.0
                if q and q in bag:
                    score += 140.0
                if q and q in name:
                    score += 60.0
                ratio = SequenceMatcher(None, q, bag).ratio()
                score += ratio * 80.0
                token_parts = [p for p in re.split(r"[^\w\u4e00-\u9fff]+", ProfessionalSkills.normalize_photo_keyword(query_text)) if p]
                for part in token_parts:
                    p = normalize_for_match(part)
                    if not p:
                        continue
                    if p in bag:
                        score += 30.0
                    elif SequenceMatcher(None, p, bag).ratio() >= 0.6:
                        score += 12.0
                return score

            list_all_hints = {"all", "recent", "照片", "看看", "相册", "影集", "图库"}
            random_hints = {"random", "随机", "来点随机", "随便"}

            if lowered in random_hints or any(h in raw_keyword for h in random_hints):
                raw_keyword = "random"

            search_param = raw_keyword
            if lowered in list_all_hints or any(h in raw_keyword for h in list_all_hints):
                search_param = ""
            else:
                search_param = ProfessionalSkills.normalize_photo_keyword(raw_keyword)

            variants = build_query_variants(search_param)
            if raw_keyword == "random":
                files = request_files("")
            elif search_param == "":
                files = request_files("")
            else:
                merged_map: Dict[str, Dict] = {}
                for q in variants:
                    for item in request_files(q):
                        key = str(item.get("uuid") or item.get("url") or item.get("filePath") or item.get("originalFileName") or id(item))
                        old = merged_map.get(key)
                        if old is None:
                            merged_map[key] = item
                            merged_map[key]["_score"] = score_file(item, search_param)
                        else:
                            old["_score"] = max(float(old.get("_score", 0.0)), score_file(item, search_param))
                files = list(merged_map.values())
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
                    file_url = f.get("url")
                    if not file_url:
                        file_uuid = f.get("uuid")
                        if file_uuid:
                            file_url = f"{Config.FILE_SERVICE_BASE_URL}/api/file/download/{file_uuid}"
                    
                    results.append({
                        "url": file_url,
                        "desc": f.get("originalFileName", "未知照片"),
                        "type": f.get("fileType"),
                        "tags": f.get("tags")
                    })
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
        紧急联系子女或社区。当检测到老人身体不适、跌倒风险或情绪极度崩溃时使用。
        前端只需要关注 trigger_sos 是否为 true，即表示要拉起 SOS 联系逻辑。
        
        Args:
            reason: 触发紧急联系的原因，如 "老人表示胸闷", "检测到摔倒", "情绪失控哭泣"
            level: 紧急等级，"low" (仅发消息), "medium" (发消息+语音), "high" (直接电话+社区联动)
            
        Returns:
            JSON 字符串，包含 trigger_sos、level、reason 等字段
        """
        logger.info(f"[Skill: Emergency] 触发紧急联系: 原因={reason}, 等级={level}")
        
        actions = []
        if level == "low":
            actions.append("已发送微信通知给儿子张伟")
        elif level == "medium":
            actions.append("已发送微信通知给儿子张伟")
            actions.append("已发送短信预警")
        elif level == "high":
            actions.append("正在拨打 120 急救电话 (模拟)")
            actions.append("已通知社区网格员上门查看")
            actions.append("正在呼叫儿子张伟电话")

        return json.dumps({
            "status": "success",
            "trigger_sos": True,
            "level": level,
            "reason": reason,
            "actions": actions
        }, ensure_ascii=False)

    @staticmethod
    @tool
    def play_music(query: str) -> str:
        """
        触发前端音乐播放。
        当前后端不真正播放音乐，只返回 trigger_music=true 供前端拉起播放器。

        Args:
            query: 用户想听的歌曲、歌手或音乐描述

        Returns:
            JSON 字符串，包含 trigger_music 和 query 字段
        """
        logger.info(f"[Skill: Music] 触发音乐播放: {query}")
        return json.dumps({
            "status": "success",
            "intent": "play_music",
            "trigger_music": True,
            "query": query,
            "source": "agent"
        }, ensure_ascii=False)

    @staticmethod
    @tool
    def record_health_complaint(symptom: str) -> str:
        """
        记录健康主诉。当老人提到“头疼”、“腿疼”、“睡不着”时使用。    
        
        Args:
            symptom: 症状描述
            
        Returns:
            记录结果
        """
        logger.info(f"[Skill: HealthLog] 记录健康日志: {symptom}")
        
        try:
            # 实例化 RAGHelper (虽然有点重，但能复用逻辑)
            # 注意：在生产环境中，最好通过依赖注入传递单例实例
            rag = RAGHelper()
            
            # 将症状添加到用户画像的 'health_condition' 字段中
            rag.update_user_profile("health_condition", symptom)
            
            return f"已将症状 '{symptom}' 写入用户健康画像，系统将持续关注。"
        except Exception as e:
            logger.error(f"记录健康主诉失败: {e}")
            return f"记录症状失败: {str(e)}，请稍后再试。"
