from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Image, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
import os
import aiohttp
import asyncio
import io
import uuid
from datetime import datetime
from app.api.stream_router import global_tasks_status

async def download_image(url):
    """下载图片为二进制数据"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.read()
            return None

async def generate_script_pdf(script_data, images_data, output_dir="app/static/pdf"):
    """生成包含剧本和图片的PDF文件"""
    os.makedirs(output_dir, exist_ok=True)
    pdf_filename = f"script_{uuid.uuid4()}.pdf"
    pdf_path = os.path.join(output_dir, pdf_filename)
    
    # 创建PDF文档
    doc = SimpleDocTemplate(pdf_path, pagesize=A4)
    styles = getSampleStyleSheet()
    
    # 自定义样式
    title_style = ParagraphStyle(
        'TitleStyle',
        parent=styles['Heading1'],
        fontSize=16,
        textColor=colors.darkblue,
        spaceAfter=12
    )
    
    section_style = ParagraphStyle(
        'SectionStyle',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=colors.darkblue,
        spaceAfter=10
    )
    
    # 准备内容
    story = []
    
    # 添加标题
    story.append(Paragraph("AI生成剧本与图片", title_style))
    story.append(Paragraph(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    story.append(Spacer(1, 0.5*inch))
    
    # 添加剧本内容
    story.append(Paragraph("剧本内容", section_style))
    story.append(Paragraph(script_data["full_script"], styles['Normal']))
    story.append(Spacer(1, 0.5*inch))
    
    # 添加图片和提示词
    story.append(Paragraph("场景图片", section_style))
    
    for episode, scenes in images_data.items():
        story.append(Paragraph(f"第{episode}集", styles['Heading3']))
        
        for scene, prompts in scenes.items():
            story.append(Paragraph(f"场景: {scene}", styles['Heading4']))
            
            for idx, prompt_data in prompts.items():
                # 添加提示词
                story.append(Paragraph(f"提示词: {prompt_data['prompt']}", styles['Normal']))
                
                # 处理图片
                image_url = prompt_data.get("image_url")
                if image_url:
                    try:
                        img_data = await download_image(image_url)
                        if img_data:
                            img = Image(io.BytesIO(img_data), width=5*inch, height=3*inch)
                            story.append(img)
                    except Exception as e:
                        story.append(Paragraph(f"图片加载失败: {str(e)}", styles['Normal']))
                
                story.append(Spacer(1, 0.25*inch))
    
    # 构建PDF
    doc.build(story)
    
    return {
        "file_path": pdf_path,
        "file_name": pdf_filename,
        "download_url": f"/static/pdf/{pdf_filename}"
    }

async def get_images_data(request_id: str) -> dict:
    """
    从RunningHub结果中提取图片URL数据
    
    Args:
        request_id: RunningHub请求ID
        
    Returns:
        字典格式的图片数据，按集数、场景和提示词组织
    """
    images_data = {}
    
    # 查找与该请求ID相关的所有任务
    related_tasks = {}
    for subtask_id, task_info in global_tasks_status.items():
        if task_info.get("request_id") == request_id:
            # 解析子任务ID，格式通常为: request_id_episode_scene_index
            # 例如: 3cbe5873-455c-4bb3-b188-e1b358116bf1_1_1-1_0
            parts = subtask_id.split("_")
            if len(parts) >= 4:
                episode = parts[1] 
                scene = parts[2]
                index = parts[3]
                
                # 初始化数据结构
                if episode not in images_data:
                    images_data[episode] = {}
                if scene not in images_data[episode]:
                    images_data[episode][scene] = {}
                
                # 获取任务结果
                result = task_info.get("result", {})
                
                # 提取图片URL - 根据RunningHub API的返回格式调整
                image_url = None
                if isinstance(result, dict):
                    # 从final_result中提取图片URL
                    final_result = result.get("final_result", {})
                    
                    # 方法1: 直接从data字段提取
                    if isinstance(final_result, dict) and "data" in final_result:
                        data = final_result.get("data", {})
                        # 可能的情况1: data是字典且包含imageUrl字段
                        if isinstance(data, dict) and "imageUrl" in data:
                            image_url = data.get("imageUrl")
                        # 可能的情况2: data是字典且包含outputs字段，outputs是列表
                        elif isinstance(data, dict) and "outputs" in data:
                            outputs = data.get("outputs", [])
                            if outputs and isinstance(outputs, list) and len(outputs) > 0:
                                # 第一个输出通常是图片
                                first_output = outputs[0]
                                if isinstance(first_output, dict) and "url" in first_output:
                                    image_url = first_output.get("url")
                        # 可能的情况3: data是包含url字段的字典
                        elif isinstance(data, dict) and "url" in data:
                            image_url = data.get("url")
                        # 可能的情况4: data直接是一个URL字符串
                        elif isinstance(data, str) and (data.startswith("http://") or data.startswith("https://")):
                            image_url = data
                    
                    # 方法2: 在整个结果中搜索URL模式
                    if not image_url:
                        # 递归搜索字典中的URL
                        def find_url_in_dict(d):
                            if not isinstance(d, dict):
                                return None
                            
                            for k, v in d.items():
                                # 检查是否是URL相关的键
                                if k.lower() in ["url", "imageurl", "image_url", "src", "source"]:
                                    if isinstance(v, str) and (v.startswith("http://") or v.startswith("https://")):
                                        return v
                                # 如果值是字典，递归搜索
                                elif isinstance(v, dict):
                                    url = find_url_in_dict(v)
                                    if url:
                                        return url
                                # 如果值是列表，遍历搜索
                                elif isinstance(v, list):
                                    for item in v:
                                        if isinstance(item, dict):
                                            url = find_url_in_dict(item)
                                            if url:
                                                return url
                            return None
                        
                        image_url = find_url_in_dict(final_result)
                
                # 保存提示词和图片URL
                images_data[episode][scene][index] = {
                    "prompt": result.get("prompt", "未知提示词"),
                    "image_url": image_url,
                    "status": result.get("status", "UNKNOWN")
                }
    
    return images_data
