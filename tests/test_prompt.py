import asyncio
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.emotional_agent import EmotionalConnectionAgent

async def test_emotional_prompt():
    print("=== Testing Improved Emotional Agent Prompt ===\n")
    agent = EmotionalConnectionAgent()
    
    test_cases = [
        "哎，最近腿脚不好，哪里也去不了，感觉自己是个废人了。",
        "今天孙子打电话来了，说工作忙，过年又不回来了。",
        "我想看看老伴儿的照片，心里怪想他的。"
    ]
    
    for input_text in test_cases:
        print(f"\nUser: {input_text}")
        print("-" * 40)
        
        response_text = ""
        action_data = {}
        tokens = []
        
        async for event in agent.astream_run(input_text):
            # print(f"Event: {event['event']}") # Debug
            
            if event["event"] == "on_chat_model_end":
                output = event["data"]["output"]
                
                # Check for tool calls
                tool_calls = getattr(output, "tool_calls", [])
                if not tool_calls and isinstance(output, dict):
                     tool_calls = output.get("tool_calls", [])
                
                if tool_calls:
                    for tc in tool_calls:
                        if tc["name"] == "EmotionalStateUpdate":
                            action_data = tc["args"]
                
                # Check for content
                content = getattr(output, "content", "")
                if not content and isinstance(output, dict):
                    content = output.get("content", "")
                
                if content:
                    response_text = content
            
            elif event["event"] == "on_chat_model_stream":
                 chunk = event["data"]["chunk"]
                 if hasattr(chunk, "content") and chunk.content:
                     tokens.append(chunk.content)
        
        if not response_text and tokens:
            response_text = "".join(tokens)
            
        print(f"Agent: {response_text}")

        print(f"Agent: {response_text}")
        print(f"State Update: Expression={action_data.get('expression')}, Action={action_data.get('action')}")
        
        # Validation
        if "(" in response_text or "（" in response_text:
            print("⚠️ Warning: Parentheses detected in output!")
        if "乖" in response_text:
            print("⚠️ Warning: Childish language detected!")

if __name__ == "__main__":
    asyncio.run(test_emotional_prompt())
