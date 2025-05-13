from fastapi import APIRouter, Request, BackgroundTasks, status
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
import asyncio
import json
import uuid
import time
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
from app.utils.runninghub_api import MAX_CONCURRENT_TASKS, call_runninghub_workflow, process_scene_prompts, query_task_status, query_task_result, wait_for_task_completion

# =============================================================================
# 全局任务队列系统 - 实现多用户任务等待队列
# =============================================================================

# 全局任务队列
global_task_queue = asyncio.Queue()

# 全局事件字典 - 用于存储不同请求的事件队列 {request_id: event_queue}
global_event_queues = {}

# 全局运行状态
is_global_worker_running = False
global_worker_lock = asyncio.Lock()

# 全局任务状态跟踪
global_tasks_status = {}  # {task_id: status_dict}

# 全局请求元数据，存储每个请求的任务总数和任务ID列表
global_request_metadata = {}  # {request_id: {"total_tasks": n, "task_ids": [...]}}

# 启动全局工作器
async def start_global_worker():
    global is_global_worker_running
    
    async with global_worker_lock:
        if is_global_worker_running:
            print("全局工作器已在运行中")
            return
        
        is_global_worker_running = True
        print("启动全局工作器")
    
    try:
        while True:
            try:
                # 从全局队列获取任务
                task = await global_task_queue.get()
                
                # 提取任务信息
                request_id = task["request_id"]
                event_queue = global_event_queues.get(request_id)
                task_data = task["task_data"]
                subtask_id = task["subtask_id"]  # 获取子任务ID
                
                if not event_queue:
                    print(f"错误: 找不到请求ID {request_id} 的事件队列，跳过任务")
                    global_task_queue.task_done()
                    continue
                
                # 更新任务状态为处理中
                print(f"开始处理任务: {subtask_id} (请求: {request_id})")
                global_tasks_status[subtask_id] = {
                    "status": "PROCESSING",
                    "start_time": time.time(),
                    "request_id": request_id,
                    "task_data": task_data
                }
                
                # 发送状态更新
                await event_queue.put(format_sse_event("status", {
                    "message": f"开始处理任务: 第{task_data['episode']}集 场次{task_data['scene']} 提示词{task_data['prompt_index']}",
                    "task_id": subtask_id,
                    "status": "PROCESSING"
                }))
                
                # 处理任务
                print(f"处理任务: {subtask_id}, 请求ID: {request_id}")
                try:
                    # 调用RunningHub API
                    prompt = task_data["prompt"]
                    create_result = await call_runninghub_workflow(prompt)
                    
                    # 提取runninghub_task_id
                    runninghub_task_id = None
                    if create_result and isinstance(create_result, dict):
                        if "data" in create_result:
                            data = create_result.get("data", {})
                            if isinstance(data, dict):
                                runninghub_task_id = data.get("taskId")
                            elif isinstance(data, str) and data.isdigit():
                                runninghub_task_id = data
                        elif "taskId" in create_result:
                            runninghub_task_id = create_result.get("taskId")
                    
                    # 发送创建结果
                    await event_queue.put(format_sse_event("task_created", {
                        "episode": task_data["episode_key"],
                        "scene": task_data["scene_key"],
                        "prompt_index": str(task_data["prompt_index"]),
                        "task_id": subtask_id,
                        "runninghub_task_id": runninghub_task_id
                    }))
                    
                    # 如果创建成功，等待任务完成
                    result = {
                        "prompt": prompt,
                        "create_result": create_result,
                        "task_id": runninghub_task_id,
                        "status": "FAILED"  # 默认失败，成功时会更新
                    }
                    
                    if runninghub_task_id:
                        # 等待RunningHub任务完成
                        final_status, final_result = await wait_for_task_completion(runninghub_task_id)
                        
                        # 更新结果
                        result["status_result"] = final_status
                        result["final_result"] = final_result
                        result["status"] = "SUCCESS" if final_status in ["SUCCESS", "FINISHED", "COMPLETE", "COMPLETED"] else "FAILED"
                    
                    # 更新全局状态
                    print(f"更新任务 {subtask_id} 状态为 COMPLETED")
                    global_tasks_status[subtask_id] = {
                        "status": "COMPLETED",
                        "end_time": time.time(),
                        "request_id": request_id,
                        "task_data": task_data,
                        "result": result
                    }
                    
                    # 发送完成事件
                    await event_queue.put(format_sse_event("task_completed", {
                        "episode": task_data["episode_key"],
                        "scene": task_data["scene_key"],
                        "prompt_index": str(task_data["prompt_index"]),
                        "task_id": subtask_id,
                        "runninghub_task_id": runninghub_task_id,
                        "status": result["status"],
                        "result": {
                            "episode": task_data["episode_key"],
                            "results": {
                                task_data["scene_key"]: {
                                    str(task_data["prompt_index"]): result
                                }
                            }
                        }
                    }))
                    
                except Exception as e:
                    # 处理错误
                    error_msg = f"处理任务时出错: {str(e)}"
                    print(error_msg)
                    
                    # 更新全局状态
                    print(f"更新任务 {subtask_id} 状态为 ERROR")
                    global_tasks_status[subtask_id] = {
                        "status": "ERROR",
                        "end_time": time.time(),
                        "request_id": request_id,
                        "task_data": task_data,
                        "error": str(e)
                    }
                    
                    # 发送错误事件
                    await event_queue.put(format_sse_event("task_error", {
                        "episode": task_data["episode_key"],
                        "scene": task_data["scene_key"],
                        "prompt_index": str(task_data["prompt_index"]),
                        "task_id": subtask_id,
                        "error": str(e)
                    }))
                
                finally:
                    # 标记任务完成
                    global_task_queue.task_done()
                    
                    # 发送进度更新 - 改为使用请求元数据中的任务ID列表计算进度
                    if request_id in global_request_metadata:
                        request_meta = global_request_metadata[request_id]
                        expected_total = request_meta["total_tasks"]
                        task_ids_list = request_meta["task_ids"]
                        
                        # 使用单独变量保存计数结果
                        completed_count = 0
                        for current_id in task_ids_list:
                            if current_id in global_tasks_status and global_tasks_status[current_id]["status"] in ["COMPLETED", "ERROR"]:
                                completed_count += 1
                        
                        print(f"请求 {request_id} 进度更新: 完成={completed_count}/{expected_total}")
                        
                        # 发送进度更新
                        await event_queue.put(format_sse_event("progress", {
                            "completed": completed_count,
                            "total": expected_total,
                            "percentage": int(completed_count * 100 / expected_total) if expected_total else 0
                        }))
                        
                        # 检查请求的所有任务是否完成
                        if completed_count == expected_total:
                            print(f"请求 {request_id} 的所有 {expected_total} 个任务已完成")
                            await event_queue.put(format_sse_event("all_tasks_completed", {
                                "request_id": request_id,
                                "completed": completed_count,
                                "total": expected_total
                            }))
                    else:
                        # 备用方法：如果没有元数据，使用过滤方法计算
                        request_tasks = [t for t_id, t in global_tasks_status.items() if t["request_id"] == request_id]
                        completed_tasks = [t for t in request_tasks if t["status"] in ["COMPLETED", "ERROR"]]
                        
                        print(f"备用进度方法，请求 {request_id} 完成={len(completed_tasks)}/{len(request_tasks)}")
                        
                        await event_queue.put(format_sse_event("progress", {
                            "completed": len(completed_tasks),
                            "total": len(request_tasks),
                            "percentage": int(len(completed_tasks) * 100 / len(request_tasks)) if request_tasks else 0
                        }))
                        
                        # 检查请求的所有任务是否完成
                        if len(completed_tasks) == len(request_tasks) and len(request_tasks) > 0:
                            print(f"请求 {request_id} 的所有任务已完成 (备用方法)")
                            await event_queue.put(format_sse_event("all_tasks_completed", {
                                "request_id": request_id,
                                "completed": len(completed_tasks),
                                "total": len(request_tasks)
                            }))
            
            except asyncio.CancelledError:
                print("全局工作器被取消")
                break
                
            except Exception as e:
                print(f"全局工作器异常: {str(e)}")
                import traceback
                print(traceback.format_exc())
    
    finally:
        async with global_worker_lock:
            is_global_worker_running = False
            print("全局工作器已停止")

# 启动全局工作器的后台任务
def ensure_global_worker_running():
    # 检查工作器是否运行
    if not is_global_worker_running:
        # 创建后台任务
        asyncio.create_task(start_global_worker())
        print("已启动全局工作器后台任务")
        
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
                                    
                                    # 保存单集内容
                                    save_partial_content(task_id, current_episode, episode_content)
                                    
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

@router.post("/stream/extract-scene-prompts")
async def stream_extract_scene_prompts(request: ExtractScenePromptsRequest):
    """
    流式提取剧本中的画面描述词并返回
    使用task_id标识剧本
    """
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

@router.post("/stream/process-prompts-with-runninghub")
async def process_prompts_with_runninghub(request: RunningHubProcessRequest):
    """
    将剧本中提取的画面描述词发送到RunningHub API处理
    使用task_id标识剧本，采用队列方式处理提示词，实时返回每个任务的结果
    """
    # 从请求体中获取task_id
    script_task_id = request.task_id
    
    # 检查存储中是否有对应剧本
    state = load_generation_state(script_task_id)
    if not state:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"未找到任务ID {script_task_id} 的剧本"}
        )
    
    async def event_generator():
        try:
            # 生成唯一的请求ID
            request_id = str(uuid.uuid4())
            print(f"创建请求: {request_id}, 剧本ID: {script_task_id}")
            
            # 创建事件队列并注册到全局字典
            event_queue = asyncio.Queue()
            global_event_queues[request_id] = event_queue
            
            # 发送开始事件
            yield format_sse_event("status", {"message": "正在提取画面描述词并发送到RunningHub...", "request_id": request_id})
            
            # 提取画面描述词
            script_text = state.get("full_script", "")
            prompts_dict = extract_prompts(script_text)
            
            # 打印详细提取信息
            print(f"提取到的画面描述词详情:")
            for episode, scenes in prompts_dict.items():
                scene_count = len(scenes)
                prompt_count = sum(len([p for p in prompts if p.replace('#', '').strip()]) for _, prompts in scenes.items())
                print(f"  第{episode}集: {scene_count}个场景, {prompt_count}个提示词")
            
            # 如果指定了特定集数，只处理该集的内容
            if request.episode is not None:
                specific_episode = request.episode
                if specific_episode in prompts_dict:
                    single_episode_dict = {specific_episode: prompts_dict[specific_episode]}
                    prompts_dict = single_episode_dict
                    print(f"只处理第{specific_episode}集的画面描述词")
                else:
                    print(f"错误: 未找到第{specific_episode}集的画面描述词")
                    yield format_sse_event("error", {"message": f"未找到第{specific_episode}集的画面描述词"})
                    return
            
            # 跟踪任务总数
            total_tasks = 0
            request_task_ids = []  # 存储此请求的所有任务ID
            
            # 将任务添加到全局队列
            for episode, scenes in prompts_dict.items():
                # 格式化集数键为"第X集"
                episode_key = f"第{episode}集" if not str(episode).startswith("第") else str(episode)
                
                for scene, prompts in scenes.items():
                    # 格式化场景键为"场次X-X"
                    scene_key = f"场次{scene}" if not str(scene).startswith("场次") else str(scene)
                    
                    # 添加有效提示词到队列
                    for idx, prompt in enumerate(prompts):
                        clean_prompt = prompt.replace('#', '').strip()
                        if clean_prompt:
                            total_tasks += 1
                            # 准备任务数据
                            task_data = {
                                "episode": episode,
                                "episode_key": episode_key,
                                "scene": scene,
                                "scene_key": scene_key,
                                "prompt_index": idx,
                                "prompt": clean_prompt
                            }
                            
                            # 生成一个独特的任务ID
                            # 使用确定的格式：请求ID_集数_场景_提示词索引
                            subtask_id = f"{request_id}_{episode}_{scene}_{idx}"
                            request_task_ids.append(subtask_id)
                            
                            # 创建全局任务项
                            task_item = {
                                "request_id": request_id,
                                "task_data": task_data,
                                "added_time": time.time(),
                                "subtask_id": subtask_id  # 任务ID字段
                            }
                            
                            # 添加到全局队列
                            await global_task_queue.put(task_item)
                            
                            # 更新状态跟踪
                            global_tasks_status[subtask_id] = {
                                "status": "QUEUED",
                                "queue_time": time.time(),
                                "request_id": request_id,
                                "task_data": task_data
                            }
            
            # 打印任务详情
            print(f"请求 {request_id} 添加了 {total_tasks} 个任务")
            print(f"任务ID列表: {request_task_ids}")
            
            # 确保全局工作器在运行
            ensure_global_worker_running()
            
            # 发送状态更新
            yield format_sse_event("status", {
                "message": f"已将{total_tasks}个提示词添加到全局队列，等待处理...",
                "total_prompts": total_tasks,
                "request_id": request_id,
                "queue_size": global_task_queue.qsize()
            })

            # 保存请求任务总数到全局状态
            if request_id not in global_event_queues:
                global_event_queues[request_id] = event_queue
            
            # 创建请求元数据存储
            global_request_metadata[request_id] = {
                "total_tasks": total_tasks,
                "task_ids": request_task_ids,
                "created_time": time.time()
            }
            
            # 从事件队列读取并yield事件
            try:
                # 设置是否已发送完成事件的标志
                complete_sent = False
                
                while True:
                    try:
                        # 等待事件，较短的超时确保响应性
                        event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                        yield event
                        event_queue.task_done()
                        
                        # 检查是否是all_tasks_completed事件
                        if "all_tasks_completed" in event:
                            # 所有任务已完成，准备结束循环
                            if not complete_sent:
                                yield format_sse_event("complete", {
                                    "message": "所有任务处理完成",
                                    "request_id": request_id
                                })
                                complete_sent = True
                            
                            # 等待一小段时间确保所有事件都被处理
                            await asyncio.sleep(1)
                            break
                        
                    except asyncio.TimeoutError:
                        # 检查请求的任务进度
                        if request_id in global_request_metadata:
                            # 从元数据中获取请求的总任务数和任务ID列表
                            request_meta = global_request_metadata[request_id]
                            expected_total = request_meta["total_tasks"]
                            request_task_ids = request_meta["task_ids"]
                            
                            # 根据任务ID列表检查完成状态
                            completed_tasks = []
                            for subtask_id in request_task_ids:
                                if subtask_id in global_tasks_status:
                                    task_status = global_tasks_status[subtask_id]
                                    if task_status["status"] in ["COMPLETED", "ERROR"]:
                                        completed_tasks.append(subtask_id)
                            
                            # 计算队列中和处理中的任务
                            queued_tasks = []
                            processing_tasks = []
                            for subtask_id in request_task_ids:
                                if subtask_id in global_tasks_status:
                                    status = global_tasks_status[subtask_id]["status"]
                                    if status == "QUEUED":
                                        queued_tasks.append(subtask_id)
                                    elif status == "PROCESSING":
                                        processing_tasks.append(subtask_id)
                            
                            # 打印详细状态
                            print(f"请求 {request_id} 任务状态: 完成={len(completed_tasks)}/{expected_total}, "
                                  f"队列中={len(queued_tasks)}, 处理中={len(processing_tasks)}, "
                                  f"全局队列大小={global_task_queue.qsize()}")
                            print(f"完成的任务ID: {completed_tasks}")
                            
                            # 判断是否所有任务已完成
                            if len(completed_tasks) == expected_total:
                                # 所有任务已完成，发送完成事件
                                if not complete_sent:
                                    print(f"请求 {request_id} 的所有 {expected_total} 个任务已完成，发送完成事件")
                                    yield format_sse_event("complete", {
                                        "message": "所有任务处理完成",
                                        "request_id": request_id,
                                        "completed_tasks": len(completed_tasks),
                                        "total_tasks": expected_total
                                    })
                                    complete_sent = True
                                    
                                    # 等待一小段时间确保所有事件都被处理
                                    await asyncio.sleep(1)
                                    break
                        else:
                            # 如果没有元数据，使用之前的方法（但应该不会发生这种情况）
                            request_tasks = [t for t_id, t in global_tasks_status.items() if t["request_id"] == request_id]
                            completed_tasks = [t for t in request_tasks if t["status"] in ["COMPLETED", "ERROR"]]
                            
                            if request_tasks and completed_tasks and len(completed_tasks) == len(request_tasks):
                                # 所有任务已完成，发送完成事件
                                if not complete_sent:
                                    print(f"警告：使用备用方法判断请求 {request_id} 完成状态")
                                    yield format_sse_event("complete", {
                                        "message": "所有任务处理完成",
                                        "request_id": request_id,
                                        "completed_tasks": len(completed_tasks),
                                        "total_tasks": len(request_tasks)
                                    })
                                    complete_sent = True
                                    
                                    # 等待一小段时间确保所有事件都被处理
                                    await asyncio.sleep(1)
                                    break
                            
                        # 发送等待状态消息
                        print(f"等待请求 {request_id} 的任务完成, 全局队列大小={global_task_queue.qsize()}")
                
            except Exception as e:
                print(f"事件处理循环异常: {str(e)}")
                import traceback
                print(traceback.format_exc())
                
            finally:
                # 清理
                print(f"清理请求 {request_id} 的资源")
                if request_id in global_event_queues:
                    del global_event_queues[request_id]
                    
                # 清理请求元数据
                if request_id in global_request_metadata:
                    del global_request_metadata[request_id]
                    print(f"已清理请求 {request_id} 的元数据")
                
                # 如果还没有发送完成事件，确保发送
                if not complete_sent:
                    yield format_sse_event("complete", {
                        "message": "处理结束",
                        "request_id": request_id
                    })
                
                print(f"请求 {request_id} 的事件生成器结束")
            
        except Exception as e:
            # 发送错误
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
            "X-Accel-Buffering": "no"
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


