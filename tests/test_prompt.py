import asyncio

from src.agents.emotional_agent import EmotionalConnectionAgent


class FakeChunk:
    def __init__(self, content):
        self.content = content


class FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


async def _collect_prompt_style_result(agent):
    response_text = ""
    action_data = {}
    tokens = []

    async for event in agent.astream_run("我想看看老伴儿的照片。"):
        if event["event"] == "on_chat_model_end":
            output = event["data"]["output"]
            for tool_call in getattr(output, "tool_calls", []):
                if tool_call["name"] == "EmotionalStateUpdate":
                    action_data = tool_call["args"]
            if getattr(output, "content", ""):
                response_text = output.content
        elif event["event"] == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            if getattr(chunk, "content", ""):
                tokens.append(chunk.content)

    if not response_text and tokens:
        response_text = "".join(tokens)
    return response_text, action_data


def test_emotional_prompt_style_stream_result_is_collectable():
    agent = object.__new__(EmotionalConnectionAgent)

    async def fake_astream_run(_input_text):
        yield {"event": "on_chat_model_stream", "data": {"chunk": FakeChunk("我在这儿陪着您。")}}
        yield {
            "event": "on_chat_model_end",
            "data": {
                "output": FakeMessage(
                    content="我在这儿陪着您。",
                    tool_calls=[
                        {
                            "name": "EmotionalStateUpdate",
                            "args": {"expression": "concerned", "action": "comfort"},
                        }
                    ],
                )
            },
        }

    agent.astream_run = fake_astream_run

    response_text, action_data = asyncio.run(_collect_prompt_style_result(agent))

    assert response_text == "我在这儿陪着您。"
    assert action_data == {"expression": "concerned", "action": "comfort"}
