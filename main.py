import sys
import asyncio
from src.config import Config
from src.orchestrator import SystemOrchestrator

async def main():
    print("=== 空巢老人陪伴系统 - 情感智能体测试 ===")
    
    # 检查配置
    if not Config.validate():
        print("\n[错误] 未检测到有效的 API Key。")
        sys.exit(1)

    try:
        orchestrator = SystemOrchestrator()
        
        print("\n=== 开始对话 (输入 'quit' 退出) ===")
        print("模拟场景：您可以直接输入文字，也可以模拟语音/视觉输入（代码中硬编码演示）。")
        
        while True:
            user_input = input("\n老人说: ")
            if user_input.lower() in ['quit', 'exit', '退出']:
                break
            
            # 调试指令：导出当前 Prompt
            if user_input.startswith("!prompt"):
                try:
                    debug_input = user_input[7:].strip()
                    # 访问 orchestrator 内部的 emotional_agent
                    if hasattr(orchestrator, 'emotional_agent'):
                        full_prompt = orchestrator.emotional_agent.get_current_prompt(debug_input)
                        print("\n" + "="*20 + " PROMPT DEBUG " + "="*20)
                        print(full_prompt)
                        print("="*54 + "\n")
                    else:
                        print("[Debug] SystemOrchestrator 未公开 emotional_agent 实例")
                except Exception as e:
                    print(f"[Debug Error] {e}")
                continue

            # 模拟上下文数据
            # 实际项目中，这里的数据来自前端传感器
            # 测试脚本：改为不再硬编码视觉情感，而是依赖 orchestrator 内部去抓取 8083 API
            context = {
                "audio_transcript": user_input # 模拟语音转文字与文本一致
            }
            
            print("【系统】正在处理...", end="\r")
            
            # 调用 orchestrator 处理输入流
            async for event_json in orchestrator.process_input_stream(user_input, context):
                # 这里简单打印 token，实际是 SSE JSON 格式
                import json
                event = json.loads(event_json)
                event_type = event["type"]
                data = event["data"]
                
                if event_type == "token":
                    print(data, end="", flush=True)
                elif event_type == "log":
                    # print(f"\n[LOG] {data}") # 可选：打印日志
                    pass
                elif event_type == "error":
                    print(f"\n[ERROR] {data}")
            
            print() # 换行

    except Exception as e:
        print(f"\n[运行出错]: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
