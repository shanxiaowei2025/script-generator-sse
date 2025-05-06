import json
import httpx
import asyncio
from app.core.config import MODEL_NAME, API_VERSION, REQUEST_TIMEOUT
from app.core.config import RESUME_TOKEN_LIMIT
from app.utils.text_utils import extract_title_and_directory
from app.utils.storage import save_partial_content
from typing import Optional, Callable, Awaitable

async def resume_episode_generation(ep, genre, episodes, duration, full_script, partial_content, API_KEY, API_URL, client_id=None, content_callback: Optional[Callable[[str], Awaitable[bool]]] = None):
    """继续生成之前中断的单集内容"""
    print(f"\n==== 继续生成第{ep}集剧本 ====")
    print(f"已有内容长度: {len(partial_content)} 字符")
    
    # 检查API设置
    if not API_KEY or not API_URL:
        print(f"错误: 缺少API设置。API_KEY: {'已设置' if API_KEY else '未设置'}, API_URL: {'已设置' if API_URL else '未设置'}")
        return f"继续生成失败: API密钥或URL未正确设置。"
    
    # 分析部分内容，确定其结束位置是否合适
    # 查找最后一个完整段落或对话的结束位置
    last_paragraph_end = max(
        partial_content.rfind("\n\n"), 
        partial_content.rfind("。\n"),
        partial_content.rfind("」\n"),
        partial_content.rfind(".\n")
    )

    # 如果找到合适的断点，从那里截断，否则使用全部内容
    if last_paragraph_end > len(partial_content) * 0.5:  # 确保至少有一半内容保留
        existing_content = partial_content[:last_paragraph_end+1]
        print(f"找到合适的中断点，截断至{len(existing_content)}字符")
    else:
        existing_content = partial_content

    
    # 构建恢复生成的prompt
    prompt = f"""
    [角色]
    你是一位AI短剧生成器，专门自动输出{genre}题材短剧内容。
    
    [任务]
    现在的任务是继续完成第{ep}集的剧本，这个剧本在生成过程中被中断了。
    以下是已经生成的部分内容，请从中断处【继续】完成剧本，保持风格和情节的连贯性。
    不要重复已生成的内容，直接从中断处继续。
    
    [预设参数]
    题材：{genre}
    集数：{episodes}
    每集时长：{duration}
    
    [剧本基础信息]
    {extract_title_and_directory(full_script)}
    
    [最近剧情]
    {full_script[-1500:]}
    
    [当前集已生成内容]
    {existing_content}

    [剧本格式]：
    剧名：《剧名》
    集数：第1集<集名>
    ------------------------------------------------

    场次1-1：<场景描述><时间描述>
    ------------------------------------------------
    **出场人物：**<角色1>；<角色2>；<角色n>；<其他角色>

    △<场景详细描述，描述环境、氛围或关键动作，请详细描述>

    #<根据以上场景详细描述，描述环境、氛围或关键动作生成个画面提示词，要求按照以下描述结构生成这个画面的提示词：所有角色在画面中的描述，场景设定，色调与光影，风格关键词与氛围。每个画面提示词要连贯为一个段落，且逻辑通顺有条理，不要出现抽象词语，不要分点陈述，能够直接通过画面描述提示词通过Ai绘画软件生成对应的图>

    <角色1>
    (<语气或动作>)
    <角色1的台词内容。>

    △<动作或环境变化，请详细描述>
    #<根据以上动作或环境变化生成个画面提示词，要求按照以下描述结构生成这个画面的提示词：所有角色在画面中的描述，场景设定，色调与光影，风格关键词与氛围。每个画面提示词要连贯为一个段落，且逻辑通顺有条理，不要出现抽象词语，不要分点陈述，能够直接通过画面描述提示词通过Ai绘画软件生成对应的图>

    <角色2>
    (<语气或动作>)
    <角色2的台词内容。>

    △<其他角色的动作，或场景补充描述，请详细描述>
    #<根据其他角色的动作，或场景补充描述生成个画面提示词，要求按照以下描述结构生成这个画面的提示词：所有角色在画面中的描述，场景设定，色调与光影，风格关键词与氛围。每个画面提示词要连贯为一个段落，且逻辑通顺有条理，不要出现抽象词语，不要分点陈述，能够直接通过画面描述提示词通过Ai绘画软件生成对应的图>

    ...

    场次1-n：<场景描述><时间描述>
    ------------------------------------------------
    **出场人物：**<角色n>；<角色n+1>；<其他角色>

    △<场景详细描述，描述环境、氛围或关键动作，请详细描述>

    #<根据以上场景详细描述，描述环境、氛围或关键动作生成个画面提示词，要求按照以下描述结构生成这个画面的提示词：所有角色在画面中的描述，场景设定，色调与光影，风格关键词与氛围。每个画面提示词要连贯为一个段落，且逻辑通顺有条理，不要出现抽象词语，不要分点陈述，能够直接通过画面描述提示词通过Ai绘画软件生成对应的图>

    <角色n>
    (<语气或动作>)
    <角色n的台词内容>

    △<动作或环境变化，请详细描述>
    #<根据以上动作或环境变化生成个画面提示词，要求按照以下描述结构生成这个画面的提示词：所有角色在画面中的描述，场景设定，色调与光影，风格关键词与氛围。每个画面提示词要连贯为一个段落，且逻辑通顺有条理，不要出现抽象词语，不要分点陈述，能够直接通过画面描述提示词通过Ai绘画软件生成对应的图>

    <角色n+1>
    (<语气或动作>)
    <角色n+1的台词内容。>

    △<其他角色的动作，或场景补充描述，请详细描述>

    #<根据其他角色的动作，或场景补充描述生成个画面提示词，要求按照以下描述结构生成这个画面的提示词：所有角色在画面中的描述，场景设定，色调与光影，风格关键词与氛围。每个画面提示词要连贯为一个段落，且逻辑通顺有条理，不要出现抽象词语，不要分点陈述，能够直接通过画面描述提示词通过Ai绘画软件生成对应的图>

    (完)

    [重要提示]
    1. 请从上面【当前集已生成内容】的末尾处【直接继续】，不要重复已有内容，也不要重启新的场景。
    2. 严格遵循已有剧名和剧情设定。
    3. 剧本格式应与已有部分保持一致。
    4. 请确保生成完整的一集剧本。
    5. 当前生成第{ep}集内容。
    6. 当前生成内容有{len(existing_content)}字符，请根据当前内容继续生成，字符数不要超过20000。
    """
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "anthropic-version": API_VERSION
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": RESUME_TOKEN_LIMIT,
        "temperature": 0.7,
        "stream": True
    }
    
    print(f"请求继续生成第{ep}集内容，模型: {payload['model']}")
    continuation = ""
    
    try:
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                print(f"继续生成第{ep}集 - 尝试 {retry_count+1}/{max_retries}")
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    async with client.stream("POST", API_URL, json=payload, headers=headers) as response:
                        if response.status_code != 200:
                            print(f"继续生成第{ep}集失败: HTTP {response.status_code}")
                            retry_count += 1
                            if retry_count >= max_retries:
                                return f"继续生成第{ep}集失败，API响应错误: {response.status_code}"
                            await asyncio.sleep(2)  # 等待2秒后重试
                            continue
                        
                        print(f"开始接收第{ep}集续写内容流...")
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
                                    continuation += delta
                                    
                                    # 如果提供了client_id，实时保存部分内容
                                    if client_id:
                                        complete_content = existing_content + continuation
                                        save_partial_content(client_id, ep, complete_content)
                                    
                                    # 如果提供了回调函数，发送实时内容
                                    if content_callback:
                                        callback_success = await content_callback(delta)
                                        if callback_success is False:
                                            print(f"连接已断开，中止第{ep}集生成")
                                            return existing_content + continuation
                            except Exception as e:
                                print(f"处理第{ep}集续写响应时出错: {str(e)}")
                                if "连接已断开" in str(e) or "close message" in str(e):
                                    print(f"检测到连接断开错误，中止第{ep}集生成")
                                    return existing_content + continuation
                                continue
                        
                        # 成功获取完整内容
                        if continuation:
                            print(f"第{ep}集续写成功，续写内容长度: {len(continuation)} 字符")
                            break
                        else:
                            print(f"第{ep}集续写内容为空，重试")
                            retry_count += 1
            except Exception as e:
                print(f"继续生成第{ep}集请求出错 (尝试 {retry_count+1}/{max_retries}): {str(e)}")
                if "连接已断开" in str(e) or "close message" in str(e):
                    print(f"检测到连接断开错误，不再重试")
                    return existing_content + continuation
                
                retry_count += 1
                if retry_count >= max_retries:
                    return "继续生成出错，但将尝试完成剧本内容。"
                await asyncio.sleep(5 * retry_count)
                
        if not continuation:
            continuation = "生成中断，未能继续生成完整内容。"
    except Exception as e:
        print(f"继续生成第{ep}集出错: {str(e)}")
        continuation = f"生成出错: {str(e)}"
    
    # 组合已有内容和新生成的内容
    full_content = existing_content + continuation
    
    print(f"第{ep}集续写完成，总长度: {len(full_content)} 字符")
    return full_content 