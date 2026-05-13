import asyncio
import json
import os
import sys


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestrator import SystemOrchestrator
from src.tools.professional_skills import ProfessionalSkills


TEST_CASES = [
    {
        "name": "摔倒求救",
        "input": "救命，我摔倒了，腿动不了，快来帮帮我。",
        "expect_sos": True,
    },
    {
        "name": "胸闷呼吸困难",
        "input": "我胸口疼得厉害，喘不上气了，快救命。",
        "expect_sos": True,
    },
    {
        "name": "普通不适",
        "input": "我今天有点腿疼，想先休息一下。",
        "expect_sos": False,
    },
]


async def run_case(orchestrator: SystemOrchestrator, case: dict) -> dict:
    tokens = []
    events = {"sos": None, "action": None, "risk": None}

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
        "sos": events["sos"],
        "action": events["action"],
        "risk": events["risk"],
        "expect_sos": case["expect_sos"],
    }


async def main():
    tool_result = ProfessionalSkills.emergency_contact.invoke({
        "reason": "老人表示救命，我摔倒了",
        "level": "high"
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
