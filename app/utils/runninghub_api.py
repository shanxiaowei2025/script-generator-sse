import aiohttp
import asyncio
import json
from typing import Dict, List, Any, Tuple, Set, Deque
from collections import deque
from app.core.config import (
    RUNNINGHUB_CREATE_API_URL,
    RUNNINGHUB_STATUS_API_URL,
    RUNNINGHUB_RESULT_API_URL,
    RUNNINGHUB_API_KEY,
    RUNNINGHUB_WORKFLOW_ID,
    RUNNINGHUB_NODE_ID
)

# 任务处理的最大并发数
MAX_CONCURRENT_TASKS = 3
# 任务状态检查之间的等待时间（秒）
TASK_STATUS_CHECK_INTERVAL = 10
# 最大等待次数
MAX_STATUS_CHECK_ATTEMPTS = 20
# 完成或失败的任务状态
FINISHED_TASK_STATUSES = ["SUCCESS", "FINISHED", "COMPLETE", "COMPLETED", "FAILED", "ERROR"]

async def call_runninghub_workflow(prompt: str) -> Dict[str, Any]:
    """
    调用RunningHub工作流API创建任务
    
    Args:
        prompt (str): 画面提示词
        
    Returns:
        Dict[str, Any]: API响应结果，成功时code为0
    """
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "apiKey": RUNNINGHUB_API_KEY,
        "workflowId": RUNNINGHUB_WORKFLOW_ID,
        "nodeInfoList": [
            {
                "nodeId": RUNNINGHUB_NODE_ID,
                "fieldName": "text",
                "fieldValue": prompt
            }
        ]
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                RUNNINGHUB_CREATE_API_URL,
                headers=headers,
                json=payload
            ) as response:
                response_data = await response.json()
                return response_data
    except Exception as e:
        return {"error": str(e), "status": "failed"}

async def query_task_status(task_id: str) -> Dict[str, Any]:
    """
    查询RunningHub任务状态
    
    Args:
        task_id (str): 任务ID
        
    Returns:
        Dict[str, Any]: API响应结果，成功时code为0
    """
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "apiKey": RUNNINGHUB_API_KEY,
        "taskId": task_id
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                RUNNINGHUB_STATUS_API_URL,
                headers=headers,
                json=payload
            ) as response:
                response_data = await response.json()
                return response_data
    except Exception as e:
        return {"error": str(e), "status": "failed"}

async def query_task_result(task_id: str) -> Dict[str, Any]:
    """
    查询RunningHub任务结果
    
    Args:
        task_id (str): 任务ID
        
    Returns:
        Dict[str, Any]: API响应结果，成功时code为0
    """
    headers = {
        "Content-Type": "application/json"
    }
    
    payload = {
        "apiKey": RUNNINGHUB_API_KEY,
        "taskId": task_id
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                RUNNINGHUB_RESULT_API_URL,
                headers=headers,
                json=payload
            ) as response:
                response_data = await response.json()
                return response_data
    except Exception as e:
        return {"error": str(e), "status": "failed"}

async def wait_for_task_completion(task_id: str, callback=None) -> Tuple[str, Dict]:
    """
    等待任务完成并返回状态
    
    Args:
        task_id (str): 任务ID
        callback (callable, optional): 状态更新回调函数
        
    Returns:
        Tuple[str, Dict]: 任务状态和任务结果
    """
    final_status = None
    result = None
    
    # 检查任务状态，直到完成或失败
    for attempt in range(MAX_STATUS_CHECK_ATTEMPTS):
        # 等待一段时间
        await asyncio.sleep(TASK_STATUS_CHECK_INTERVAL)
        
        # 查询任务状态
        status_result = await query_task_status(task_id)
        
        # 如果提供了回调函数，通知状态更新
        if callback:
            await callback({
                "task_id": task_id, 
                "attempt": attempt + 1, 
                "status_result": status_result
            })
        
        # 检查任务是否完成
        if status_result.get("code") == 0:
            task_status = status_result.get("data", "")
            if isinstance(task_status, str):
                task_status_str = task_status
            else:
                task_status_str = task_status.get("taskStatus", "")
            
            # 如果任务已完成或失败，尝试获取结果并退出循环
            if task_status_str in FINISHED_TASK_STATUSES:
                final_status = task_status_str
                if task_status_str not in ["FAILED", "ERROR"]:
                    # 查询结果
                    result = await query_task_result(task_id)
                break
    
    # 如果没有获取到最终状态，标记为超时
    if final_status is None:
        final_status = "TIMEOUT"
    
    return final_status, result

async def process_scene_prompts(prompts_dict: Dict[int, Dict[str, List[str]]], status_callback=None) -> Dict[str, Any]:
    """
    处理所有场景提示词并调用RunningHub API，采用真正的队列模式
    
    Args:
        prompts_dict (Dict): 按集数和场次组织的画面描述词字典
        status_callback (callable, optional): 状态更新回调函数
        
    Returns:
        Dict[str, Any]: 处理结果字典，包含各场景的API调用结果
    """
    # 最终结果
    results = {}
    
    # 收集所有提示词，形成待处理队列
    prompt_queue = deque()
    for episode, scenes in prompts_dict.items():
        for scene, prompts in scenes.items():
            for prompt in prompts:
                # 去除提示词中的#号和空格
                clean_prompt = prompt.replace('#', '').strip()
                if clean_prompt:
                    # 将提示词和位置信息加入队列
                    prompt_queue.append((int(episode), scene, clean_prompt))
    
    # 当前活跃任务 {task_id: (episode, scene, prompt, result_obj)}
    active_tasks = {}
    # 已完成任务数量
    completed_tasks = 0
    # 总任务数量
    total_tasks = len(prompt_queue)
    
    # 状态回调
    async def update_status(message):
        if status_callback:
            await status_callback({
                "message": message,
                "active_tasks": len(active_tasks),
                "completed": completed_tasks,
                "total": total_tasks,
                "queue_size": len(prompt_queue)
            })
    
    # 启动初始任务（最多MAX_CONCURRENT_TASKS个）
    while prompt_queue and len(active_tasks) < MAX_CONCURRENT_TASKS:
        episode, scene, prompt = prompt_queue.popleft()
        
        # 创建任务
        await update_status(f"创建任务中: {episode}-{scene} '{prompt[:30]}...'")
        create_result = await call_runninghub_workflow(prompt)
        
        # 结果对象
        result_obj = {
            "prompt": prompt,
            "create_result": create_result,
            "task_id": None,
            "status_result": None,
            "final_result": None,
            "status": None
        }
        
        # 检查是否创建成功
        task_id = None
        if create_result.get("code") == 0:
            task_id = create_result.get("data", {}).get("taskId")
            if task_id is None and "data" in create_result and "taskId" in create_result["data"]:
                task_id = create_result["data"]["taskId"]
            
            result_obj["task_id"] = task_id
            
            if task_id:
                # 加入活跃任务
                active_tasks[task_id] = (episode, scene, prompt, result_obj)
                await update_status(f"任务已创建: {task_id} (活跃任务: {len(active_tasks)})")
            else:
                # 创建失败但返回成功，添加到结果
                ensure_result_path(results, episode, scene, result_obj)
                completed_tasks += 1
        else:
            # 创建失败，可能是队列已满，重新放回队列末尾
            if create_result.get("code") == 421:  # TASK_QUEUE_MAXED
                # 任务队列已满，将任务重新放入队列末尾
                prompt_queue.append((episode, scene, prompt))
                await update_status("RunningHub队列已满，任务重新排队")
                # 等待一段时间再尝试
                await asyncio.sleep(5)
            else:
                # 其他错误，记录并标记为完成
                result_obj["status"] = "CREATE_FAILED"
                ensure_result_path(results, episode, scene, result_obj)
                completed_tasks += 1
                await update_status(f"任务创建失败: {create_result.get('msg', 'unknown error')}")
    
    # 主处理循环
    while active_tasks or prompt_queue:
        # 等待任何任务完成
        if active_tasks:
            # 创建任务完成监控
            monitoring_tasks = []
            for task_id, (episode, scene, prompt, result_obj) in active_tasks.items():
                # 创建异步任务，等待任务完成
                task = asyncio.create_task(wait_for_task_completion(
                    task_id, 
                    callback=lambda status: update_status(f"检查任务状态: {task_id}, 尝试: {status['attempt']}")
                ))
                monitoring_tasks.append((task_id, task))
            
            # 等待任意一个任务完成（使用as_completed）
            for future in asyncio.as_completed([task for _, task in monitoring_tasks]):
                try:
                    # 获取完成的任务结果
                    final_status, result = await future
                    
                    # 找到对应的task_id
                    completed_task_id = None
                    for tid, t in monitoring_tasks:
                        if t.done() and t._result == (final_status, result):
                            completed_task_id = tid
                            break
                    
                    if completed_task_id and completed_task_id in active_tasks:
                        # 获取任务信息
                        episode, scene, prompt, result_obj = active_tasks[completed_task_id]
                        
                        # 更新结果
                        result_obj["status"] = final_status
                        result_obj["final_result"] = result
                        
                        # 添加到结果字典
                        ensure_result_path(results, episode, scene, result_obj)
                        
                        # 从活跃任务中移除
                        del active_tasks[completed_task_id]
                        
                        # 更新完成数量
                        completed_tasks += 1
                        
                        # 状态更新
                        await update_status(
                            f"任务完成: {completed_task_id} ({completed_tasks}/{total_tasks}), 状态: {final_status}"
                        )
                        
                        # 启动下一个任务（如果有）
                        if prompt_queue:
                            new_episode, new_scene, new_prompt = prompt_queue.popleft()
                            
                            # 创建新任务
                            await update_status(f"创建新任务: {new_episode}-{new_scene} '{new_prompt[:30]}...'")
                            new_create_result = await call_runninghub_workflow(new_prompt)
                            
                            # 新结果对象
                            new_result_obj = {
                                "prompt": new_prompt,
                                "create_result": new_create_result,
                                "task_id": None,
                                "status_result": None,
                                "final_result": None,
                                "status": None
                            }
                            
                            # 检查是否创建成功
                            new_task_id = None
                            if new_create_result.get("code") == 0:
                                new_task_id = new_create_result.get("data", {}).get("taskId")
                                if new_task_id is None and "data" in new_create_result and "taskId" in new_create_result["data"]:
                                    new_task_id = new_create_result["data"]["taskId"]
                                
                                new_result_obj["task_id"] = new_task_id
                                
                                if new_task_id:
                                    # 加入活跃任务
                                    active_tasks[new_task_id] = (new_episode, new_scene, new_prompt, new_result_obj)
                                    await update_status(f"新任务已创建: {new_task_id} (活跃任务: {len(active_tasks)})")
                                else:
                                    # 创建失败但返回成功，添加到结果
                                    ensure_result_path(results, new_episode, new_scene, new_result_obj)
                                    completed_tasks += 1
                            else:
                                # 创建失败，可能是队列已满，重新放回队列开头
                                if new_create_result.get("code") == 421:  # TASK_QUEUE_MAXED
                                    prompt_queue.appendleft((new_episode, new_scene, new_prompt))
                                    await update_status("RunningHub队列已满，新任务重新排队")
                                    # 等待一段时间再尝试
                                    await asyncio.sleep(5)
                                else:
                                    # 其他错误，记录并标记为完成
                                    new_result_obj["status"] = "CREATE_FAILED"
                                    ensure_result_path(results, new_episode, new_scene, new_result_obj)
                                    completed_tasks += 1
                                    await update_status(f"新任务创建失败: {new_create_result.get('msg', 'unknown error')}")
                        
                        # 如果所有监控的任务都已完成，跳出当前循环
                        break
                
                except Exception as e:
                    await update_status(f"监控任务出错: {str(e)}")
            
            # 取消未完成的监控任务
            for _, task in monitoring_tasks:
                if not task.done():
                    task.cancel()
            
            # 如果没有活跃任务但还有队列中的任务，尝试启动新任务
            if not active_tasks and prompt_queue:
                continue
        
        # 如果没有活跃任务但队列中还有任务，尝试启动新任务
        elif prompt_queue:
            episode, scene, prompt = prompt_queue.popleft()
            
            # 创建任务
            await update_status(f"创建任务中: {episode}-{scene} '{prompt[:30]}...'")
            create_result = await call_runninghub_workflow(prompt)
            
            # 结果对象
            result_obj = {
                "prompt": prompt,
                "create_result": create_result,
                "task_id": None,
                "status_result": None,
                "final_result": None,
                "status": None
            }
            
            # 检查是否创建成功
            task_id = None
            if create_result.get("code") == 0:
                task_id = create_result.get("data", {}).get("taskId")
                if task_id is None and "data" in create_result and "taskId" in create_result["data"]:
                    task_id = create_result["data"]["taskId"]
                
                result_obj["task_id"] = task_id
                
                if task_id:
                    # 加入活跃任务
                    active_tasks[task_id] = (episode, scene, prompt, result_obj)
                    await update_status(f"任务已创建: {task_id} (活跃任务: {len(active_tasks)})")
                else:
                    # 创建失败但返回成功，添加到结果
                    ensure_result_path(results, episode, scene, result_obj)
                    completed_tasks += 1
            else:
                # 创建失败，可能是队列已满，重新放回队列开头
                if create_result.get("code") == 421:  # TASK_QUEUE_MAXED
                    prompt_queue.appendleft((episode, scene, prompt))
                    await update_status("RunningHub队列已满，任务重新排队")
                    # 等待一段时间再尝试
                    await asyncio.sleep(5)
                else:
                    # 其他错误，记录并标记为完成
                    result_obj["status"] = "CREATE_FAILED"
                    ensure_result_path(results, episode, scene, result_obj)
                    completed_tasks += 1
                    await update_status(f"任务创建失败: {create_result.get('msg', 'unknown error')}")
        
        # 等待一段时间，避免过于频繁检查
        if active_tasks or prompt_queue:
            await asyncio.sleep(2)
    
    await update_status(f"所有任务处理完成！共 {completed_tasks}/{total_tasks} 个任务")
    return results

def ensure_result_path(results, episode, scene, result_obj):
    """确保结果字典中有正确的路径"""
    episode_str = str(episode)
    if episode_str not in results:
        results[episode_str] = {}
    
    if scene not in results[episode_str]:
        results[episode_str][scene] = []
    
    results[episode_str][scene].append(result_obj) 