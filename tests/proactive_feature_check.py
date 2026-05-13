import asyncio
import json
import os
import sys
from datetime import datetime, timedelta


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.proactive_agent import ProactiveAgent
from src.utils.rag_helper import RAGHelper


def ts(delta_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%d %H:%M:%S")


def write_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def main():
    rag = RAGHelper()
    proactive = ProactiveAgent()

    backup = {}
    for path in [rag.memory_file, rag.profile_file, rag.emotion_file, rag.agent_status_file]:
        with open(path, "r", encoding="utf-8") as f:
            backup[path] = f.read()

    try:
        base_profile = {
            "name": "王爷爷",
            "health_condition": [],
            "family_members": ["女儿", "孙女"],
            "preferences": ["京剧", "邓丽君"],
            "dialect": "unknown"
        }
        empty_emotions = []

        results = []

        # Case 1: 未到 15 秒，不应触发
        write_json(rag.memory_file, [])
        write_json(rag.profile_file, base_profile)
        write_json(rag.emotion_file, empty_emotions)
        write_json(rag.agent_status_file, rag._build_default_agent_status())
        rag.update_agent_status(user_interaction_time=ts(-5))
        results.append({
            "name": "未到触发时间",
            "result": await proactive.check_and_generate()
        })

        # Case 2: 焦虑场景，15 秒后应触发心理关怀
        write_json(rag.memory_file, [
            {"timestamp": ts(-40), "role": "user", "content": "我这两天总是心里发慌，晚上也睡不好。"},
            {"timestamp": ts(-40), "role": "assistant", "content": "我陪您缓一缓。"}
        ])
        write_json(rag.profile_file, base_profile)
        write_json(rag.emotion_file, [{"timestamp": ts(-50), "emotion": "sad", "risk_level": "medium"}])
        write_json(rag.agent_status_file, rag._build_default_agent_status())
        rag.update_agent_status(user_interaction_time=ts(-20))
        anxiety_result = await proactive.check_and_generate()
        results.append({
            "name": "焦虑主动关怀",
            "result": anxiety_result
        })

        # Case 3: 刚主动过，不应重复触发
        repeat_result = await proactive.check_and_generate()
        results.append({
            "name": "防重复触发",
            "result": repeat_result
        })

        # Case 4: 想家/照片场景
        write_json(rag.memory_file, [
            {"timestamp": ts(-60), "role": "user", "content": "今天一个人在家，有点想孙女，也想看看照片。"},
            {"timestamp": ts(-60), "role": "assistant", "content": "我陪您说说话。"}
        ])
        write_json(rag.profile_file, base_profile)
        write_json(rag.emotion_file, empty_emotions)
        write_json(rag.agent_status_file, rag._build_default_agent_status())
        rag.update_agent_status(user_interaction_time=ts(-20))
        family_result = await proactive.check_and_generate()
        results.append({
            "name": "家庭照片关怀",
            "result": family_result
        })

        # Case 5: 健康关怀场景
        medical_profile = dict(base_profile)
        medical_profile["health_condition"] = ["腿疼", "头晕"]
        write_json(rag.memory_file, [
            {"timestamp": ts(-60), "role": "user", "content": "刚刚腿还是有点疼。"},
            {"timestamp": ts(-60), "role": "assistant", "content": "您先坐稳。"}
        ])
        write_json(rag.profile_file, medical_profile)
        write_json(rag.emotion_file, empty_emotions)
        write_json(rag.agent_status_file, rag._build_default_agent_status())
        rag.update_agent_status(user_interaction_time=ts(-20))
        medical_result = await proactive.check_and_generate()
        results.append({
            "name": "健康主动关怀",
            "result": medical_result
        })

        # Case 6: 兴趣关怀场景
        write_json(rag.memory_file, [
            {"timestamp": ts(-60), "role": "user", "content": "我最近又想听戏了，尤其是《锁麟囊》。"},
            {"timestamp": ts(-60), "role": "assistant", "content": "这戏真好听。"}
        ])
        write_json(rag.profile_file, base_profile)
        write_json(rag.emotion_file, empty_emotions)
        write_json(rag.agent_status_file, rag._build_default_agent_status())
        rag.update_agent_status(user_interaction_time=ts(-20))
        interest_result = await proactive.check_and_generate()
        results.append({
            "name": "兴趣主动关怀",
            "result": interest_result
        })

        print(json.dumps(results, ensure_ascii=False, indent=2))

    finally:
        for path, content in backup.items():
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)


if __name__ == "__main__":
    asyncio.run(main())
