import asyncio
import os
import re
from typing import Dict, Any, Optional
from fastapi.responses import JSONResponse
from fastapi import status

from app.utils.storage import load_generation_state
from app.utils.text_utils import extract_scene_prompts as extract_prompts
from app.utils.pdf_generator import create_script_pdf
from app.core.config import PDFS_DIR, IMAGES_DIR
from app.services.task_queue import script_to_image_task_mapping


async def generate_script_pdf_path_service(task_id: str, timeout: int = 60) -> JSONResponse:
    """生成剧本PDF文件并返回文件路径服务"""
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