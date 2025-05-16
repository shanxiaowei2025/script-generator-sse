import os
import aiohttp
import asyncio
from typing import List, Dict, Any
import aiofiles
import re
from urllib.parse import urlparse
from datetime import datetime
from app.core.config import IMAGES_DIR

# 默认图片保存目录
DEFAULT_IMAGE_DIR = IMAGES_DIR

async def download_image(url: str, save_path: str) -> str:
    """
    下载图片并保存到指定路径
    
    Args:
        url: 图片URL
        save_path: 保存路径
        
    Returns:
        str: 图片保存的完整路径
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    # 确保目录存在
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    
                    # 保存图片
                    async with aiofiles.open(save_path, 'wb') as f:
                        await f.write(await response.read())
                    
                    print(f"图片下载成功: {save_path}")
                    return save_path
                else:
                    print(f"下载图片失败，状态码: {response.status}, URL: {url}")
                    return None
    except Exception as e:
        print(f"下载图片出错: {str(e)}, URL: {url}")
        return None

async def download_task_images(task_result: Dict[str, Any], script_task_id: str = None, base_dir: str = None) -> Dict[str, Any]:
    """
    从任务结果中下载所有图片
    
    Args:
        task_result: 任务结果数据，包含嵌套的图片URL
        script_task_id: 脚本任务ID，用于创建保存目录
        base_dir: 基础保存目录，默认为app/storage/images
        
    Returns:
        Dict: 包含下载结果的字典
    """
    if base_dir is None:
        base_dir = DEFAULT_IMAGE_DIR
    
    # 确保基础目录存在
    os.makedirs(base_dir, exist_ok=True)
    
    # 提取任务基本信息
    runninghub_task_id = task_result.get("task_id", "unknown")
    episode = task_result.get("episode", "unknown")
    
    # 格式化集数为"第X集"格式
    if not str(episode).startswith("第"):
        episode_formatted = f"第{episode}集"
    else:
        episode_formatted = episode if episode.endswith("集") else f"{episode}集"
    
    # 提取场景信息
    results = task_result.get("results", {})
    download_results = {
        "task_id": runninghub_task_id,
        "script_task_id": script_task_id,
        "episode": episode_formatted,
        "scenes": {},
        "total_downloaded": 0,
        "download_errors": 0
    }
    
    # 使用脚本任务ID作为目录名
    task_dir = os.path.join(base_dir, script_task_id if script_task_id else runninghub_task_id)
    print(f"图片将保存到目录: {task_dir}")
    
    # 下载每个场景的图片
    for scene, prompts in results.items():
        # 格式化场景为"场次X-X"格式
        if not str(scene).startswith("场次"):
            scene_formatted = f"场次{scene}"
        else:
            scene_formatted = scene
        
        # 规范化场次编号 - 确保第一部分与当前集数一致
        if scene_formatted.startswith("场次") and "-" in scene_formatted:
            # 提取场次编号
            scene_match = re.search(r'场次(\d+-\d+)', scene_formatted)
            if scene_match:
                scene_number = scene_match.group(1)
                scene_parts = scene_number.split('-')
                
                # 确保场次编号第一部分与当前集数一致
                episode_num = int(episode) if str(episode).isdigit() else (
                    int(episode.replace("第", "").replace("集", "")) 
                    if "集" in str(episode) else 1
                )
                
                if len(scene_parts) == 2 and int(scene_parts[0]) != episode_num:
                    # 修正场次编号
                    corrected_scene = f"场次{episode_num}-{scene_parts[1]}"
                    print(f"规范化场次编号：原编号={scene_formatted}，新编号={corrected_scene} (集数={episode_formatted})")
                    scene_formatted = corrected_scene
            
        download_results["scenes"][scene] = {}
        
        # 打印提示词索引，便于调试
        prompt_indices = list(prompts.keys())
        print(f"场景 {scene} 的提示词索引: {prompt_indices}")
        
        for prompt_idx, prompt_data in prompts.items():
            # 处理该提示词下的所有图片
            image_urls = []
            local_paths = []
            
            try:
                if "final_result" in prompt_data and "data" in prompt_data["final_result"]:
                    data_items = prompt_data["final_result"]["data"]
                    
                    for idx, item in enumerate(data_items):
                        if "fileUrl" in item:
                            url = item["fileUrl"]
                            image_urls.append(url)
                            
                            # 获取图片扩展名
                            parsed_url = urlparse(url)
                            filename = os.path.basename(parsed_url.path)
                            file_ext = os.path.splitext(filename)[1]
                            
                            # 如果没有扩展名，添加默认扩展名
                            if not file_ext:
                                file_ext = ".png"
                            
                            # 使用新的命名规则：第X集_场次X-X_X_img.png
                            save_filename = f"{episode_formatted}_{scene_formatted}_{prompt_idx}_img{idx}{file_ext}"
                            save_path = os.path.join(task_dir, save_filename)
                            
                            # 下载图片
                            downloaded_path = await download_image(url, save_path)
                            
                            if downloaded_path:
                                local_paths.append(downloaded_path)
                                download_results["total_downloaded"] += 1
                            else:
                                download_results["download_errors"] += 1
                else:
                    print(f"提示词 {prompt_idx} 没有final_result或data字段")
            except Exception as e:
                print(f"处理提示词 {prompt_idx} 时出错: {str(e)}")
                download_results["download_errors"] += 1
            
            # 记录下载结果
            download_results["scenes"][scene][prompt_idx] = {
                "prompt": prompt_data.get("prompt", ""),
                "image_urls": image_urls,
                "local_paths": local_paths
            }
    
    return download_results

async def download_images_from_event(event_data: Dict[str, Any], script_task_id: str = None) -> Dict[str, Any]:
    """
    从事件数据中提取任务结果并下载图片
    
    Args:
        event_data: 事件数据，包含result字段
        script_task_id: 脚本任务ID，用于创建保存目录
        
    Returns:
        Dict: 下载结果
    """
    # 提取结果数据
    if "result" not in event_data:
        return {"status": "error", "message": "事件数据中没有result字段"}
    
    # 从事件数据获取runninghub_task_id作为备用存储目录名
    runninghub_task_id = event_data.get("runninghub_task_id", "unknown")
    
    # 首选使用script_task_id作为目录名
    task_id_for_dir = script_task_id if script_task_id else runninghub_task_id
    
    # 确保task_id_for_dir不为None或空字符串
    if not task_id_for_dir or task_id_for_dir == "unknown":
        # 尝试从任务ID中提取一个合理的标识符
        task_id = event_data.get("task_id", "")
        if task_id and "_" in task_id:
            # 使用请求ID部分作为目录名
            task_id_for_dir = task_id.split("_")[0]
        else:
            # 使用完整任务ID
            task_id_for_dir = task_id if task_id else "unknown_task"
    
    print(f"图片将使用目录: {task_id_for_dir}")
    
    # 下载图片
    download_result = await download_task_images(
        event_data["result"], 
        script_task_id=task_id_for_dir
    )
    
    return {
        "status": "success",
        "event_task_id": event_data.get("task_id"),
        "script_task_id": script_task_id,
        "storage_dir": task_id_for_dir,
        "download_result": download_result
    } 