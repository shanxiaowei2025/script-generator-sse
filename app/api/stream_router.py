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
from app.utils.runninghub_api import process_scene_prompts, query_task_status, query_task_result

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

class RunningHubProcessRequest(BaseModel):
    task_id: str
    episode: Optional[int] = None

class RunningHubTaskStatusRequest(BaseModel):
    task_id: str

class RunningHubTaskResultRequest(BaseModel):
    task_id: str

# 创建路由器
router = APIRouter(tags=["Streaming API"])

# 流式生成状态跟踪
active_streaming_tasks = {}

@router.post("/stream/generate-script")
async def stream_generate_script(request: StreamScriptGenerationRequest):
    """
    流式生成脚本API端点
    返回 Server-Sent Events (SSE) 格式的流式响应
    """
    task_id = str(uuid.uuid4())
    queue = asyncio.Queue()
    
    async def event_generator():
        # 在函数内部定义变量
        initial_content = ""
        characters_directory_completed = False
        episode_generation_started = False
        current_episode = 1
        
        try:
            # 发送初始事件
            yield format_sse_event("task_id", {"task_id": task_id})
            yield format_sse_event("status", {"message": "正在生成角色表和目录..."})
            
            # 定义独立的异步回调函数
            async def initial_callback(chunk):
                print(f"收到角色表内容块: {len(chunk)}字符")
                await queue.put({"type": "initial_content_chunk", "content": chunk})
                return True
                
            async def episode_callback(chunk):
                print(f"收到剧集内容块: {len(chunk)}字符")
                await queue.put({"type": "episode_content_chunk", "content": chunk})
                return True
            
            # 创建任务
            print("开始生成角色表和目录...")
            initial_content_task = asyncio.create_task(
                generate_character_and_directory(
                    request.genre,
                    request.episodes,
                    request.duration,
                    request.characters,
                    request.api_key or API_KEY,
                    request.api_url or API_URL,
                    content_callback=initial_callback
                )
            )
            
            # 处理队列中的事件
            while True:
                try:
                    # 等待队列项
                    item = await asyncio.wait_for(queue.get(), timeout=5.0)  # 增加超时时间
                    event_type = item["type"]
                    content = item["content"]
                    
                    # 处理不同类型的内容
                    if event_type == "initial_content_chunk":
                        # 记录内容
                        initial_content += content
                        # 发送内容块
                        yield format_sse_event("content_chunk", {
                            "content": content,
                            "is_complete": False
                        })
                    elif event_type == "episode_content_chunk":
                        # 发送内容块
                        yield format_sse_event("content_chunk", {
                            "content": content,
                            "is_complete": False
                        })
                    
                    queue.task_done()
                
                except asyncio.TimeoutError:
                    print("队列超时，检查任务状态...")
                    
                    # 检查角色表和目录生成任务
                    if not characters_directory_completed and initial_content_task.done():
                        try:
                            result = initial_content_task.result()
                            print(f"角色表和目录生成完成，长度: {len(result) if result else 0}字符")
                            
                            if result:
                                initial_content = result
                                save_generation_state(task_id, 0, initial_content)
                                
                                # 标记为已完成角色表和目录
                                characters_directory_completed = True
                                
                                # 发送状态更新
                                yield format_sse_event("status", {"message": f"正在生成第{current_episode}集..."})
                                yield format_sse_event("progress", {
                                    "current": current_episode,
                                    "total": request.episodes
                                })
                                
                                # 开始生成第一集
                                print(f"开始生成第{current_episode}集...")
                                episode_task = asyncio.create_task(
                                    generate_episode(
                                        current_episode,
                                        request.genre,
                                        request.episodes,
                                        request.duration,
                                        initial_content,
                                        request.api_key or API_KEY,
                                        request.api_url or API_URL,
                                        task_id,
                                        content_callback=episode_callback
                                    )
                                )
                                episode_generation_started = True
                            else:
                                print("角色表和目录生成结果为空")
                                yield format_sse_event("error", {"message": "角色表和目录生成失败"})
                                break
                        except Exception as e:
                            print(f"处理角色表和目录结果时出错: {str(e)}")
                            yield format_sse_event("error", {"message": f"生成内容出错: {str(e)}"})
                            break
                    
                    # 如果剧本生成已开始，检查是否完成
                    elif episode_generation_started and 'episode_task' in locals():
                        if episode_task.done():
                            try:
                                episode_content = episode_task.result()
                                print(f"第{current_episode}集生成完成，长度: {len(episode_content) if episode_content else 0}字符")
                                
                                # 检查是否有有效内容
                                if episode_content and len(episode_content) > 20:  # 至少要有一些实质内容
                                    # 保存生成的剧本
                                    initial_content += "\n\n" + episode_content
                                    save_generation_state(task_id, current_episode, initial_content)
                                    
                                    # 增加集数
                                    current_episode += 1
                                    
                                    # 检查是否需要生成下一集
                                    if current_episode <= request.episodes:
                                        # 发送状态更新
                                        yield format_sse_event("status", {"message": f"正在生成第{current_episode}集..."})
                                        yield format_sse_event("progress", {
                                            "current": current_episode,
                                            "total": request.episodes
                                        })
                                        
                                        # 开始生成下一集
                                        print(f"开始生成第{current_episode}集...")
                                        episode_task = asyncio.create_task(
                                            generate_episode(
                                                current_episode,
                                                request.genre,
                                                request.episodes,
                                                request.duration,
                                                initial_content,
                                                request.api_key or API_KEY,
                                                request.api_url or API_URL,
                                                task_id,
                                                content_callback=episode_callback
                                            )
                                        )
                                    else:
                                        # 所有剧集都已生成完成
                                        yield format_sse_event("complete", {})
                                        break
                                else:
                                    print("生成的剧本内容为空或太短")
                                    yield format_sse_event("error", {"message": "生成的剧本内容为空或太短"})
                                    break
                            except Exception as e:
                                print(f"处理剧本生成结果时出错: {str(e)}")
                                yield format_sse_event("error", {"message": f"生成内容出错: {str(e)}"})
                                break
                        else:
                            # 检查任务是否运行太久
                            # 这里可以添加超时检查逻辑，例如使用asyncio.Task.get_coro()来获取任务创建时间
                            pass
                
                except Exception as e:
                    print(f"事件处理异常: {str(e)}")
                    yield format_sse_event("error", {"message": str(e)})
                    break
        
        except Exception as e:
            print(f"事件生成器主异常: {str(e)}")
            yield format_sse_event("error", {"message": str(e)})
        
        finally:
            # 清理任务
            if 'initial_content_task' in locals() and not initial_content_task.done():
                initial_content_task.cancel()
            if 'episode_task' in locals() and not episode_task.done():
                episode_task.cancel()
    
    # 返回流式响应
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
            "Keep-Alive": "timeout=600"  # 10分钟超时
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

@router.post("/stream/process-prompts-with-runninghub/{task_id}")
async def process_prompts_with_runninghub(task_id: str, request: Optional[RunningHubProcessRequest] = None):
    """
    将剧本中提取的画面描述词发送到RunningHub API处理
    使用task_id标识剧本，采用队列方式处理提示词
    """
    # 如果请求体为空，使用路径参数创建请求对象
    if request is None:
        request = RunningHubProcessRequest(task_id=task_id)
    
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
            yield format_sse_event("status", {"message": "正在提取画面描述词并发送到RunningHub..."})
            
            # 提取画面描述词
            script_text = state.get("full_script", "")
            prompts_dict = extract_prompts(script_text)
            
            # 如果指定了特定集数，只处理该集的内容
            if request.episode is not None:
                specific_episode = request.episode
                if specific_episode in prompts_dict:
                    single_episode_dict = {specific_episode: prompts_dict[specific_episode]}
                    prompts_dict = single_episode_dict
                else:
                    yield format_sse_event("error", {"message": f"未找到第{specific_episode}集的画面描述词"})
                    return
            
            # 计算提示词总数
            total_prompts = 0
            for episode, scenes in prompts_dict.items():
                for scene, prompts in scenes.items():
                    total_prompts += len([p for p in prompts if p.replace('#', '').strip()])
            
            # 发送状态更新
            yield format_sse_event("status", {
                "message": f"检测到{total_prompts}个提示词，将使用队列方式处理(最大并发3个)...",
                "total_prompts": total_prompts
            })
            
            # 创建状态更新回调
            async def status_callback(status_info):
                """接收状态更新并通过SSE发送到客户端"""
                message = status_info.get("message", "处理中...")
                yield_event = format_sse_event("queue_status", {
                    "message": message,
                    "active_tasks": status_info.get("active_tasks", 0),
                    "completed": status_info.get("completed", 0),
                    "total": status_info.get("total", 0),
                    "queue_size": status_info.get("queue_size", 0)
                })
                return yield_event
            
            # 发送状态更新
            yield format_sse_event("status", {"message": "开始处理提示词队列，每个任务等待时间增加至10秒..."})
            
            # 处理开始时间
            start_time = asyncio.get_event_loop().time()
            
            # 定义状态收集器
            status_events = []
            
            # 状态回调包装器
            async def collect_status_events(status_info):
                event = await status_callback(status_info)
                status_events.append(event)
            
            # 调用处理函数
            results = await process_scene_prompts(prompts_dict, status_callback=collect_status_events)
            
            # 发送收集的状态事件（每5个事件发送一次，避免事件过多）
            for i in range(0, len(status_events), 5):
                # 只发送这个批次的最后一个事件
                yield status_events[min(i + 4, len(status_events) - 1)]
                await asyncio.sleep(0.05)  # 小延迟，避免客户端过载
            
            # 处理结束时间
            end_time = asyncio.get_event_loop().time()
            processing_time = end_time - start_time
            
            # 发送处理完成信息
            yield format_sse_event("status", {
                "message": f"所有提示词处理完成！共耗时{processing_time:.2f}秒"
            })
            
            # 对于每个集数，分别发送事件
            for episode, episode_results in results.items():
                yield format_sse_event("runninghub_results", {
                    "episode": episode,
                    "results": episode_results
                })
                # 小延迟，帮助客户端处理
                await asyncio.sleep(0.1)
            
            # 发送完成事件
            yield format_sse_event("complete", {})
            
        except Exception as e:
            # 发送错误事件
            import traceback
            error_msg = f"处理画面描述词出错: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            yield format_sse_event("error", {"message": error_msg})
    
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

@router.post("/runninghub/task-status")
async def get_runninghub_task_status(request: RunningHubTaskStatusRequest):
    """
    查询RunningHub任务状态
    
    Args:
        request: 包含task_id的请求对象
        
    Returns:
        任务状态信息
    """
    if not request.task_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "缺少任务ID"}
        )
    
    try:
        # 调用RunningHub API查询任务状态
        status_result = await query_task_status(request.task_id)
    
        # 否则只返回状态信息
        return {
            "task_id": request.task_id,
            "status": status_result
        }
    
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"查询任务状态出错: {str(e)}"}
        )

@router.post("/runninghub/task-result")
async def get_runninghub_task_result(request: RunningHubTaskResultRequest):
    """
    查询RunningHub任务结果
    
    Args:
        request: 包含task_id的请求对象
        
    Returns:
        任务结果信息
    """
    if not request.task_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "缺少任务ID"}
        )
    
    try:
        # 调用RunningHub API查询任务结果
        result = await query_task_result(request.task_id)
        
        return {
            "task_id": request.task_id,
            "result": result
        }
    
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"查询任务结果出错: {str(e)}"}
        )

def format_sse_event(event_type: str, data: Any) -> str:
    """格式化SSE事件"""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n" 