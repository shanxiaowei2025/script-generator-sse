import asyncio
import uuid
import time
import json
import re
from typing import Dict, Any, List, Optional, Set
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi import status

from app.api.models import (
    RunningHubProcessRequest,
    RunningHubTaskStatusRequest,
    RunningHubTaskResultRequest
)
from app.utils.storage import load_generation_state
from app.utils.text_utils import extract_scene_prompts as extract_prompts
from app.utils.runninghub_api import (
    query_task_status,
    query_task_result,
    cancel_runninghub_task,
    cancelled_task_ids
)
from app.services.task_queue import (
    format_sse_event,
    global_event_queues,
    global_task_queue,
    global_tasks_status,
    global_request_metadata,
    global_runninghub_tasks,
    script_to_image_task_mapping,
    ensure_global_worker_running,
    active_streaming_tasks
)
from app.services.image_processing import download_and_report_images


async def process_prompts_service(request: RunningHubProcessRequest) -> StreamingResponse:
    """将剧本中提取的画面描述词发送到RunningHub API处理"""
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
                download_tasks = []
                
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
                                    if download_tasks:
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
                                if download_tasks:
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
                                if download_tasks:
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


async def get_task_status_service(request: RunningHubTaskStatusRequest) -> Dict[str, Any]:
    """查询RunningHub任务状态服务"""
    if not request.task_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "缺少任务ID"}
        )
    
    try:
        # 调用RunningHub API查询任务状态
        status_result = await query_task_status(request.task_id)
    
        # 只返回状态信息
        return {
            "task_id": request.task_id,
            "status": status_result
        }
    
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"查询任务状态出错: {str(e)}"}
        )


async def get_task_result_service(request: RunningHubTaskResultRequest) -> Dict[str, Any]:
    """查询RunningHub任务结果服务"""
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


async def cancel_task_service(task_id: str) -> Dict[str, Any]:
    """取消任务服务"""
    if task_id in active_streaming_tasks:
        # 取消流式生成任务
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
    
    # 否则，尝试取消RunningHub任务
    return await cancel_runninghub_task_service(task_id)


async def cancel_runninghub_task_service(request_id: str) -> Dict[str, Any]:
    """取消任务ID相关的所有RunningHub任务并从队列中删除待处理任务"""
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