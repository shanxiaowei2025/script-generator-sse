import asyncio
import time
from typing import Dict, List, Any, Optional, Set
from app.utils.runninghub_api import MAX_CONCURRENT_TASKS, cancelled_task_ids


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

# 流式生成状态跟踪
active_streaming_tasks = {}


def format_sse_event(event_type: str, data: Any) -> str:
    """格式化SSE事件"""
    import json
    json_data = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {json_data}\n\n"


# 启动全局工作器
async def start_global_worker():
    """启动全局工作器，管理并发任务处理"""
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
    """工作协程处理函数，负责处理队列中的任务"""
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
                    from app.utils.runninghub_api import call_runninghub_workflow, wait_for_task_completion
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
    """确保全局工作器正在运行"""
    # 检查工作器是否运行
    if not is_global_worker_running:
        # 创建后台任务
        asyncio.create_task(start_global_worker())
        print(f"已启动全局工作器后台任务，最多可并发处理 {MAX_CONCURRENT_TASKS} 个任务") 