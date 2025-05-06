import json
import httpx
import asyncio
from app.core.config import MODEL_NAME, API_VERSION, REQUEST_TIMEOUT
from app.core.config import DIRECTORY_TOKEN_LIMIT, EPISODE_TOKEN_LIMIT, RESUME_TOKEN_LIMIT
from app.utils.text_utils import extract_title_and_directory
from app.utils.storage import save_partial_content

# 导入从generator_part2.py和generator_part3.py
from app.core.generator_part2 import generate_episode
from app.core.generator_part3 import resume_episode_generation

async def generate_character_and_directory(genre, episodes, duration, characters, api_key, api_url):
    """生成角色表和目录
    
    注意：此函数不需要实现流式传输，因为初始内容（角色表和目录）是一次性发送给前端的。
    流式传输主要用于每集内容生成，以提供更好的实时反馈。
    """
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
        "Authorization": f"Bearer {api_key}",
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
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    async with client.stream("POST", api_url, json=payload, headers=headers) as response:
                        if response.status_code != 200:
                            print(f"角色表和目录生成失败: HTTP {response.status_code}")
                            retry_count += 1
                            if retry_count >= max_retries:
                                return f"角色表和目录生成失败，API响应错误: {response.status_code}"
                            await asyncio.sleep(2)  # 等待2秒后重试
                            continue
                        
                        print(f"开始接收角色表和目录内容流...")
                        async for line in response.aiter_lines():
                            if not line or not line.startswith("data: "):
                                continue
                            
                            try:
                                line = line.replace("data: ", "")
                                if line == "[DONE]":
                                    break
                                
                                chunk_data = json.loads(line)
                                delta = ""
                                
                                if "type" in chunk_data and chunk_data.get("type") == "content_block_delta":
                                    delta = chunk_data.get("delta", {}).get("text", "")
                                elif "choices" in chunk_data and chunk_data["choices"]:
                                    delta = chunk_data["choices"][0].get("delta", {}).get("content", "")
                                
                                if delta:
                                    initial_content += delta
                            except json.JSONDecodeError:
                                print(f"JSON解析错误，跳过此行: {line[:50]}...")
                                continue
                            except Exception as e:
                                print(f"处理角色表和目录响应时出错: {str(e)}")
                                continue
                        
                        # 成功获取完整内容
                        if initial_content:
                            print(f"角色表和目录 成功获取流式内容，长度: {len(initial_content)} 字符")
                            break
                        else:
                            print(f"角色表和目录 流式内容为空，重试")
                            retry_count += 1
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