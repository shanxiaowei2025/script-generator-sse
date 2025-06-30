import asyncio
import time
from typing import Dict, Any, Optional
import os

from app.services.task_queue import (
    format_sse_event,
    script_to_image_task_mapping,
    global_tasks_status
)
from app.utils.image_downloader import download_images_from_event


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