from fastapi import APIRouter, Request, BackgroundTasks, status
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import asyncio
import json
import uuid
from typing import Optional, Callable, Awaitable, Any, Dict, List

from app.core.generator import generate_character_and_directory
from app.core.generator_part2 import generate_episode
from app.core.generator_part3 import resume_episode_generation
from app.utils.storage import (
    save_generation_state, 
    load_generation_state,
    find_latest_state_for_any_client,
    save_partial_content,
    get_partial_content
)
from app.utils.text_utils import extract_scene_prompts as extract_prompts, format_scene_prompts
from app.core.config import API_KEY, API_URL

# 定义请求模型
class StreamScriptGenerationRequest(BaseModel):
    genre: str
    duration: str
    episodes: int
    characters: List[str]
    api_key: Optional[str] = None
    api_url: Optional[str] = None

class ExtractScenePromptsRequest(BaseModel):
    task_id: str
    episode: Optional[int] = None

# 创建路由器
router = APIRouter(tags=["Streaming API"])

# 流式生成状态跟踪
active_streaming_tasks = {}

@router.post("/stream/generate-script")
async def stream_generate_script(request: StreamScriptGenerationRequest, background_tasks: BackgroundTasks):
    """
    流式生成脚本API端点
    返回 Server-Sent Events (SSE) 格式的流式响应
    """
    # 生成唯一的任务ID
    task_id = str(uuid.uuid4())
    
    # 注册任务
    active_streaming_tasks[task_id] = {
        "is_active": True,
        "last_event": None
    }
    
    # 定义生成器函数
    async def event_generator():
        try:
            # 首先发送任务ID
            yield format_sse_event("task_id", {"task_id": task_id})
            
            # 生成初始内容并发送
            yield format_sse_event("status", {"message": "正在生成角色表和目录..."})
            
            initial_content = await generate_character_and_directory(
                request.genre,
                request.episodes,
                request.duration,
                request.characters,
                request.api_key or API_KEY,
                request.api_url or API_URL
            )
            
            # 发送生成的初始内容
            yield format_sse_event("initial_content", {"content": initial_content})
            
            # 保存初始状态
            save_generation_state(task_id, 0, initial_content)
            current_episode = 1
            
            # 开始生成每一集
            while current_episode <= request.episodes:
                # 检查任务是否还在活跃
                if not active_streaming_tasks.get(task_id, {}).get("is_active", False):
                    print(f"任务 {task_id} 已取消，停止生成但保存当前状态")
                    # 当前集数减1，因为这集未完成
                    if current_episode > 1:
                        save_generation_state(task_id, current_episode - 1, initial_content)
                    break
                
                # 加载当前状态
                state = load_generation_state(task_id)
                if state:
                    # 使用已有状态继续生成
                    full_script = state.get("full_script", initial_content)
                    # 更新initial_content以便后续使用
                    initial_content = full_script
                
                # 发送当前进度
                yield format_sse_event("progress", {
                    "current": current_episode,
                    "total": request.episodes
                })
                
                yield format_sse_event("status", {
                    "message": f"正在生成第{current_episode}集..."
                })
                
                # 检查是否有部分生成的内容
                partial_content = get_partial_content(task_id, current_episode)
                
                # 创建内容接收回调函数
                async def content_callback(chunk):
                    """接收内容块并通过SSE发送到客户端"""
                    # 将chunk作为事件发送
                    active_streaming_tasks[task_id]["last_event"] = format_sse_event(
                        "episode_content_chunk", 
                        {
                            "episode": current_episode,
                            "content": chunk,
                            "is_complete": False
                        }
                    )
                    return True  # 总是返回成功，因为我们不能实时检测HTTP连接状态
                
                episode_content = ""
                try:
                    if partial_content:
                        # 继续生成，传入回调函数进行实时更新
                        episode_content = await resume_episode_generation(
                            current_episode,
                            request.genre,
                            request.episodes,
                            request.duration,
                            initial_content,
                            partial_content,
                            request.api_key or API_KEY,
                            request.api_url or API_URL,
                            task_id,
                            content_callback=content_callback
                        )
                    else:
                        # 从头生成，传入回调函数进行实时更新
                        episode_content = await generate_episode(
                            current_episode,
                            request.genre,
                            request.episodes,
                            request.duration,
                            initial_content,
                            request.api_key or API_KEY,
                            request.api_url or API_URL,
                            task_id,
                            content_callback=content_callback
                        )
                    
                    # 如果有缓存的事件，则先发送它
                    if active_streaming_tasks[task_id]["last_event"]:
                        yield active_streaming_tasks[task_id]["last_event"]
                        active_streaming_tasks[task_id]["last_event"] = None
                        
                    # 发送完整的生成结果
                    yield format_sse_event("episode_content", {
                        "episode": current_episode,
                        "content": episode_content,
                        "is_complete": True
                    })
                    
                    # 更新完整脚本
                    if initial_content:
                        initial_content += "\n\n" + episode_content
                    else:
                        initial_content = episode_content
                    
                    # 保存当前状态
                    save_generation_state(task_id, current_episode, initial_content)
                    save_partial_content(task_id, current_episode, episode_content)
                    
                    # 进入下一集
                    current_episode += 1
                
                except Exception as e:
                    print(f"生成第{current_episode}集时出错: {str(e)}")
                    # 发送错误消息
                    yield format_sse_event("error", {
                        "message": f"生成第{current_episode}集时出错: {str(e)}"
                    })
                    
                    # 保存当前状态
                    save_generation_state(task_id, current_episode - 1, initial_content)
                    break
            
            # 生成完成
            if current_episode > request.episodes:
                yield format_sse_event("complete", {})
        
        finally:
            # 清理任务状态
            if task_id in active_streaming_tasks:
                active_streaming_tasks[task_id]["is_active"] = False
    
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

@router.delete("/stream/cancel/{task_id}")
async def cancel_streaming(task_id: str):
    """取消流式生成"""
    if task_id in active_streaming_tasks:
        active_streaming_tasks[task_id]["is_active"] = False
        return {"status": "canceled", "task_id": task_id}
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"任务 {task_id} 不在活跃流式生成中"}
    )

@router.post("/stream/extract-scene-prompts/{task_id}")
async def stream_extract_scene_prompts(task_id: str, request: Optional[ExtractScenePromptsRequest] = None):
    """
    流式提取剧本中的画面描述词并返回
    使用task_id标识剧本
    """
    # 如果请求体为空，使用路径参数创建请求对象
    if request is None:
        request = ExtractScenePromptsRequest(task_id=task_id)
    
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
            prompts_dict = extract_prompts(script_text)
            
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

def format_sse_event(event_type: str, data: Any) -> str:
    """格式化服务器发送事件（SSE）"""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n" 