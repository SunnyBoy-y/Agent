import asyncio
import json
import os
import sys


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestrator import SystemOrchestrator


TEST_CASES = [
    {
        "name": "心理焦虑",
        "input": "我这两天总是心里发慌，晚上也睡不好，越想越烦。",
        "context": {},
    },
    {
        "name": "医疗不适",
        "input": "我今天有点头晕，腿也疼。",
        "context": {},
    },
    {
        "name": "反诈风险",
        "input": "刚才有人打电话说我中奖了，让我先转两千块手续费，这是真的吗？",
        "context": {},
    },
    {
        "name": "查看照片",
        "input": "我想看看上次和孙女在公园拍的照片。",
        "context": {},
    },
    {
        "name": "播放音乐",
        "input": "给我放一首邓丽君的歌听听。",
        "context": {},
    },
]


async def run_case(orchestrator: SystemOrchestrator, case: dict) -> dict:
    tokens = []
    photos = None
    risk = None
    action = None
    logs = []

    async for event_json in orchestrator.process_input_stream(case["input"], case.get("context", {})):
        event = json.loads(event_json)
        event_type = event["type"]
        data = event["data"]

        if event_type == "token":
            tokens.append(data)
        elif event_type == "photos":
            photos = data
        elif event_type == "risk":
            risk = data
        elif event_type == "action":
            action = data
        elif event_type == "log":
            logs.append(data)

    return {
        "name": case["name"],
        "input": case["input"],
        "route": orchestrator.last_system_state.get("last_route"),
        "tool_calls": orchestrator.last_system_state.get("tool_calls", []),
        "response": "".join(tokens).strip(),
        "photos": photos,
        "risk": risk,
        "action": action,
        "logs": logs[-3:],
    }


async def main():
    orchestrator = SystemOrchestrator()
    results = []

    for case in TEST_CASES:
        result = await run_case(orchestrator, case)
        results.append(result)

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
