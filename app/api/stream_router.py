from fastapi import APIRouter, Request, BackgroundTasks, status
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from pydantic import BaseModel
import asyncio
import json
import uuid
import time
from typing import Optional, Callable, Awaitable, Any, Dict, List
import sys
import os
import re

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
from app.core.config import API_KEY, API_URL, PDFS_DIR, IMAGES_DIR
from app.utils.runninghub_api import (
    MAX_CONCURRENT_TASKS, 
    call_runninghub_workflow, 
    process_scene_prompts, 
    query_task_status, 
    query_task_result,
    wait_for_task_completion,
    cancel_runninghub_task,
    cancelled_task_ids
)
from app.utils.pdf_generator import create_script_pdf
from app.utils.image_downloader import download_images_from_event

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

# 添加一个新的任务映射，用于快速查找属于特定请求的所有RunningHub任务ID
global_runninghub_tasks = {}  # {request_id: set(runninghub_task_id1, runninghub_task_id2, ...)}

# 用于存储子任务结果的字典
subtask_results = {}

# 添加一个任务关联存储字典
script_to_image_task_mapping = {}  # {script_task_id: image_request_id}

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
        # 创建多个工作协程，实现并发处理
        workers = []
        for worker_id in range(MAX_CONCURRENT_TASKS):
            worker_task = asyncio.create_task(worker_process(worker_id))
            workers.append(worker_task)
            print(f"已启动工作协程 #{worker_id + 1}")
            
        # 等待所有工作协程完成（实际上除非程序终止，否则不会完成）
        await asyncio.gather(*workers)
    
    finally:
        async with global_worker_lock:
            is_global_worker_running = False
            print("全局工作器已停止")

# 单个工作协程的处理函数
async def worker_process(worker_id: int):
    print(f"工作协程 #{worker_id + 1} 已启动")
    
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
                
                # 检查此任务是否由于队列满而推迟处理
                if task.get("retry_after_queue_full") and task.get("added_time", 0) > time.time():
                    # 任务需要延迟处理，放回队列
                    await global_task_queue.put(task)
                    global_task_queue.task_done()
                    print(f"工作协程 #{worker_id + 1} - 任务 {subtask_id} 需要延迟处理，放回队列")
                    
                    # 等待一小段时间再继续处理其他任务，避免频繁重复处理同一任务
                    await asyncio.sleep(5)
                    continue
                
                # 更新任务状态为处理中
                print(f"工作协程 #{worker_id + 1} 开始处理任务: {subtask_id} (请求: {request_id})")
                global_tasks_status[subtask_id] = {
                    "status": "PROCESSING",
                    "start_time": time.time(),
                    "request_id": request_id,
                    "task_data": task_data,
                    "worker_id": worker_id
                }
                
                # 添加场次信息，便于排查问题
                if "task_data" in task and "scene" in task["task_data"] and "episode" in task["task_data"]:
                    print(f"任务详情: 第{task['task_data']['episode']}集 场次{task['task_data']['scene']} 提示词索引{task['task_data']['prompt_index']}")
                
                # 发送状态更新
                await event_queue.put(format_sse_event("status", {
                    "message": f"开始处理任务: 第{task_data['episode']}集 场次{task_data['scene']} 提示词{task_data['prompt_index']}",
                    "task_id": subtask_id,
                    "status": "PROCESSING"
                }))
                
                # 在创建任务前检查请求是否已被取消
                if request_id in global_runninghub_tasks and "CANCELLED_REQUEST" in global_runninghub_tasks[request_id]:
                    print(f"工作协程 #{worker_id + 1} - 请求 {request_id} 已被取消，跳过任务 {subtask_id}")
                    
                    # 更新任务状态为已取消
                    global_tasks_status[subtask_id] = {
                        "status": "CANCELLED",
                        "end_time": time.time(),
                        "request_id": request_id,
                        "task_data": task_data,
                        "message": "请求已被取消，任务未执行"
                    }
                    
                    # 发送取消事件
                    await event_queue.put(format_sse_event("task_cancelled", {
                        "task_id": subtask_id,
                        "message": "请求已被取消，任务未执行",
                        "worker_id": worker_id + 1
                    }))
                    
                    # 标记任务完成并返回
                    global_task_queue.task_done()
                    continue
                
                # 处理任务
                print(f"工作协程 #{worker_id + 1} 处理任务: {subtask_id}, 请求ID: {request_id}")
                
                try:
                    # 调用RunningHub API
                    prompt = task_data["prompt"]
                    
                    # 添加重试逻辑
                    max_retries = 10
                    retry_count = 0
                    retry_delay = 30  # 初始等待30秒
                    create_result = None
                    
                    # 设置一个不阻塞整个工作协程的任务等待策略
                    while retry_count < max_retries:
                        # 调用API创建任务
                        create_result = await call_runninghub_workflow(prompt)
                        
                        # 检查是否任务队列已满
                        if (create_result and isinstance(create_result, dict) and 
                            create_result.get("code") == 421 and 
                            create_result.get("msg") == "TASK_QUEUE_MAXED"):
                            
                            retry_count += 1
                            wait_time = min(retry_delay * retry_count, 300)  # 递增等待时间，最大5分钟
                            
                            # 更新任务状态为等待
                            global_tasks_status[subtask_id] = {
                                "status": "WAITING",
                                "wait_time": time.time(),
                                "request_id": request_id,
                                "task_data": task_data,
                                "retry_count": retry_count,
                                "max_retries": max_retries,
                                "worker_id": worker_id
                            }
                            
                            # 发送等待通知
                            await event_queue.put(format_sse_event("task_waiting", {
                                "episode": task_data["episode_key"],
                                "scene": task_data["scene_key"],
                                "prompt_index": str(task_data["prompt_index"]),
                                "task_id": subtask_id,
                                "retry": retry_count,
                                "max_retries": max_retries,
                                "wait_seconds": wait_time,
                                "message": f"RunningHub队列已满，等待{wait_time}秒后重试 ({retry_count}/{max_retries})",
                                "worker_id": worker_id + 1
                            }))
                            
                            print(f"工作协程 #{worker_id + 1} - RunningHub队列已满，等待{wait_time}秒后重试 ({retry_count}/{max_retries})")
                            
                            # 如果已达最大重试次数，将任务重新放回队列末尾而不是失败
                            if retry_count >= max_retries:
                                print(f"工作协程 #{worker_id + 1} - 任务 {subtask_id} 达到最大重试次数，放回队列末尾")
                                # 将任务放回队列末尾，增加延迟标记
                                task_item = {
                                    "request_id": request_id,
                                    "task_data": task_data,
                                    "added_time": time.time() + 600,  # 10分钟后才尝试处理
                                    "subtask_id": subtask_id,
                                    "retry_after_queue_full": True
                                }
                                
                                await global_task_queue.put(task_item)
                                
                                # 发送放回队列通知
                                await event_queue.put(format_sse_event("task_requeued", {
                                    "episode": task_data["episode_key"],
                                    "scene": task_data["scene_key"],
                                    "prompt_index": str(task_data["prompt_index"]),
                                    "task_id": subtask_id,
                                    "message": "已达最大重试次数，任务放回队列末尾，将在稍后处理",
                                    "worker_id": worker_id + 1
                                }))
                                
                                # 标记当前任务为已完成，因为我们已将其重新入队
                                global_task_queue.task_done()
                                
                                # 不要发送进度更新，因为任务尚未真正完成
                                # 也不要更新全局状态，保持为"WAITING"状态
                                
                                # 跳到外层循环，处理下一个任务
                                break
                            
                            # 让出执行权，让其他工作协程有机会处理其他任务
                            # 不使用sleep阻塞整个协程，而是使用一个带超时的任务
                            try:
                                wait_task = asyncio.create_task(asyncio.sleep(wait_time))
                                # 设置较短的超时，周期性检查是否应该继续等待
                                check_interval = 5  # 每5秒检查一次是否应该继续等待
                                wait_remaining = wait_time
                                
                                while wait_remaining > 0:
                                    try:
                                        await asyncio.wait_for(asyncio.shield(wait_task), timeout=min(check_interval, wait_remaining))
                                        break  # 等待完成
                                    except asyncio.TimeoutError:
                                        # 检查是否应该继续等待
                                        wait_remaining -= check_interval
                                        # 这里可以添加检查条件，例如是否有取消请求等
                            except asyncio.CancelledError:
                                if not wait_task.done():
                                    wait_task.cancel()
                                raise
                            
                            continue
                        else:
                            # 不是队列已满错误，跳出重试循环
                            break
                    
                    # 如果是因为放回队列而跳出的循环，则跳过后续处理
                    if retry_count >= max_retries:
                        continue
                        
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
                        "runninghub_task_id": runninghub_task_id,
                        "worker_id": worker_id + 1
                    }))
                    
                    # 在任务创建后更新全局状态，添加runninghub_task_id
                    if runninghub_task_id and subtask_id in global_tasks_status:
                        global_tasks_status[subtask_id]["runninghub_task_id"] = runninghub_task_id
                        print(f"为任务 {subtask_id} 记录runninghub_task_id: {runninghub_task_id}")
                        
                        # 将RunningHub任务ID添加到全局映射
                        if request_id not in global_runninghub_tasks:
                            global_runninghub_tasks[request_id] = set()
                        global_runninghub_tasks[request_id].add(runninghub_task_id)
                        print(f"将RunningHub任务ID {runninghub_task_id} 添加到请求 {request_id} 的映射，当前任务数: {len(global_runninghub_tasks[request_id])}")
                    
                    # 如果创建成功，等待任务完成
                    result = {
                        "prompt": prompt,
                        "create_result": create_result,
                        "task_id": runninghub_task_id,
                        "status": "FAILED"  # 默认失败，成功时会更新
                    }
                    
                    if runninghub_task_id:
                        # 等待RunningHub任务完成，传递request_id以检查请求级别的取消
                        final_status, final_result = await wait_for_task_completion(
                            runninghub_task_id, 
                            callback=None, 
                            request_id=request_id
                        )
                        
                        # 更新结果
                        result["status_result"] = final_status
                        result["final_result"] = final_result
                        result["status"] = "SUCCESS" if final_status in ["SUCCESS", "FINISHED", "COMPLETE", "COMPLETED"] else "FAILED"
                    
                    # 更新全局状态
                    print(f"工作协程 #{worker_id + 1} 更新任务 {subtask_id} 状态为 COMPLETED")
                    global_tasks_status[subtask_id] = {
                        "status": "COMPLETED",
                        "end_time": time.time(),
                        "request_id": request_id,
                        "task_data": task_data,
                        "result": result,
                        "runninghub_task_id": runninghub_task_id  # 直接保存runninghub_task_id
                    }
                    
                    # 发送完成事件 - 使用明确的单任务完成事件类型以避免与整体流程完成事件混淆
                    await event_queue.put(format_sse_event("subtask_completed", {
                        "episode": task_data["episode_key"],
                        "scene": task_data["scene_key"],
                        "prompt_index": str(task_data["prompt_index"]),
                        "task_id": subtask_id,
                        "runninghub_task_id": runninghub_task_id,
                        "status": result["status"],
                        "worker_id": worker_id + 1,
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
                    error_msg = f"工作协程 #{worker_id + 1} 处理任务时出错: {str(e)}"
                    print(error_msg)
                    
                    # 更新全局状态
                    print(f"工作协程 #{worker_id + 1} 更新任务 {subtask_id} 状态为 ERROR")
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
                        "error": str(e),
                        "worker_id": worker_id + 1
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
                        waiting_count = 0
                        
                        for current_id in task_ids_list:
                            if current_id in global_tasks_status:
                                status = global_tasks_status[current_id]["status"] 
                                if status in ["COMPLETED", "ERROR"]:
                                    completed_count += 1
                                elif status == "WAITING":
                                    waiting_count += 1
                        
                        print(f"工作协程 #{worker_id + 1} - 请求 {request_id} 进度更新: 完成={completed_count}/{expected_total}, 等待中={waiting_count}")
                        
                        # 发送进度更新
                        await event_queue.put(format_sse_event("progress", {
                            "completed": completed_count,
                            "total": expected_total,
                            "waiting": waiting_count,
                            "percentage": int(completed_count * 100 / expected_total) if expected_total else 0
                        }))
                        
                        # 检查请求的所有任务是否完成 - 只有当没有等待中的任务，且完成数等于总数时才真正完成
                        if completed_count == expected_total and waiting_count == 0:
                            print(f"工作协程 #{worker_id + 1} - 请求 {request_id} 的所有 {expected_total} 个任务已完成")
                            await event_queue.put(format_sse_event("all_tasks_completed", {
                                "request_id": request_id,
                                "completed": completed_count,
                                "total": expected_total
                            }))
                    else:
                        # 备用方法：如果没有元数据，使用过滤方法计算
                        request_tasks = [t for t_id, t in global_tasks_status.items() if t["request_id"] == request_id]
                        completed_tasks = [t for t in request_tasks if t["status"] in ["COMPLETED", "ERROR"]]
                        waiting_tasks = [t for t in request_tasks if t["status"] == "WAITING"]
                        
                        print(f"工作协程 #{worker_id + 1} - 备用进度方法，请求 {request_id} 完成={len(completed_tasks)}/{len(request_tasks)}, 等待中={len(waiting_tasks)}")
                        
                        await event_queue.put(format_sse_event("progress", {
                            "completed": len(completed_tasks),
                            "total": len(request_tasks),
                            "waiting": len(waiting_tasks),
                            "percentage": int(len(completed_tasks) * 100 / len(request_tasks)) if request_tasks else 0
                        }))
                        
                        # 检查请求的所有任务是否完成 - 确保没有等待中的任务
                        if len(completed_tasks) == len(request_tasks) and len(waiting_tasks) == 0 and len(request_tasks) > 0:
                            print(f"工作协程 #{worker_id + 1} - 请求 {request_id} 的所有任务已完成 (备用方法)")
                            await event_queue.put(format_sse_event("all_tasks_completed", {
                                "request_id": request_id,
                                "completed": len(completed_tasks),
                                "total": len(request_tasks)
                            }))
            
            except asyncio.CancelledError:
                print(f"工作协程 #{worker_id + 1} 被取消")
                break
                
            except Exception as e:
                print(f"工作协程 #{worker_id + 1} 异常: {str(e)}")
                import traceback
                print(traceback.format_exc())
                # 确保即使出现异常，全局队列任务也能标记为完成
                try:
                    global_task_queue.task_done()
                except Exception:
                    pass
    
    except Exception as e:
        print(f"工作协程 #{worker_id + 1} 致命错误: {str(e)}")
        import traceback
        print(traceback.format_exc())

# 启动全局工作器的后台任务
def ensure_global_worker_running():
    # 检查工作器是否运行
    if not is_global_worker_running:
        # 创建后台任务
        asyncio.create_task(start_global_worker())
        print("已启动全局工作器后台任务，最多可并发处理 {} 个任务".format(MAX_CONCURRENT_TASKS))

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
    auto_download: Optional[bool] = True

class RunningHubTaskStatusRequest(BaseModel):
    task_id: str

class RunningHubTaskResultRequest(BaseModel):
    task_id: str

class RunningHubTaskCancelRequest(BaseModel):
    request_id: str

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
    
    # 将任务添加到活跃任务字典中
    active_streaming_tasks[task_id] = {
        "is_active": True,
        "start_time": time.time(),
        "queue": queue,
        "type": "script_generation"
    }
    print(f"创建新的流式生成任务: {task_id}，当前活跃任务数: {len(active_streaming_tasks)}")
    
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
                    
                    # 检查是否是取消事件
                    if isinstance(item, dict) and item.get("type") == "cancel":
                        print(f"收到取消事件: {task_id}")
                        yield format_sse_event("canceled", {"message": "生成已被用户取消"})
                        break
                        
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
                    
                    # 检查任务是否已被取消
                    if task_id in active_streaming_tasks and not active_streaming_tasks[task_id]["is_active"]:
                        print(f"检测到任务 {task_id} 已被取消")
                        yield format_sse_event("canceled", {"message": "生成已被用户取消"})
                        break
                    
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
                
            # 从活跃任务列表中移除
            if task_id in active_streaming_tasks:
                del active_streaming_tasks[task_id]
                print(f"任务 {task_id} 已从活跃列表中移除")
    
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
    print(f"收到取消流式生成请求: {task_id}，当前活跃任务: {list(active_streaming_tasks.keys())}")
    
    if task_id in active_streaming_tasks:
        print(f"找到活跃任务 {task_id}，准备取消")
        active_streaming_tasks[task_id]["is_active"] = False
        
        # 获取任务类型，以便可能需要额外的清理操作
        task_type = active_streaming_tasks[task_id].get("type", "unknown")
        
        # 查找与此任务相关的资源进行清理（如果有）
        try:
            # 例如：对于剧本生成，尝试取消正在进行的任务
            if task_type == "script_generation" and "queue" in active_streaming_tasks[task_id]:
                queue = active_streaming_tasks[task_id]["queue"]
                await queue.put({"type": "cancel", "message": "用户取消了生成"})
                print(f"已向任务 {task_id} 的队列发送取消事件")
        except Exception as e:
            print(f"取消任务 {task_id} 时出错: {str(e)}")
            
        return {"status": "canceled", "task_id": task_id, "task_type": task_type}
    
    # 检查task_id是否是请求ID，查找相关子任务
    related_tasks = []
    for active_id, task_info in list(active_streaming_tasks.items()):
        if active_id.startswith(task_id) or (task_info.get("request_id") == task_id):
            related_tasks.append(active_id)
            
    if related_tasks:
        print(f"找到 {len(related_tasks)} 个相关任务: {related_tasks}")
        for related_id in related_tasks:
            active_streaming_tasks[related_id]["is_active"] = False
            # 执行与上面相同的清理操作
            
        return {
            "status": "canceled", 
            "task_id": task_id, 
            "related_tasks": related_tasks
        }
            
    # 任务未找到，返回404
    print(f"未找到任务 {task_id}，活跃任务列表: {list(active_streaming_tasks.keys())}")
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

@router.post("/stream/process-prompts-with-runninghub")
async def process_prompts_with_runninghub(request: RunningHubProcessRequest):
    """
    将剧本中提取的画面描述词发送到RunningHub API处理
    使用task_id标识剧本，采用队列方式处理提示词，实时返回每个任务的结果
    """
    # 从请求体中获取task_id
    script_task_id = request.task_id
    
    # 是否自动下载图片
    auto_download = request.auto_download if hasattr(request, 'auto_download') else True
    
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
            
            # 保存剧本任务ID和图片请求ID的映射关系
            script_to_image_task_mapping[script_task_id] = request_id
            print(f"已创建任务映射: 剧本任务 {script_task_id} -> 图片请求 {request_id}")
            
            # 创建事件队列并注册到全局字典
            event_queue = asyncio.Queue()
            global_event_queues[request_id] = event_queue
            
            # 发送开始事件
            yield format_sse_event("status", {"message": "正在提取画面描述词并发送到RunningHub...", "request_id": request_id})
            
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
                
                print(f"\n开始添加第{episode}集的任务到队列:")
                
                # 按场次编号排序，确保按照顺序处理
                sorted_scenes = sorted(scenes.keys(), key=lambda x: tuple(map(int, x.split('-'))))
                for scene in sorted_scenes:
                    # 格式化场景键为"场次X-X"
                    scene_key = f"场次{scene}" if not str(scene).startswith("场次") else str(scene)
                    
                    print(f"  处理场次{scene}的提示词:")
                    prompts = scenes[scene]
                    
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
                            
                            print(f"    添加任务: {subtask_id} - 提示词: {clean_prompt[:50]}..." if len(clean_prompt) > 50 else f"    添加任务: {subtask_id} - 提示词: {clean_prompt}")
                            
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
                        
                        # 检查是否是complete事件或cancel_complete事件，注意避免混淆task_completed与complete事件
                        if (("event: complete" in event) or 
                            ("event: cancel_complete" in event) or 
                            ("event: all_tasks_completed" in event)):  # 确保完全匹配事件名称
                            
                            # 标记已发送完成事件
                            if not complete_sent:
                                print(f"收到完成或取消事件，准备结束事件流: {event}")
                                
                                # 如果收到的是取消事件，确保发送complete事件
                                if "event: cancel_complete" in event and "event: complete" not in event:
                                    yield format_sse_event("complete", {
                                        "message": "所有任务处理完成(已取消)",
                                        "request_id": request_id
                                    })
                                
                                # 如果接收到all_tasks_completed但没有收到complete
                                if "event: all_tasks_completed" in event and "event: complete" not in event:
                                    # 检查所有图片下载任务是否完成
                                    all_downloads_done = True
                                    if 'download_tasks' in locals() and download_tasks:
                                        print(f"检查{len(download_tasks)}个图片下载任务状态...")
                                        # 检查是否所有下载任务都已完成
                                        for dt in download_tasks:
                                            if not dt.done():
                                                all_downloads_done = False
                                                print(f"等待图片下载任务完成...")
                                                # 继续等待，不立即发送complete事件
                                                break
                                    
                                    if all_downloads_done:
                                        print(f"所有图片下载任务已完成，发送complete事件")
                                        yield format_sse_event("complete", {
                                            "message": "所有任务和图片下载处理完成",
                                            "request_id": request_id
                                        })
                                        complete_sent = True
                                
                                if "event: complete" not in event and "event: all_tasks_completed" not in event:
                                    complete_sent = True
                            
                            # 如果是complete事件，准备结束循环
                            if "event: complete" in event:
                                # 等待一小段时间确保所有事件都被处理
                                await asyncio.sleep(1)
                                print(f"收到complete事件，结束事件流")
                                break
                        
                        # 检查是否是subtask_completed事件，如果是并且自动下载设置为True，则下载图片
                        if auto_download and "event: subtask_completed" in event:
                            try:
                                # 解析事件数据
                                event_data = json.loads(event.split("data: ")[1])
                                print(f"收到子任务完成事件，正在处理图片下载: {event_data.get('task_id')}")
                                
                                # 异步下载图片，不阻塞主流程
                                download_task = asyncio.create_task(
                                    download_and_report_images(event_data, event_queue)
                                )
                                
                                # 将下载任务添加到跟踪列表
                                if 'download_tasks' not in locals():
                                    download_tasks = []
                                download_tasks.append(download_task)
                                
                            except Exception as e:
                                print(f"处理下载图片时出错: {str(e)}")
                        
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
                            
                            # 判断是否所有任务已完成
                            if len(completed_tasks) == expected_total:
                                # 检查所有图片下载任务是否完成
                                all_downloads_done = True
                                if 'download_tasks' in locals() and download_tasks:
                                    # 检查是否所有下载任务都已完成
                                    for dt in download_tasks:
                                        if not dt.done():
                                            all_downloads_done = False
                                            print(f"等待图片下载任务完成...")
                                            break
                                
                                # 所有任务和下载都已完成，发送完成事件
                                if all_downloads_done and not complete_sent:
                                    print(f"请求 {request_id} 的所有 {expected_total} 个任务和图片下载已完成，发送完成事件")
                                    yield format_sse_event("complete", {
                                        "message": "所有任务和图片下载处理完成",
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
                                # 检查所有图片下载任务是否完成
                                all_downloads_done = True
                                if 'download_tasks' in locals() and download_tasks:
                                    # 检查是否所有下载任务都已完成
                                    for dt in download_tasks:
                                        if not dt.done():
                                            all_downloads_done = False
                                            print(f"备用方法：等待图片下载任务完成...")
                                            break
                                
                                # 所有任务已完成，发送完成事件
                                if all_downloads_done and not complete_sent:
                                    print(f"警告：使用备用方法判断请求 {request_id} 完成状态")
                                    yield format_sse_event("complete", {
                                        "message": "所有任务和图片下载处理完成",
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

@router.post("/runninghub/task-cancel")
async def cancel_runninghub_task_route(request: RunningHubTaskCancelRequest):
    """
    取消任务ID相关的所有RunningHub任务并从队列中删除待处理任务
    
    Args:
        request: 包含request_id的请求对象
    """
    request_id = request.request_id
    print(f"收到取消任务请求: request_id={request_id}")
    
    # 添加调试信息 - 输出全局状态中的任务信息
    debug_info = {
        "global_tasks_count": len(global_tasks_status),
        "global_queue_size": global_task_queue.qsize(),
        "sample_task_ids": list(global_tasks_status.keys())[:5] if global_tasks_status else [],
        "runninghub_tasks_count": sum(len(ids) for ids in global_runninghub_tasks.values()),
        "request_has_tasks": request_id in global_runninghub_tasks,
        "active_requests": list(global_runninghub_tasks.keys())
    }
    print(f"调试信息: {debug_info}")
    
    # 查找与该task_id相关的所有任务 - 扩大搜索范围
    tasks_to_cancel = []
    runninghub_task_ids = set()  # 使用集合去重
    cancellation_results = []
    
    # 1. 首先直接从全局RunningHub任务映射查找
    if request_id in global_runninghub_tasks:
        print(f"从全局映射中找到请求 {request_id} 的RunningHub任务")
        runninghub_task_ids.update(global_runninghub_tasks[request_id])
        print(f"已从全局映射中添加 {len(global_runninghub_tasks[request_id])} 个RunningHub任务ID")
    
    # 2. 查找所有与该task_id相关的任务
    for subtask_id, task_info in global_tasks_status.items():
        # 检查任务ID是否相关
        task_related = False
        
        # 条件1: 子任务ID以request_id开头
        if subtask_id.startswith(request_id):
            task_related = True
            print(f"找到匹配任务(子任务ID前缀): {subtask_id}")
            
        # 条件2: 请求ID等于request_id
        elif task_info.get("request_id") == request_id:
            task_related = True
            print(f"找到匹配任务(请求ID): {subtask_id}")
            
        # 条件3: 任务数据中包含request_id
        elif "task_data" in task_info and str(task_info["task_data"]).find(request_id) != -1:
            task_related = True
            print(f"找到匹配任务(任务数据): {subtask_id}")
            
        # 条件4: 如果request_id是UUID的一部分，检查部分匹配
        elif len(request_id) > 8 and (subtask_id.find(request_id) != -1 or (task_info.get("request_id") and task_info.get("request_id").find(request_id) != -1)):
            task_related = True
            print(f"找到匹配任务(部分匹配): {subtask_id}")
        
        # 如果任务相关，添加到取消列表
        if task_related:
            # 记录子任务ID
            tasks_to_cancel.append(subtask_id)
            
            # 尝试从任务信息中提取runninghub_task_id
            extracted_ids = []
            
            # 方法1: 直接从任务信息中提取runninghub_task_id字段
            if "runninghub_task_id" in task_info:
                extracted_ids.append(task_info["runninghub_task_id"])
                print(f"直接从任务信息中提取到RunningHub任务ID: {task_info['runninghub_task_id']}")
            
            # 方法2: 从结果字段提取
            if "result" in task_info and isinstance(task_info["result"], dict) and task_info["result"].get("task_id"):
                extracted_ids.append(task_info["result"].get("task_id"))
                print(f"从结果字段提取到RunningHub任务ID: {task_info['result'].get('task_id')}")
            
            # 方法3: 从原始数据提取
            if "task_data" in task_info and isinstance(task_info["task_data"], dict):
                # 直接查找runninghub_task_id字段
                if "runninghub_task_id" in task_info["task_data"]:
                    extracted_ids.append(task_info["task_data"]["runninghub_task_id"])
                    print(f"从任务数据中提取到RunningHub任务ID: {task_info['task_data']['runninghub_task_id']}")
                
                # 遍历所有可能包含task_id的字段
                for k, v in task_info["task_data"].items():
                    if k.lower().find("task_id") != -1 and isinstance(v, str):
                        extracted_ids.append(v)
                        print(f"从字段 {k} 提取到可能的RunningHub任务ID: {v}")
            
            # 添加所有提取到的ID
            for rid in extracted_ids:
                if rid and isinstance(rid, (str, int)) and str(rid).strip():
                    runninghub_task_ids.add(str(rid).strip())
                    print(f"找到RunningHub任务ID: {rid}")

    print(f"找到{len(tasks_to_cancel)}个相关任务, {len(runninghub_task_ids)}个RunningHub任务ID")
    
    # 取消所有找到的RunningHub任务
    for runninghub_task_id in runninghub_task_ids:
        try:
            print(f"取消RunningHub任务: {runninghub_task_id}")
            cancel_result = await cancel_runninghub_task(runninghub_task_id)
            cancellation_results.append({
                "runninghub_task_id": runninghub_task_id,
                "result": cancel_result
            })
            
            # 直接添加到取消任务集合，确保立即停止状态检查
            if runninghub_task_id not in cancelled_task_ids:
                cancelled_task_ids.add(runninghub_task_id)
                print(f"已将任务 {runninghub_task_id} 添加到取消集合，当前大小: {len(cancelled_task_ids)}")
                
        except Exception as e:
            print(f"取消RunningHub任务出错: {runninghub_task_id}, 错误: {str(e)}")
            cancellation_results.append({
                "runninghub_task_id": runninghub_task_id,
                "error": str(e)
            })
    
    # 收集所有相关的请求ID，用于发送complete事件
    related_request_ids = set()
    for subtask_id in tasks_to_cancel:
        if subtask_id in global_tasks_status:
            req_id = global_tasks_status[subtask_id].get("request_id")
            if req_id:
                related_request_ids.add(req_id)
    
    # 更新任务状态为已取消并发送通知
    updated_task_count = 0
    for subtask_id in tasks_to_cancel:
        try:
            if subtask_id in global_tasks_status:
                # 更新状态为已取消
                global_tasks_status[subtask_id]["status"] = "CANCELLED"
                updated_task_count += 1
                print(f"已取消任务: {subtask_id}")
                
                # 获取请求ID用于发送事件通知
                req_id = global_tasks_status[subtask_id].get("request_id")
                
                # 发送取消事件通知前端
                if req_id and req_id in global_event_queues:
                    event_queue = global_event_queues[req_id]
                    await event_queue.put(format_sse_event("task_cancelled", {
                        "task_id": subtask_id,
                        "message": "任务已取消"
                    }))
        except Exception as e:
            print(f"取消任务 {subtask_id} 时出错: {str(e)}")
    
    # 创建取消标记，防止后续创建的任务继续执行
    # 这将阻止即使是在取消命令之后创建的任务
    if request_id not in global_runninghub_tasks:
        global_runninghub_tasks[request_id] = set()
    
    # 添加特殊标记表示这个请求已被取消
    global_runninghub_tasks[request_id].add("CANCELLED_REQUEST")
    
    # 从全局队列中移除相关任务
    removed_count = 0
    if not global_task_queue.empty():
        # 创建临时队列存储需要保留的任务
        temp_queue = asyncio.Queue()
        
        # 移动任务到临时队列
        try:
            orig_queue_size = global_task_queue.qsize()
            print(f"开始清理队列, 当前队列大小: {orig_queue_size}")
            
            while not global_task_queue.empty():
                task = await global_task_queue.get()
                subtask_id = task.get("subtask_id")
                task_request_id = task.get("request_id")
                
                # 如果任务不在要取消的列表中且不属于要取消的请求，则保留
                if (subtask_id not in tasks_to_cancel and 
                    (task_request_id != request_id) and 
                    not (subtask_id and subtask_id.startswith(request_id))):
                    await temp_queue.put(task)
                else:
                    removed_count += 1
                    print(f"从队列中移除任务: {subtask_id}")
                
                global_task_queue.task_done()
            
            temp_queue_size = temp_queue.qsize()
            print(f"临时队列大小: {temp_queue_size}, 移除的任务数: {removed_count}")
            
            # 将保留的任务移回全局队列
            while not temp_queue.empty():
                task = await temp_queue.get()
                await global_task_queue.put(task)
                temp_queue.task_done()
                
            print(f"队列清理完成, 新队列大小: {global_task_queue.qsize()}")
            
        except Exception as e:
            print(f"清理队列时出错: {str(e)}")
    
    # 给所有相关的请求发送complete事件
    notified_requests = 0
    for req_id in related_request_ids:
        if req_id in global_event_queues:
            try:
                event_queue = global_event_queues[req_id]
                # 首先发送取消完成的通知
                await event_queue.put(format_sse_event("cancel_complete", {
                    "message": "所有任务已成功取消",
                    "request_id": req_id,
                    "cancelled_count": updated_task_count
                }))
                
                # 然后发送流结束的complete事件
                await event_queue.put(format_sse_event("complete", {
                    "message": "流处理已终止",
                    "request_id": req_id,
                    "reason": "任务已取消"
                }))
                
                notified_requests += 1
                print(f"已向请求 {req_id} 发送完成事件")
            except Exception as e:
                print(f"向请求 {req_id} 发送完成事件时出错: {str(e)}")
    
    return {
        "status": "success",
        "message": "任务取消请求已处理",
        "request_id": request_id,
        "cancelled_runninghub_tasks": updated_task_count,
        "updated_tasks": updated_task_count,
        "removed_from_queue": removed_count,
        "tasks_affected": len(tasks_to_cancel),
        "request_ids_notified": notified_requests,
        "debug_info": debug_info
    }

# @router.get("/collect-task-images/{request_id}")
async def collect_task_images(request_id: str):
    """收集与请求ID相关的所有子任务图片URL"""
    print(f"开始收集任务 {request_id} 的图片URL")
    
    collected_images = {
        "request_id": request_id,
        "episodes": {}
    }
    
    # 方法1: 从subtask_results中收集
    print(f"方法1: 从subtask_results中收集图片")
    related_tasks = []
    for task_id, task_data in subtask_results.items():
        if task_id.startswith(request_id):
            related_tasks.append(task_data)
            print(f"找到关联任务: {task_id}")
    
    print(f"从subtask_results找到 {len(related_tasks)} 个相关任务")
    
    # 处理每个任务的图片URL
    for task in related_tasks:
        if "result" in task and "episode" in task["result"] and "results" in task["result"]:
            episode = task["result"]["episode"]
            print(f"处理第 {episode} 集的数据")
            
            if episode not in collected_images["episodes"]:
                collected_images["episodes"][episode] = {}
            
            for scene, prompts in task["result"]["results"].items():
                print(f"  处理场次 {scene}")
                
                if scene not in collected_images["episodes"][episode]:
                    collected_images["episodes"][episode][scene] = {}
                
                for prompt_idx, prompt_data in prompts.items():
                    print(f"    处理提示词 {prompt_idx}")
                    
                    if "final_result" in prompt_data and "data" in prompt_data["final_result"]:
                        image_urls = []
                        for item in prompt_data["final_result"]["data"]:
                            if "fileUrl" in item:
                                image_urls.append(item["fileUrl"])
                                print(f"      找到图片URL: {item['fileUrl'][:50]}...")
                        
                        collected_images["episodes"][episode][scene][prompt_idx] = {
                            "prompt": prompt_data.get("prompt", ""),
                            "image_urls": image_urls
                        }
    
    # 方法2: 如果没有找到足够的图片，尝试从global_tasks_status中收集
    if not any(collected_images["episodes"].values()):
        print(f"方法2: 从global_tasks_status中收集图片")
        # 查找所有与该request_id相关的任务
        related_tasks_ids = []
        
        for subtask_id, task_info in global_tasks_status.items():
            if (subtask_id.startswith(request_id) or 
                task_info.get("request_id") == request_id):
                related_tasks_ids.append(subtask_id)
        
        print(f"从global_tasks_status找到 {len(related_tasks_ids)} 个相关任务")
        
        # 处理每个任务
        for subtask_id in related_tasks_ids:
            task_info = global_tasks_status[subtask_id]
            # 检查是否是已完成的任务
            if task_info.get("status") == "COMPLETED" and "result" in task_info:
                result = task_info["result"]
                
                # 从任务数据中提取集数和场次信息
                if "task_data" in task_info:
                    task_data = task_info["task_data"]
                    episode_key = task_data.get("episode")
                    scene_key = task_data.get("scene")
                    prompt_index = task_data.get("prompt_index")
                    
                    if episode_key and scene_key and prompt_index is not None:
                        # 从结果中提取图片URL
                        if "final_result" in result and "data" in result["final_result"]:
                            data_items = result["final_result"]["data"]
                            image_urls = []
                            
                            for item in data_items:
                                if isinstance(item, dict) and "fileUrl" in item:
                                    image_urls.append(item["fileUrl"])
                                    print(f"      找到图片URL: {item['fileUrl'][:50]}...")
                            
                            # 只有在找到图片URL时才添加
                            if image_urls:
                                # 初始化数据结构
                                if episode_key not in collected_images["episodes"]:
                                    collected_images["episodes"][episode_key] = {}
                                if scene_key not in collected_images["episodes"][episode_key]:
                                    collected_images["episodes"][episode_key][scene_key] = {}
                                
                                collected_images["episodes"][episode_key][scene_key][str(prompt_index)] = {
                                    "prompt": task_data.get("prompt", ""),
                                    "image_urls": image_urls
                                }
    
    # 方法3: 检查是否task_id对应的是请求ID而不是任务ID
    if not any(collected_images["episodes"].values()):
        print(f"方法3: 尝试检查global_runninghub_tasks")
        if request_id in global_runninghub_tasks:
            runninghub_task_ids = global_runninghub_tasks[request_id]
            print(f"在global_runninghub_tasks找到 {len(runninghub_task_ids)} 个RunningHub任务ID")
            
            # 根据RunningHub任务ID去查找相关子任务
            for task_id in runninghub_task_ids:
                print(f"处理RunningHub任务ID: {task_id}")
                if task_id != "CANCELLED_REQUEST":  # 跳过取消标记
                    # 可以尝试直接查询RunningHub API获取任务结果
                    try:
                        result = await query_task_result(task_id)
                        print(f"查询RunningHub任务 {task_id} 的结果: {result}")
                        # 解析结果并提取图片URL
                        # 这里需要根据API的返回格式进行具体实现
                    except Exception as e:
                        print(f"查询RunningHub任务结果出错: {str(e)}")
    
    # 打印收集的总结果
    episode_count = len(collected_images["episodes"])
    total_images = sum(
        len(prompt_data.get("image_urls", []))
        for ep_data in collected_images["episodes"].values()
        for scene_data in ep_data.values()
        for prompt_data in scene_data.values()
    )
    
    print(f"收集完成，共找到 {episode_count} 集, {total_images} 张图片")
    return collected_images

# 创建任务关联API
# @router.get("/link-tasks/{script_task_id}/{image_request_id}")
async def link_script_and_image_tasks(script_task_id: str, image_request_id: str):
    """关联剧本生成任务和图片生成任务"""
    script_to_image_task_mapping[script_task_id] = image_request_id
    print(f"手动关联任务: 剧本任务 {script_task_id} -> 图片请求 {image_request_id}")
    return {
        "status": "success", 
        "message": f"已关联剧本任务 {script_task_id} 和图片请求 {image_request_id}"
    }

# 查询任务关联API
# @router.get("/get-linked-tasks/{script_task_id}")
async def get_linked_tasks(script_task_id: str):
    """获取关联的图片生成任务ID"""
    image_request_id = script_to_image_task_mapping.get(script_task_id)
    if not image_request_id:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND, 
            content={"status": "error", "message": f"未找到与剧本任务 {script_task_id} 关联的图片请求"}
        )
    return {"script_task_id": script_task_id, "image_request_id": image_request_id}

def format_sse_event(event_type: str, data: Any) -> str:
    """格式化SSE事件"""
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n"

# PDF生成和下载路由
# @router.get("/generate-script-pdf/{task_id}")
async def generate_script_pdf(task_id: str):
    """
    生成剧本PDF文件并提供下载
    如果文件已存在则直接返回，避免重复生成
    
    Args:
        task_id: 剧本任务ID
    """
    try:
        print(f"开始处理PDF下载请求，剧本任务ID: {task_id}")
        
        # 加载剧本内容
        script_state = load_generation_state(task_id)
        if not script_state:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"status": "error", "message": f"找不到任务ID: {task_id} 的剧本内容"}
            )
        
        script_content = script_state.get("full_script", "")
        if not script_content:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"status": "error", "message": "剧本内容为空"}
            )
        
        # 确定使用哪个图片目录
        image_folder_id = None
        
        # 策略1：首先检查对应剧本任务ID的图片目录是否存在
        direct_image_path = os.path.join(IMAGES_DIR, task_id)
        if os.path.exists(direct_image_path) and os.path.isdir(direct_image_path):
            print(f"找到直接匹配的图片目录: {task_id}")
            image_folder_id = task_id
        else:
            # 策略2：查找关联的图片请求ID
            image_request_id = script_to_image_task_mapping.get(task_id)
            if image_request_id:
                request_image_path = os.path.join(IMAGES_DIR, image_request_id)
                if os.path.exists(request_image_path) and os.path.isdir(request_image_path):
                    print(f"找到关联图片目录: {image_request_id}")
                    image_folder_id = image_request_id
                else:
                    print(f"关联图片目录不存在: {request_image_path}")
            else:
                print(f"未找到关联的图片请求ID")
            
            # 策略3：查找任何可能相关的目录
            if not image_folder_id:
                print(f"尝试查找包含任务ID部分内容的图片目录")
                for dir_name in os.listdir(IMAGES_DIR):
                    dir_path = os.path.join(IMAGES_DIR, dir_name)
                    if os.path.isdir(dir_path) and task_id in dir_name:
                        print(f"找到相关目录: {dir_name}")
                        image_folder_id = dir_name
                        break
        
        # 如果找到了图片目录，使用该目录；否则使用剧本任务ID（即使目录不存在）
        final_image_id = image_folder_id if image_folder_id else task_id
        print(f"最终使用的图片目录ID: {final_image_id}")
        
        # 提取剧名，用于生成文件名
        title_match = re.search(r'剧名：《(.+?)》', script_content)
        title = title_match.group(1) if title_match else "未命名剧本"
        
        # 构建预期的PDF文件路径
        expected_pdf_filename = f"{title}_{final_image_id}.pdf"
        expected_pdf_path = os.path.join(PDFS_DIR, expected_pdf_filename)
        
        # 检查文件是否已存在
        if os.path.exists(expected_pdf_path):
            print(f"PDF文件已存在，直接返回文件: {expected_pdf_path}")
            # 从路径中提取文件名
            filename = os.path.basename(expected_pdf_path)
            
            # 返回文件
            return FileResponse(
                path=expected_pdf_path,
                filename=filename,
                media_type="application/pdf",
                background=None
            )
        
        print(f"PDF文件不存在，需要生成: {expected_pdf_path}")
        
        # 获取图片数据，用于构建PDF内容
        image_data = {"episodes": {}}
        
        # 使用剧本内容提取需要的图片信息
        try:
            # 从剧本中提取集数和场景信息
            prompts_dict = extract_prompts(script_content)
            
            # 构建图片数据结构
            for episode, scenes in prompts_dict.items():
                if episode not in image_data["episodes"]:
                    image_data["episodes"][episode] = {}
                
                for scene, prompts in scenes.items():
                    if scene not in image_data["episodes"][episode]:
                        image_data["episodes"][episode][scene] = {}
                    
                    for idx, prompt in enumerate(prompts):
                        clean_prompt = prompt.replace('#', '').strip()
                        if clean_prompt:
                            image_data["episodes"][episode][scene][str(idx)] = {
                                "prompt": clean_prompt
                            }
        except Exception as e:
            print(f"提取提示词数据时出错: {str(e)}")
            # 出错时仍然继续，只是没有提示词信息
        
        # 生成PDF文件，指定输出文件名
        pdf_path = await create_script_pdf(
            task_id=final_image_id,  # 使用确定的图片目录ID
            script_content=script_content,
            image_data=image_data["episodes"],
            output_dir=PDFS_DIR,
            filename=expected_pdf_filename  # 使用预期的文件名
        )
        
        print(f"PDF生成成功，文件路径: {pdf_path}")
        
        # 从路径中提取文件名
        filename = os.path.basename(pdf_path)
        
        # 返回文件
        return FileResponse(
            path=pdf_path,
            filename=filename,
            media_type="application/pdf",
            background=None
        )
        
    except Exception as e:
        # 输出详细错误信息以便调试
        import traceback
        error_details = traceback.format_exc()
        print(f"PDF生成出错: {str(e)}\n{error_details}")
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "error", 
                "message": f"生成PDF出错: {str(e)}"
            }
        )

# 图片下载和报告函数
async def download_and_report_images(event_data: Dict[str, Any], event_queue: asyncio.Queue):
    """下载事件中的图片并报告下载结果"""
    try:
        # 记录开始时间
        start_time = time.time()
        task_id = event_data.get('task_id')
        print(f"开始下载图片: 任务ID = {task_id}")
        
        # 查找关联的脚本任务ID
        script_task_id = None
        
        # 从事件数据中提取request_id
        # 首先尝试从task_id中获取，因为子任务ID通常格式为：请求ID_集数_场景_提示词索引
        if task_id and '_' in task_id:
            request_id = task_id.split('_')[0]
            print(f"从任务ID中提取请求ID: {request_id}")
            
            # 反向查找脚本任务ID
            for script_id, img_request_id in script_to_image_task_mapping.items():
                if img_request_id == request_id:
                    script_task_id = script_id
                    print(f"找到关联的脚本任务ID: {script_task_id}")
                    break
        
        # 如果任务ID中没有找到请求ID，从全局状态尝试获取
        if not script_task_id and task_id in global_tasks_status:
            task_info = global_tasks_status.get(task_id, {})
            request_id = task_info.get("request_id")
            if request_id:
                print(f"从全局状态找到请求ID: {request_id}")
                # 反向查找脚本任务ID
                for script_id, img_request_id in script_to_image_task_mapping.items():
                    if img_request_id == request_id:
                        script_task_id = script_id
                        print(f"找到关联的脚本任务ID: {script_task_id}")
                        break
        
        # 下载图片，传入脚本任务ID
        download_result = await download_images_from_event(event_data, script_task_id=script_task_id)
        
        # 计算下载耗时
        elapsed = time.time() - start_time
        
        # 为下载结果添加耗时信息
        download_result["download_time"] = f"{elapsed:.2f}秒"
        
        # 通过事件队列报告下载结果
        if event_queue:
            await event_queue.put(format_sse_event("image_download_complete", {
                "task_id": task_id,
                "script_task_id": script_task_id,
                "download_result": download_result,
                "message": f"已完成图片下载，共下载{download_result.get('download_result', {}).get('total_downloaded', 0)}张图片，耗时{elapsed:.2f}秒"
            }))
        
        print(f"图片下载完成: 任务ID = {task_id}, 脚本任务ID = {script_task_id}, 下载{download_result.get('download_result', {}).get('total_downloaded', 0)}张图片, 耗时{elapsed:.2f}秒")
        
        # 保存下载结果到subtask_results中
        subtask_id = task_id
        if subtask_id in global_tasks_status:
            task_info = global_tasks_status[subtask_id]
            if "result" in task_info:
                task_info["result"]["download_result"] = download_result
            
            print(f"已将下载结果保存到任务状态: {subtask_id}")
        
    except Exception as e:
        print(f"下载图片期间出错: {str(e)}")
        import traceback
        print(traceback.format_exc())

# PDF生成路由 - 仅返回文件路径
@router.get("/generate-script-pdf-path/{task_id}")
async def generate_script_pdf_path(task_id: str, timeout: Optional[int] = 60):
    """
    生成剧本PDF文件并返回文件路径
    如果文件已存在则直接返回路径，避免重复生成
    
    Args:
        task_id: 剧本任务ID
        timeout: PDF生成超时时间(秒)，默认60秒
        
    Returns:
        JSON对象，包含PDF文件路径
    """
    try:
        print(f"开始处理PDF路径请求，剧本任务ID: {task_id}")
        
        # 加载剧本内容
        script_state = load_generation_state(task_id)
        if not script_state:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"status": "error", "message": f"找不到任务ID: {task_id} 的剧本内容"}
            )
        
        script_content = script_state.get("full_script", "")
        if not script_content:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"status": "error", "message": "剧本内容为空"}
            )
        
        # 确定使用哪个图片目录
        image_folder_id = None
        
        # 策略1：首先检查对应剧本任务ID的图片目录是否存在
        direct_image_path = os.path.join(IMAGES_DIR, task_id)
        if os.path.exists(direct_image_path) and os.path.isdir(direct_image_path):
            print(f"找到直接匹配的图片目录: {task_id}")
            image_folder_id = task_id
        else:
            # 策略2：查找关联的图片请求ID
            image_request_id = script_to_image_task_mapping.get(task_id)
            if image_request_id:
                request_image_path = os.path.join(IMAGES_DIR, image_request_id)
                if os.path.exists(request_image_path) and os.path.isdir(request_image_path):
                    print(f"找到关联图片目录: {image_request_id}")
                    image_folder_id = image_request_id
                else:
                    print(f"关联图片目录不存在: {request_image_path}")
            else:
                print(f"未找到关联的图片请求ID")
            
            # 策略3：查找任何可能相关的目录
            if not image_folder_id:
                print(f"尝试查找包含任务ID部分内容的图片目录")
                for dir_name in os.listdir(IMAGES_DIR):
                    dir_path = os.path.join(IMAGES_DIR, dir_name)
                    if os.path.isdir(dir_path) and task_id in dir_name:
                        print(f"找到相关目录: {dir_name}")
                        image_folder_id = dir_name
                        break
        
        # 如果找到了图片目录，使用该目录；否则使用剧本任务ID（即使目录不存在）
        final_image_id = image_folder_id if image_folder_id else task_id
        print(f"最终使用的图片目录ID: {final_image_id}")
        
        # 提取剧名，用于生成文件名
        title_match = re.search(r'剧名：《(.+?)》', script_content)
        title = title_match.group(1) if title_match else "未命名剧本"
        
        # 构建预期的PDF文件路径
        expected_pdf_filename = f"{title}_{final_image_id}.pdf"
        expected_pdf_path = os.path.join(PDFS_DIR, expected_pdf_filename)
        
        # 检查文件是否已存在
        if os.path.exists(expected_pdf_path):
            print(f"PDF文件已存在，直接返回路径: {expected_pdf_path}")
            # 检查文件是否完整
            try:
                with open(expected_pdf_path, 'rb') as f:
                    # 检查文件大小是否大于0，并尝试读取文件末尾以确保文件完整
                    f.seek(0, 2)  # 移动到文件末尾
                    file_size = f.tell()
                    if file_size > 0:
                        # 从路径中提取文件名
                        filename = os.path.basename(expected_pdf_path)
                        # 构建相对路径
                        relative_path = f"/storage/pdfs/{filename}"
                        
                        # 返回文件路径信息
                        return JSONResponse(
                            status_code=status.HTTP_200_OK,
                            content={
                                "status": "success",
                                "message": "PDF文件已存在",
                                "data": {
                                    "filename": filename,
                                    "path": relative_path,
                                    "full_path": expected_pdf_path,
                                    "size": file_size
                                }
                            }
                        )
                    else:
                        print(f"文件大小为0，需要重新生成: {expected_pdf_path}")
                        # 删除零字节文件
                        os.remove(expected_pdf_path)
            except Exception as e:
                print(f"检查文件时出错，可能文件损坏: {str(e)}")
                # 尝试删除可能损坏的文件
                try:
                    os.remove(expected_pdf_path)
                    print(f"已删除可能损坏的文件: {expected_pdf_path}")
                except:
                    pass
        
        print(f"PDF文件不存在，需要生成: {expected_pdf_path}")
        
        # 获取图片数据，用于构建PDF内容
        image_data = {"episodes": {}}
        
        # 使用剧本内容提取需要的图片信息
        try:
            # 从剧本中提取集数和场景信息
            prompts_dict = extract_prompts(script_content)
            
            # 构建图片数据结构
            for episode, scenes in prompts_dict.items():
                if episode not in image_data["episodes"]:
                    image_data["episodes"][episode] = {}
                
                for scene, prompts in scenes.items():
                    if scene not in image_data["episodes"][episode]:
                        image_data["episodes"][episode][scene] = {}
                    
                    for idx, prompt in enumerate(prompts):
                        clean_prompt = prompt.replace('#', '').strip()
                        if clean_prompt:
                            image_data["episodes"][episode][scene][str(idx)] = {
                                "prompt": clean_prompt
                            }
        except Exception as e:
            print(f"提取提示词数据时出错: {str(e)}")
            # 出错时仍然继续，只是没有提示词信息
        
        # 使用异步任务和超时机制生成PDF
        try:
            # 创建任务，并设置超时
            pdf_path_future = asyncio.create_task(
                create_script_pdf(
                    task_id=final_image_id,  # 使用确定的图片目录ID
                    script_content=script_content,
                    image_data=image_data["episodes"],
                    output_dir=PDFS_DIR,
                    with_progress=True
                )
            )
            
            # 等待任务完成，增加超时处理
            pdf_path = await asyncio.wait_for(pdf_path_future, timeout=timeout)
            
            print(f"PDF生成成功，文件路径: {pdf_path}")
            
            # 从路径中提取文件名
            filename = os.path.basename(pdf_path)
            
            # 构建相对于API服务的相对路径（供前端使用）
            relative_path = f"/storage/pdfs/{filename}"
            
            # 获取文件大小
            file_size = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0
            
            # 返回文件路径信息
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "success",
                    "message": "PDF生成成功",
                    "data": {
                        "filename": filename,
                        "path": relative_path,
                        "full_path": pdf_path,
                        "size": file_size
                    }
                }
            )
        except asyncio.TimeoutError:
            print(f"PDF生成超时 (超过{timeout}秒)")
            return JSONResponse(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                content={
                    "status": "error",
                    "message": f"PDF生成超时 (超过{timeout}秒)，请稍后再试",
                    "data": {
                        "expected_path": expected_pdf_path
                    }
                }
            )
        
    except Exception as e:
        # 输出详细错误信息以便调试
        import traceback
        error_details = traceback.format_exc()
        print(f"PDF生成出错 (path模式): {str(e)}\n{error_details}")
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "error", 
                "message": f"生成PDF出错: {str(e)}"
            }
        )