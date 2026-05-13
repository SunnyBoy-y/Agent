import asyncio
import json
import os
import sys


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestrator import SystemOrchestrator
from src.tools.professional_skills import ProfessionalSkills


TEST_CASES = [
    {
        "name": "播放音乐请求",
        "input": "给我放一首邓丽君的歌听听。",
        "expect_music": True,
    },
    {
        "name": "普通兴趣聊天",
        "input": "我最近又想听戏了，尤其是《锁麟囊》。",
        "expect_music": False,
    },
]


async def run_case(orchestrator: SystemOrchestrator, case: dict) -> dict:
    tokens = []
    events = {"music": None, "action": None}

    async for event_json in orchestrator.process_input_stream(case["input"], {}):
        event = json.loads(event_json)
        event_type = event["type"]
        data = event["data"]

        if event_type == "token":
            tokens.append(data)
        elif event_type in events:
            events[event_type] = data

    return {
        "name": case["name"],
        "input": case["input"],
        "route": orchestrator.last_system_state.get("last_route"),
        "response": " ".join("".join(tokens).split()),
        "music": events["music"],
        "action": events["action"],
        "expect_music": case["expect_music"],
    }


async def main():
    tool_result = ProfessionalSkills.play_music.invoke({
        "query": "给我放一首邓丽君的歌听听。"
    })
    if isinstance(tool_result, str):
        tool_result = json.loads(tool_result)

    orchestrator = SystemOrchestrator()
    results = []
    for case in TEST_CASES:
        results.append(await run_case(orchestrator, case))

    print(json.dumps({
        "tool_result": tool_result,
        "cases": results
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
