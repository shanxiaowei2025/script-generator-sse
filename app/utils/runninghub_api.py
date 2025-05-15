import aiohttp
import asyncio
import json
from typing import Dict, List, Any, Tuple, Set, Deque
from collections import deque
from app.core.config import (
    RUNNINGHUB_CREATE_API_URL,
    RUNNINGHUB_STATUS_API_URL,
    RUNNINGHUB_RESULT_API_URL,
    RUNNINGHUB_CANCEL_API_URL,
    RUNNINGHUB_API_KEY,
    RUNNINGHUB_WORKFLOW_ID,
    RUNNINGHUB_NODE_ID
)

# 任务处理的最大并发数
MAX_CONCURRENT_TASKS = 3
# 任务状态检查之间的等待时间（秒）
TASK_STATUS_CHECK_INTERVAL = 15
# 最大等待次数
MAX_STATUS_CHECK_ATTEMPTS = 1000
# 完成或失败的任务状态
FINISHED_TASK_STATUSES = ["SUCCESS", "FINISHED", "COMPLETE", "COMPLETED", "FAILED", "ERROR"]

# 全局的取消任务集合，用于跟踪已取消的任务
cancelled_task_ids = set()

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

async def cancel_runninghub_task(task_id: str) -> Dict[str, Any]:
    """
    取消RunningHub任务
    
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
                RUNNINGHUB_CANCEL_API_URL,
                headers=headers,
                json=payload
            ) as response:
                response_data = await response.json()
                # 任务取消成功后，将任务ID添加到已取消集合
                if response_data.get("code") == 0 or "SUCCESS" in str(response_data.get("msg", "")):
                    cancelled_task_ids.add(task_id)
                    print(f"任务 {task_id} 已添加到取消集合，当前取消集合大小: {len(cancelled_task_ids)}")
                return response_data
    except Exception as e:
        return {"error": str(e), "status": "failed"}

async def wait_for_task_completion(task_id: str, callback=None, request_id=None) -> Tuple[str, Dict]:
    """
    等待任务完成并返回状态
    
    Args:
        task_id (str): 任务ID
        callback (callable, optional): 状态更新回调函数
        request_id (str, optional): 请求ID，用于检查请求级别的取消
        
    Returns:
        Tuple[str, Dict]: 任务状态和任务结果
    """
    final_status = None
    result = None
    
    print(f"开始等待任务完成: {task_id}, 请求ID: {request_id}")
    
    # 检查任务状态，直到完成或失败
    for attempt in range(MAX_STATUS_CHECK_ATTEMPTS):
        # 检查任务是否已被取消
        is_cancelled = False
        cancel_reason = ""
        
        # 方法1: 直接检查任务ID是否在已取消集合中
        if task_id in cancelled_task_ids:
            is_cancelled = True
            cancel_reason = "任务ID在取消列表中"
        
        # 方法2: 检查请求ID是否被标记为已取消
        if not is_cancelled and request_id:
            from app.api.stream_router import global_runninghub_tasks
            if request_id in global_runninghub_tasks and "CANCELLED_REQUEST" in global_runninghub_tasks[request_id]:
                is_cancelled = True
                cancel_reason = "请求已被整体取消"
                # 顺便把当前任务也加入取消列表
                cancelled_task_ids.add(task_id)
        
        if is_cancelled:
            print(f"检测到任务 {task_id} 已被取消({cancel_reason})，停止状态监听")
            final_status = "CANCELLED"
            return final_status, {"message": f"任务已取消: {cancel_reason}"}
            
        # 等待一段时间
        await asyncio.sleep(TASK_STATUS_CHECK_INTERVAL)
        
        # 初始化task_status_str变量，防止未定义错误
        task_status_str = "UNKNOWN"
        
        try:
            # 再次检查任务是否已被取消（在等待期间可能被取消）
            if task_id in cancelled_task_ids:
                print(f"检测到任务 {task_id} 已被取消，停止状态监听")
                final_status = "CANCELLED"
                return final_status, {"message": "任务已取消"}
            
            # 查询任务状态
            print(f"查询任务状态: {task_id}, 尝试: {attempt+1}/{MAX_STATUS_CHECK_ATTEMPTS}")
            status_result = await query_task_status(task_id)
            print(f"状态查询响应: {status_result}")
            
            # 如果API返回任务不存在，检查是否是因为已被取消
            if status_result.get("code") == 807 and "NOT_FOUND" in str(status_result.get("msg", "")):
                print(f"任务 {task_id} 不存在，可能已被取消")
                # 将任务添加到取消集合
                cancelled_task_ids.add(task_id)
                final_status = "CANCELLED"
                return final_status, {"message": "任务已被取消或不存在"}
            
            # 如果提供了回调函数，通知状态更新
            if callback:
                await callback({
                    "task_id": task_id, 
                    "attempt": attempt + 1, 
                    "status_result": status_result
                })
            
            # 检查任务是否完成
            if isinstance(status_result, dict) and status_result.get("code") == 0:
                task_status = status_result.get("data", {})
                
                # 处理不同的API响应格式
                if isinstance(task_status, str):
                    task_status_str = task_status
                    print(f"任务状态(字符串格式): {task_status_str}")
                elif isinstance(task_status, dict):
                    task_status_str = task_status.get("taskStatus", "UNKNOWN")
                    print(f"任务状态(字典格式): {task_status_str}")
                else:
                    print(f"未知的任务状态格式: {type(task_status)}, 值: {task_status}")
                
                # 如果任务已完成或失败，尝试获取结果并退出循环
                if task_status_str in FINISHED_TASK_STATUSES:
                    final_status = task_status_str
                    print(f"任务状态已完成或失败: {final_status}")
                    
                    if task_status_str not in ["FAILED", "ERROR"]:
                        # 查询结果
                        print(f"查询任务结果: {task_id}")
                        result_response = await query_task_result(task_id)
                        print(f"结果查询响应: {result_response}")
                        result = result_response
                    break
            else:
                print(f"API响应无效或错误: {status_result}")
                
        except Exception as e:
            print(f"查询任务状态时出错: {str(e)}")
            import traceback
            print(traceback.format_exc())
        
        print(f"查询任务状态: {task_id}, 尝试: {attempt+1}, 状态: {task_status_str}")
    
    # 如果没有获取到最终状态，标记为超时
    if final_status is None:
        final_status = "TIMEOUT"
        print(f"任务超时: {task_id}")
    
    print(f"任务完成: {task_id}, 最终状态: {final_status}")
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
    results = {}
    
    # 保存原始提示词字典，用于格式化结果
    original_prompts_dict = {}
    
    # 任务队列和状态管理
    task_queue = asyncio.Queue()
    active_tasks = []
    completed_tasks = 0
    total_tasks = 0
    
    # 统计任务总数并格式化结果结构
    for episode, scenes in prompts_dict.items():
        # 先将episode转为字符串
        episode_str = str(episode)
        # 检查是否已经包含"第"和"集"
        if episode_str.startswith("第") and episode_str.endswith("集"):
            episode_key = episode_str  # 已经是正确格式
        elif episode_str.isdigit() or episode_str.replace(".", "").isdigit():
            # 纯数字，添加前缀和后缀
            episode_key = f"{episode_str}"
        else:
            # 其他情况，保持不变
            episode_key = episode_str
        
        results[episode_key] = {}
        original_prompts_dict[episode_key] = {}
        
        for scene, prompts in scenes.items():
            # 格式化场景键为"场次X-X"
            scene_key = f"场次{scene}" if not str(scene).startswith("场次") else str(scene)
            results[episode_key][scene_key] = {}
            original_prompts_dict[episode_key][scene_key] = {}
            
            # 添加有效提示词到队列
            for idx, prompt in enumerate(prompts):
                clean_prompt = prompt.replace('#', '').strip()
                if clean_prompt:
                    # 保存原始提示词，用于后续格式化
                    original_prompts_dict[episode_key][scene_key][str(idx)] = clean_prompt
                    
                    total_tasks += 1
                    await task_queue.put({
                        "episode": episode,
                        "episode_key": episode_key,
                        "scene": scene,
                        "scene_key": scene_key,
                        "prompt_index": idx,
                        "prompt": clean_prompt
                    })
    
    # 状态回调
    async def update_status(message):
        if status_callback:
            await status_callback({
                "message": message,
                "active_tasks": active_tasks.copy(),  # 复制列表，避免异步修改问题
                "completed": completed_tasks,
                "total": total_tasks,
                "queue_size": task_queue.qsize()
            })
    
    # 处理任务函数
    async def process_task(task):
        try:
            print(f"开始处理任务: {task['episode_key']}, 场次{task['scene_key']}, 提示词{task['prompt_index']}")
            
            episode = task.get("episode")
            episode_key = task.get("episode_key") 
            scene = task.get("scene")
            scene_key = task.get("scene_key")
            prompt_index = task.get("prompt_index")
            prompt = task.get("prompt")
            
            # 调用RunningHub API创建任务
            create_result = await call_runninghub_workflow(prompt)
            
            # 提取task_id，如果不存在则使用默认值
            task_id = None
            if create_result and isinstance(create_result, dict) and "data" in create_result:
                task_id = create_result.get("data", {}).get("taskId")
            
            # 确保结果字典有正确的结构
            if episode_key not in results:
                results[episode_key] = {}
            if scene_key not in results[episode_key]:
                results[episode_key][scene_key] = {}
                
            # 保存任务结果的初始状态
            task_result = {
                "prompt": prompt,
                "create_result": create_result,
                "task_id": task_id,
                "status_result": None,
                "final_result": None,
                "status": "CREATED" if task_id else "FAILED"
            }
            
            # 如果任务创建成功，等待完成
            if task_id:
                print(f"任务创建成功: RunningHub ID={task_id}")
                start_time = asyncio.get_event_loop().time()
                final_status, final_result = await wait_for_task_completion(task_id)
                duration = asyncio.get_event_loop().time() - start_time
                print(f"任务完成: {task_id}, 状态: {final_status}, 用时: {duration:.2f}秒")
                
                # 更新任务结果
                task_result["status_result"] = final_status
                task_result["final_result"] = final_result
                task_result["status"] = "SUCCESS" if final_status in ["SUCCESS", "FINISHED", "COMPLETE", "COMPLETED"] else "FAILED"
                
                # 保存更新后的结果 - 确保使用字符串索引
                results[episode_key][scene_key][str(prompt_index)] = task_result
            else:
                # 保存失败结果 - 确保使用字符串索引
                results[episode_key][scene_key][str(prompt_index)] = task_result
            
            # 返回结果
            return task_result
            
        except Exception as e:
            print(f"处理任务时出错: {str(e)}")
            error_result = {
                "prompt": task.get("prompt", ""),
                "error": str(e),
                "status": "ERROR"
            }
            
            # 确保能正确保存错误结果
            episode_key = task.get("episode_key", "未知集数")
            scene_key = task.get("scene_key", "未知场次")
            prompt_index = task.get("prompt_index", 0)
            
            if episode_key not in results:
                results[episode_key] = {}
            if scene_key not in results[episode_key]:
                results[episode_key][scene_key] = {}
                
            results[episode_key][scene_key][str(prompt_index)] = error_result
            return error_result

    # 任务处理器
    async def worker():
        nonlocal completed_tasks
        while True:
            try:
                # 获取下一个任务
                task = await task_queue.get()
                
                # 添加到活动任务列表
                active_tasks.append(task)
                
                # 更新状态
                await update_status(f"正在处理 第{task['episode']}集 场次{task['scene']} 提示词{task['prompt_index']}...")
                
                # 处理任务
                result = await process_task(task)
                
                # 更新计数器和状态
                completed_tasks += 1
                active_tasks.remove(task)
                task_queue.task_done()
                
                # 更新状态
                # await update_status(f"已完成 {completed_tasks}/{total_tasks} 个任务，队列剩余 {task_queue.qsize()} 个")
                
                # 在任务处理后添加
                print(f"任务处理结果: {task['episode_key']}, 场次{task['scene_key']}, 提示词{task['prompt_index']}, 状态: {result.get('status', 'UNKNOWN')}")
                print(f"队列状态: 剩余{task_queue.qsize()}项, 完成{completed_tasks}/{total_tasks}")
                
            except Exception as e:
                print(f"Worker异常: {str(e)}")
                if task in active_tasks:
                    active_tasks.remove(task)
                task_queue.task_done()
    
    # 启动工作器任务
    workers = []
    for _ in range(MAX_CONCURRENT_TASKS):
        worker_task = asyncio.create_task(worker())
        workers.append(worker_task)
    
    # 等待所有任务完成
    await update_status(f"开始处理 {total_tasks} 个任务，最大并发 {MAX_CONCURRENT_TASKS}")
    await task_queue.join()
    
    # 取消所有工作器任务
    for worker_task in workers:
        worker_task.cancel()
    
    # 检查所有结果，确保格式正确
    for episode_key, episode_results in results.items():
        for scene_key, scene_results in episode_results.items():
            for prompt_index_str, result in scene_results.items():
                # 确保result包含所有必要字段
                if "prompt" not in result and episode_key in original_prompts_dict:
                    if scene_key in original_prompts_dict[episode_key]:
                        if prompt_index_str in original_prompts_dict[episode_key][scene_key]:
                            result["prompt"] = original_prompts_dict[episode_key][scene_key][prompt_index_str]
                
                # 添加状态字段
                if "status" not in result:
                    if "final_result" in result and result["final_result"]:
                        result["status"] = "SUCCESS"
                    elif "error" in result:
                        result["status"] = "ERROR"
                    else:
                        result["status"] = "UNKNOWN"
    
    # 启动队列监控任务
    async def monitor_queue():
        while not task_queue.empty() or active_tasks:
            print(f"队列监控: 剩余{task_queue.qsize()}项, 活动{len(active_tasks)}项, 完成{completed_tasks}/{total_tasks}")
            for i, task in enumerate(active_tasks):
                print(f"  活动任务 #{i+1}: 第{task['episode']}集, 场次{task['scene']}, 提示词{task['prompt_index']}")
            await asyncio.sleep(30)  # 每30秒报告一次

    monitor_task = asyncio.create_task(monitor_queue())
    
    return results

def ensure_result_path(results, episode, scene, result_obj):
    """确保结果字典中有正确的路径"""
    episode_str = str(episode)
    if episode_str not in results:
        results[episode_str] = {}
    
    if scene not in results[episode_str]:
        results[episode_str][scene] = []
    
    results[episode_str][scene].append(result_obj) 