import os
import pickle
import time
import json
import httpx
import asyncio
from typing import Dict, Optional, List, Any, Tuple
import uuid
from datetime import datetime
from enum import Enum
from app.core.config import (
    MODEL_NAME, API_VERSION, REQUEST_TIMEOUT,
    DIRECTORY_TOKEN_LIMIT, EPISODE_TOKEN_LIMIT, RESUME_TOKEN_LIMIT,
    GENERATION_STATES_DIR, 
    PARTIAL_CONTENTS_DIR,
    MINIO_ENABLED,
    SAVE_FILES_LOCALLY
)
from app.utils.text_utils import extract_title_and_directory
from app.utils.storage import save_partial_content
from app.utils.minio_storage import minio_client, get_state_object_name, get_content_object_name

# 导入从generator_part2.py和generator_part3.py
from app.core.generator_part2 import generate_episode
from app.core.generator_part3 import resume_episode_generation

async def generate_character_and_directory(
    genre, 
    episodes, 
    duration, 
    characters, 
    API_KEY, 
    API_URL,
    client_id=None,
    content_callback=None  # 添加回调函数参数
):
    print(f"\n==== 生成角色表和目录 ====")
    
    prompt = f"""
    [角色]
    你是一位AI短剧生成器，专门自动输出{genre}题材短剧内容。

    [任务]
    根据预设参数自动生成以下内容：
    1. 完整角色开发表
    2. 1-{episodes}集完整目录
    
    [预设参数]
    题材：{genre}
    集数：{episodes}
    每集时长：{duration}

    [要求]
    1. 生成剧名
    2. 角色开发
    3. 目录生成

    [角色开发]
    1.创建或更新主要角色和次要角色的详细信息表格：
    | 角色名称 | 身份定位|外貌特征（简洁直观）| 性格特征| 当前目标与动机 | 冲突点/爽点|
    |----------  |---------- |----------------------  |---------- |----------------    |------------    |
    | 角色1   | ...      | ...                 | ...      | ...              | ...            |
    | 角色2   | ...       | ...                | ...      | ...              | ...            | 
    | ...       | ...        | ...                | ...     | ...              | ...            |
            

    2.角色开发要求：
    -**简单角色关系**：主要角色性格鲜明，定位明确（如主角战神、反派卑鄙小人）。
    -**外貌与性格突出**：确保角色的外貌特征、生理或心理特点鲜明易记，适合短剧快节奏表现。
    -**目标与动机明确**：每个主要角色需有当前明确的目标与动机，与主线剧情产生紧密联系。
    -**冲突与爽感加持**：在角色间埋设矛盾点，为后续剧情的爽点或反转提供支撑。
    -**互动潜力高**：角色之间的关系设定需为对话和冲突提供机会，强化故事紧张感或轻松感。
    -**人物角色如下，剧本要包含所有角色：
    以下信息分别是人物名字、性别、年龄 
    {characters}

    [目录]
    1.根据短剧设定并按照短剧目录模板生成完整的短剧分集目录。短剧目录=
    **短剧分集目录**
    第01集<Text>
    第02集<Text>
    ...
    第n集<Text>
    2.确认分集结构是否符合选定的叙事节奏

    目录要求：
    -按照每集{duration}分钟时长，预计会创作{episodes}集
    -目录内容与整体故事情节保持一致，确保每集都有明确的冲突或爽点
    -每集的情节设置清晰简洁，避免冗长背景铺垫
    -必须完整输出所有集数目录
    -不需要生成每集简介，只生成目录
    """
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "anthropic-version": API_VERSION
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": DIRECTORY_TOKEN_LIMIT,
        "temperature": 0.7,
        "stream": True
    }
    
    print(f"请求角色表和目录，模型: {payload['model']}")
    initial_content = ""
    
    try:
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                print(f"角色表和目录 - 尝试 {retry_count+1}/{max_retries}")
                async with httpx.AsyncClient(timeout=120.0) as client:
                    # 创建请求但不等待整个响应完成
                    async with client.stream(
                        "POST", 
                        API_URL,
                        headers={"Authorization": f"Bearer {API_KEY}"},
                        json={
                            "model": "claude-3-7-sonnet-20250219",
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.7,
                            "stream": True
                        },
                        timeout=120.0
                    ) as response:
                        # 检查响应状态
                        if response.status_code != 200:
                            print(f"API错误响应: {response.status_code}")
                            error_text = await response.text()
                            print(f"错误详情: {error_text}")
                            raise Exception(f"API请求失败，状态码: {response.status_code}")
                        
                        # 一定要使用这种方式处理流式响应
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                try:
                                    # 解码为文本
                                    text_chunk = chunk.decode('utf-8')
                                    # 处理每行数据
                                    for line in text_chunk.split('\n'):
                                        if line.startswith('data: '):
                                            if line.strip() == 'data: [DONE]':
                                                break
                                            
                                            data = json.loads(line[6:])
                                            delta = ""
                                            
                                            # 提取文本增量
                                            if "choices" in data and data["choices"]:
                                                delta = data["choices"][0].get("delta", {}).get("content", "")
                                            
                                            if delta:
                                                # 重要：同时累积内容
                                                initial_content += delta
                                                
                                                if content_callback:
                                                    await content_callback(delta)
                                except Exception as e:
                                    print(f"处理流式数据出错: {str(e)}")
                        
                        # 返回累积的内容
                        print(f"角色表和目录生成完成，累积内容长度: {len(initial_content)}")
                        return initial_content
            except Exception as e:
                print(f"角色表和目录生成请求出错 (尝试 {retry_count+1}/{max_retries}): {str(e)}")
                retry_count += 1
                if retry_count >= max_retries:
                    return "角色表和目录生成出错，但将继续生成剧本内容。"
                await asyncio.sleep(2)  # 等待2秒后重试
                
        if not initial_content:
            initial_content = "角色表和目录生成失败，但将继续生成剧本内容。"
    except Exception as e:
        print(f"角色表和目录生成出错: {str(e)}")
        initial_content = "角色表和目录生成出错，但将继续生成剧本内容。"
    
    print(f"角色表和目录生成完成，长度: {len(initial_content)} 字符")
    return initial_content 