import os
import aiohttp
import asyncio
from typing import List, Dict, Any
import aiofiles
import re
from urllib.parse import urlparse
from datetime import datetime
from app.core.config import IMAGES_DIR
from app.utils.storage import get_partial_content
from app.utils.text_utils import extract_scene_prompts

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
    
    # 打印全部场次信息，帮助调试
    print(f"任务结果中包含以下场次:")
    for scene in results.keys():
        print(f"  场次: {scene}")
    
    # 获取当前集数的数字形式
    episode_num = int(episode) if str(episode).isdigit() else (
        int(episode.replace("第", "").replace("集", "")) 
        if "集" in str(episode) else 1
    )
    
    # 从剧本中提取真实场次编号
    scene_mapping = {}
    if script_task_id:
        # 尝试从剧本中提取场次映射
        scene_mapping = await extract_scene_mapping_from_script(script_task_id, episode_num)
        if scene_mapping:
            print(f"成功从剧本中提取第{episode_num}集的场次映射:")
            for simple_id, full_id in scene_mapping.items():
                print(f"  {simple_id} -> {full_id}")
    
    # 对场景进行排序并重新映射场次编号
    scene_keys = list(results.keys())
    
    # 尝试提取场次的数字编号进行排序
    def extract_scene_number(scene_key):
        # 首先尝试提取场次编号 (如 "场次2-4")
        scene_match = re.search(r'场次(\d+)-(\d+)', scene_key)
        if scene_match:
            # 使用第二部分编号作为排序依据
            return int(scene_match.group(2))
        # 尝试提取纯数字编号
        digits_match = re.search(r'(\d+)', scene_key)
        if digits_match:
            return int(digits_match.group(1))
        # 没有找到数字编号，返回一个默认值
        return 999  # 无数字场景放在最后
    
    # 对场景键进行排序
    sorted_scene_keys = sorted(scene_keys, key=extract_scene_number)
    
    # 创建新的场次编号映射 - 优先使用从剧本提取的编号，否则按顺序重新编号
    scene_number_mapping = {}
    for idx, original_scene in enumerate(sorted_scene_keys, 1):
        # 从场景名称中提取简单编号
        scene_num_match = re.search(r'(\d+)$', original_scene.replace("场次", ""))
        simple_num = scene_num_match.group(1) if scene_num_match else str(idx)
        
        # 优先使用从剧本提取的真实场次编号
        if simple_num in scene_mapping:
            script_scene_id = scene_mapping[simple_num]
            new_scene_id = script_scene_id if script_scene_id.startswith("场次") else f"场次{script_scene_id}"
            scene_number_mapping[original_scene] = new_scene_id
            print(f"使用剧本场次编号: {original_scene} -> {new_scene_id}")
        elif f"场次{simple_num}" in scene_mapping:
            script_scene_id = scene_mapping[f"场次{simple_num}"]
            scene_number_mapping[original_scene] = script_scene_id
            print(f"使用剧本场次编号: {original_scene} -> {script_scene_id}")
        else:
            # 没有找到对应的剧本场次编号，使用默认编号方式 "场次{集数}-{序号}"
            new_scene_id = f"场次{episode_num}-{idx}"
            scene_number_mapping[original_scene] = new_scene_id
            print(f"使用默认场次编号: {original_scene} -> {new_scene_id}")
    
    # 下载每个场景的图片，使用重新编号的场次
    for scene in sorted_scene_keys:
        try:
            # 使用重新映射的场次编号
            scene_formatted = scene_number_mapping.get(scene, f"场次{episode_num}-1")
            
            download_results["scenes"][scene] = {}
            
            # 打印提示词索引，便于调试
            prompts = results[scene]
            prompt_indices = list(prompts.keys())
            print(f"场景 {scene} (新编号: {scene_formatted}) 的提示词索引: {prompt_indices}")
            
            # 如果提示词索引为空，记录错误并继续处理下一个场次
            if not prompt_indices:
                print(f"警告: 场次 {scene} 没有提示词索引")
                download_results["scenes"][scene]["error"] = "没有提示词索引"
                continue
            
            for prompt_idx, prompt_data in prompts.items():
                # 处理该提示词下的所有图片
                image_urls = []
                local_paths = []
                
                try:
                    if "final_result" in prompt_data and "data" in prompt_data["final_result"]:
                        data_items = prompt_data["final_result"]["data"]
                        print(f"场次 {scene} 提示词 {prompt_idx} 有 {len(data_items)} 个图片")
                        
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
                                
                                print(f"准备下载图片: {save_filename}")
                                
                                # 下载图片
                                downloaded_path = await download_image(url, save_path)
                                
                                if downloaded_path:
                                    local_paths.append(downloaded_path)
                                    download_results["total_downloaded"] += 1
                                else:
                                    download_results["download_errors"] += 1
                    else:
                        error_msg = f"提示词 {prompt_idx} 没有final_result或data字段"
                        print(error_msg)
                        download_results["scenes"][scene][prompt_idx] = {"error": error_msg}
                except Exception as e:
                    error_msg = f"处理提示词 {prompt_idx} 时出错: {str(e)}"
                    print(error_msg)
                    download_results["download_errors"] += 1
                    download_results["scenes"][scene][prompt_idx] = {"error": error_msg}
                
                # 记录下载结果
                download_results["scenes"][scene][prompt_idx] = {
                    "prompt": prompt_data.get("prompt", ""),
                    "image_urls": image_urls,
                    "local_paths": local_paths
                }
        except Exception as e:
            error_msg = f"处理场次 {scene} 时出错: {str(e)}"
            print(error_msg)
            download_results["scenes"][scene] = {"error": error_msg}
            download_results["download_errors"] += 1
    
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

async def extract_scene_mapping_from_script(script_task_id: str, episode: int) -> Dict[str, str]:
    """
    从剧本中提取场次编号映射
    
    Args:
        script_task_id: 脚本任务ID
        episode: 集数
        
    Returns:
        Dict: 映射字典，格式为 {简化场次编号: 真实场次编号}
    """
    try:
        # 获取剧本内容
        script_content = get_partial_content(script_task_id, episode)
        if not script_content:
            print(f"警告: 无法找到第{episode}集的剧本内容")
            return {}
        
        # 提取所有场次
        scene_prompts = extract_scene_prompts(script_content)
        if not scene_prompts or episode not in scene_prompts:
            print(f"警告: 从剧本中未找到第{episode}集的场次信息")
            return {}
        
        # 创建场次映射
        scene_mapping = {}
        
        # 读取场次格式是 "X-Y" 的场次信息
        episode_scenes = scene_prompts[episode]
        
        # 打印找到的场次
        print(f"从剧本中找到第{episode}集的场次:")
        for scene_id in episode_scenes.keys():
            print(f"  {scene_id}")
            # 提取简化场次编号 (只保留第二部分数字)
            if "-" in scene_id:
                _, scene_num = scene_id.split("-")
                simple_id = scene_num
            else:
                simple_id = scene_id
            
            # 在映射中存储完整场次ID
            scene_mapping[simple_id] = scene_id
            # 同时存储带"场次"前缀的格式
            scene_mapping[f"场次{simple_id}"] = f"场次{scene_id}"
        
        return scene_mapping
    
    except Exception as e:
        print(f"从剧本提取场次映射时出错: {str(e)}")
        return {} 