import asyncio
import json
import os
import sys


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestrator import SystemOrchestrator


TEST_CASES = [
    {
        "name": "日常记录",
        "input": "我今天上午去公园散步了，还买了点菜。",
        "context": {},
    },
    {
        "name": "日常查询",
        "input": "我前两天去干什么了？",
        "context": {},
    },
    {
        "name": "情感陪伴",
        "input": "今天一个人在家，心里空落落的，挺想老伴。",
        "context": {},
    },
    {
        "name": "兴趣聊天",
        "input": "我最近又想听戏了，尤其是《锁麟囊》。",
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
        "response": " ".join("".join(tokens).split()),
        "action": action,
        "risk": risk,
        "photos": photos,
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
