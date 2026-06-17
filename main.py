import asyncio
import json
import sys
from typing import Any

from src.config import Config
from src.orchestrator import SystemOrchestrator


def _compact(data: Any) -> str:
    if isinstance(data, (dict, list)):
        return json.dumps(data, ensure_ascii=False)
    return str(data)


async def stream_chat(orchestrator: SystemOrchestrator, user_input: str, *, verbose: bool = False) -> None:
    context = {
        "audio_transcript": user_input,
    }
    assistant_started = False

    async for event_json in orchestrator.process_input_stream(user_input, context):
        try:
            event = json.loads(event_json)
        except json.JSONDecodeError:
            if assistant_started:
                print()
                assistant_started = False
            print(f"[raw] {event_json}")
            continue

        event_type = event.get("type")
        data = event.get("data")

        if event_type == "token":
            print(data or "", end="", flush=True)
            assistant_started = True
            continue

        if event_type == "done":
            break

        if assistant_started:
            print()
            assistant_started = False

        if event_type == "step":
            name = data.get("name") if isinstance(data, dict) else data
            status = data.get("status") if isinstance(data, dict) else ""
            output = data.get("output") if isinstance(data, dict) else None
            suffix = f" -> {output}" if output else ""
            print(f"[step] {name} {status}{suffix}")
        elif event_type == "error":
            print(f"[error] {_compact(data)}")
        elif event_type == "risk":
            print(f"[risk] {_compact(data)}")
        elif event_type == "sos":
            print(f"[sos] {_compact(data)}")
        elif event_type == "expression":
            print(f"[expression] {_compact(data)}")
        elif event_type == "action":
            print(f"[action] {_compact(data)}")
        elif event_type in {"music", "music_payload"}:
            print(f"[{event_type}] {_compact(data)}")
        elif event_type == "log" and verbose:
            print(f"[log] {_compact(data)}")
        elif verbose:
            print(f"[{event_type}] {_compact(data)}")

    if assistant_started:
        print()


async def main() -> None:
    print("=== Elderly Companion Agent CLI (streaming) ===")

    if not Config.validate():
        print("\n[error] Config validation failed. Check API key and required settings.")
        sys.exit(1)

    orchestrator = SystemOrchestrator()
    verbose = False

    print("\nCommands: quit/exit, !verbose, !prompt <text>")
    while True:
        try:
            user_input = input("\nUser> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit"}:
            break
        if user_input == "!verbose":
            verbose = not verbose
            print(f"[debug] verbose={verbose}")
            continue
        if user_input.startswith("!prompt"):
            debug_input = user_input[len("!prompt"):].strip()
            agent = getattr(orchestrator, "emotional_agent", None)
            prompt_fn = getattr(agent, "get_current_prompt", None)
            if callable(prompt_fn):
                print(prompt_fn(debug_input))
            else:
                print("[debug] emotional_agent.get_current_prompt is unavailable")
            continue

        print("AI> ", end="", flush=True)
        await stream_chat(orchestrator, user_input, verbose=verbose)


if __name__ == "__main__":
    asyncio.run(main())
