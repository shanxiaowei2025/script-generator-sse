import asyncio
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import status

from app.models.schema import ExtractScenePromptsRequest
from app.utils.storage import load_generation_state
from app.utils.text_utils import extract_scene_prompts as extract_prompts, format_scene_prompts
from app.services.task_queue import format_sse_event


async def extract_scene_prompts_service(request: ExtractScenePromptsRequest) -> StreamingResponse:
    """流式提取剧本中的画面描述词服务"""
    # 从请求体中获取task_id
    task_id = request.task_id
    
    # 检查存储中是否有对应剧本
    state = load_generation_state(task_id)
    if not state:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"未找到任务ID {task_id} 的剧本"}
        )
    
    async def event_generator():
        try:
            # 发送开始事件
            yield format_sse_event("status", {"message": "正在提取画面描述词..."})
            
            # 提取画面描述词
            script_text = state.get("full_script", "")
            try:
                prompts_dict = extract_prompts(script_text)
                
                # 打印详细提取信息
                print(f"提取到的画面描述词详情:")
                for episode, scenes in prompts_dict.items():
                    scene_count = len(scenes)
                    prompt_count = sum(len([p for p in prompts if p.replace('#', '').strip()]) for _, prompts in scenes.items())
                    print(f"  第{episode}集: {scene_count}个场景, {prompt_count}个提示词")
                    # 添加更详细的场次信息
                    for scene, prompts in scenes.items():
                        print(f"    场次{scene}: {len(prompts)}个提示词")
                        for i, prompt in enumerate(prompts):
                            clean_prompt = prompt.replace('#', '').strip()
                            print(f"      [{i}] {clean_prompt[:50]}..." if len(clean_prompt) > 50 else f"      [{i}] {clean_prompt}")
            except Exception as e:
                print(f"提取画面描述词时出错: {str(e)}")
                import traceback
                print(traceback.format_exc())
                yield format_sse_event("error", {"message": f"提取画面描述词时出错: {str(e)}"})
                return
            
            # 格式化结果
            formatted_prompts = format_scene_prompts(prompts_dict, request.episode)
            
            # 如果指定了特定集数，只返回该集的内容
            if request.episode:
                result = {str(request.episode): formatted_prompts.get(str(request.episode), f"未找到第{request.episode}集的画面描述词")}
            else:
                result = formatted_prompts
            
            # 对于每个集数，分别发送事件
            for episode, content in result.items():
                yield format_sse_event("episode_prompts", {
                    "episode": episode,
                    "content": content
                })
                # 小延迟，帮助客户端处理
                await asyncio.sleep(0.1)
            
            # 发送完成事件
            yield format_sse_event("complete", {})
            
        except Exception as e:
            # 发送错误事件
            yield format_sse_event("error", {"message": f"提取画面描述词出错: {str(e)}"})
    
    # 返回流式响应
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用Nginx缓冲
        }
    ) 