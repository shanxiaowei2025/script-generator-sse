import json
import httpx
import asyncio
from app.core.config import MODEL_NAME, API_VERSION, REQUEST_TIMEOUT
from app.core.config import EPISODE_TOKEN_LIMIT, RESUME_TOKEN_LIMIT
from app.utils.text_utils import extract_title_and_directory
from app.utils.storage import save_partial_content
from typing import Optional, Callable, Awaitable

async def generate_episode(ep, genre, episodes, duration, full_script, API_KEY, API_URL, client_id=None, content_callback: Optional[Callable[[str], Awaitable[bool]]] = None):
    """生成单集内容"""
    print(f"\n==== 生成第{ep}集剧本 ====")
    
    # 检查API设置
    if not API_KEY or not API_URL:
        print(f"错误: 缺少API设置。API_KEY: {'已设置' if API_KEY else '未设置'}, API_URL: {'已设置' if API_URL else '未设置'}")
        return f"生成失败: API密钥或URL未正确设置。"
    
    prompt = f"""
    [角色]
    你是一位AI短剧生成器，专门自动输出{genre}题材短剧内容。

    [技能]
    - 熟悉爽文套路：懂得如何满足观众对"爽"的需求。
    - 快速制造冲突与反转：抓住短时间内吸引观众的关键。
    - 冲突设计技巧：通过台词、场景或角色行为设计强烈的戏剧冲突，比如"被扇巴掌""屌丝逆袭"。
    - 多重高潮设置：在有限的时间内设置高频反转，每一集都能吸引观众继续观看。
    - 情节简化：能够在短时间内讲好一个完整的故事，去掉冗长的铺垫和复杂的背景设定。
    - 台词精准化：台词语言直白、生动、有冲击力，同时能直接推动剧情发展。
    - 制造情绪起伏：通过角色遭受羞辱、迅速翻盘或逆袭的桥段调动观众情绪。
    - 目标导向写作：明确观众看短剧是为了"解压""娱乐"，抓住这种需求设计剧情。

    [任务]
    根据预设参数自动生成以下内容：
    1.每集详细剧本（符合分集撰写要求）
    2.基于以下已有内容，请仅生成第{ep}集的完整剧本内容。

    [预设参数]
    题材：{genre}
    集数：{episodes}
    每集时长：{duration}

    [AI生成初始短剧创作方案]
    完完全考虑创作方案要求里的创作方式，将生成一份完整的创作建议方案，
    **1.基础信息**
    作品名称：<基于主题生成引人入胜的标题>
    写作视角：<用户选择的视角>
    语言风格：<根据基调确定>

    **2.时空背景**
    <根据类型和主题生成合适的背景设定>

    **3.叙事结构**
    <根据类型和主题推荐合适的结构>

    **4.故事核心**
    <根据主题设计核心冲突和情节架构>

    **5.结局设计**
    <根据剧本题材类型设计具体结局>

    创作方案要求：
    -**剧情冲突与爽点设计**
    -每个核心情节必须包含强烈的戏剧冲突，如"羞辱与反击""逆境与逆袭""背叛与复仇"。
    -每集设置至少两个爽点（如强势逆袭、反派被扇巴掌、角色能力爆发）。
    -强调情感波动：通过矛盾激化让观众代入角色的情绪高低起伏。
    -**叙事节奏与情节设计**
    -情节必须采用快速节奏和跳跃式叙事，避免冗长的背景铺垫。
    -强调情节反转与惊喜：每集结尾设置悬念或反转，吸引观众继续观看。
    -故事中应包含"低谷—反击—高潮"的叙事波形，以层层叠加的爽点推进剧情。
    -**结局张力**
    -结局设计必须具备冲突的终极爆发点或情感升华。
    -如果是开放结局，设置能让观众产生讨论的悬念或思考点。
    -**观众需求导向**
    -故事始终围绕观众的情绪需求展开，满足"解压""娱乐"或"代入感"的观赏目的。
    -每一集都应能独立成为一个爽点场景，同时推进整体故事。

    [分集撰写]
    1.根据短剧[剧本设定]、分集目录、情节和开发的角色撰写用户指定集数的短剧内容。并将内容按照短剧[剧本格式]输出。
    2.检查点1：确保每一集都符合整体节奏和主题，体现短剧特有的冲突与爽点。
    3.检查点2：请必须、严格、完整的遵循分集要求里的写作方式。
    4.检查点3：按照指定集数要求写出完整剧本

    分集要求：
    -**时长与字数**：每集剧本内容不得少于800字。
    -**情节设计**：
    -剧情迅速展开，开场30秒内引爆核心冲突，如主人公被羞辱或逆袭翻盘。
    -强调高强度冲突，避免冗长铺垫，直接进入高潮。
    -每集围绕单一核心冲突或情绪点展开，节奏明快，避免复杂背景交代。
    -故事采用"爽文"模板，突出情绪波动与掌嘴量。
    -常见桥段包括"战神归来""霸道总裁""屌丝逆袭"等题材，吸引观众注意力。
    -剧情围绕角色的冲突和关系发展展开。
    -**结构与形式**：
    -每集剧情结构简单，上一集尾声作为下一集的开场。
    -人物设定简洁直接，主角形象鲜明（如战神、霸总、傻白甜），反派用于制造爽点或推动剧情。
    -**高强度冲突和快速节奏**：
    -剧情迅速展开，没有冗长铺垫，直接进入高潮。
    -"1分钟8个反转""打点""留钩子""节奏快""情绪强",不注重叙事逻辑
    -开场30秒内引爆核心矛盾，例如主人公被羞辱、逆袭或遭遇冲突。
    -**借鉴网络小说**：
    -以"爽文"模板为基础，利用掌嘴量和快速情绪波动引发观众共鸣。
    -常用套路包括反复被扇巴掌、逆袭翻盘等。
    -**固定桥段与套路**：
    -熟练运用"战神归来""霸道总裁"等经典题材。
    -结合网络流行梗，营造轻松或爽快氛围。
    -**多梗叠加与跳戏**：
    -大量使用热门网络梗或反转梗。
    -采用跳跃式叙事，不拘泥于场景过渡，快速切换到高潮场景。
    -**短小精悍的叙事结构**：
    -每集围绕核心冲突设计，无复杂分支情节。
    -每集以上一集尾声作为开场延续，形成连贯紧凑的叙事节奏。
    -**视觉化与画面冲击**
    -故事设计应包含强烈的视觉冲击场景，如激烈的动作、极端环境或情感爆发。
    -台词、场景和动作描写应生动鲜明，强调画面感，例如：
    -"反派跪地求饶，主角冷笑不屑。"
    -"风雪中，主角一步步走向反派，眼神冰冷如刀。"
    -每个高潮情节都应配合一场强烈的戏剧化表演。

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
    
    [剧本基础信息]
    {extract_title_and_directory(full_script)}
    
    [最近剧情]
    {full_script[-1500:]}

    [重要提示]
    必须严格使用上方[剧本基础信息]中的剧名和分集目录。
    严禁更改剧名或分集标题。
    当前生成第{ep}集内容。
    请确保生成完整的剧本。
    """
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "anthropic-version": API_VERSION
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": EPISODE_TOKEN_LIMIT,
        "temperature": 0.7,
        "stream": True
    }
    
    print(f"请求第{ep}集内容，模型: {payload['model']}")
    episode_content = ""
    
    try:
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                print(f"第{ep}集 - 尝试 {retry_count+1}/{max_retries}")
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    async with client.stream("POST", API_URL, json=payload, headers=headers) as response:
                        if response.status_code != 200:
                            print(f"第{ep}集生成失败: HTTP {response.status_code}")
                            retry_count += 1
                            if retry_count >= max_retries:
                                return f"第{ep}集生成失败，API响应错误: {response.status_code}"
                            await asyncio.sleep(2)  # 等待2秒后重试
                            continue
                        
                        print(f"开始接收第{ep}集内容流...")
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
                                    episode_content += delta
                                    
                                    # 使用缓冲区积累一定内容后再发送
                                    buffer_size = len(delta)
                                    buffer_content = delta
                                    send_buffer = False
                                    
                                    # 在以下情况发送缓冲区内容:
                                    # 1. 内容包含换行符
                                    # 2. 积累了足够多的字符(10个以上)
                                    if "\n" in buffer_content or buffer_size >= 10:
                                        send_buffer = True
                                    
                                    # 如果提供了client_id，实时保存部分内容
                                    # 但减少保存频率，每500个字符保存一次
                                    if client_id and len(episode_content) % 500 < buffer_size:
                                        save_partial_content(client_id, ep, episode_content)
                                    
                                    # 如果提供了回调函数，发送实时内容
                                    if content_callback and send_buffer:
                                        # 修改：检查回调函数返回值，如果返回False表示连接已断开
                                        callback_success = await content_callback(buffer_content)
                                        if callback_success is False:  # 显式检查False
                                            print(f"连接已断开，中止第{ep}集生成")
                                            return episode_content
                            except json.JSONDecodeError:
                                print(f"JSON解析错误，跳过此行: {line[:50]}...")
                                continue
                            except Exception as e:
                                print(f"处理第{ep}集响应时出错: {str(e)}")
                                # 修改：如果错误包含连接断开相关信息，立即终止
                                if "连接已断开" in str(e) or "close message" in str(e):
                                    print(f"检测到连接断开错误，中止第{ep}集生成")
                                    return episode_content
                                continue
                        
                        # 成功获取完整内容
                        if episode_content:
                            print(f"第{ep}集成功获取流式内容，长度: {len(episode_content)} 字符")
                            break
                        else:
                            print(f"第{ep}集内容为空，重试")
                            retry_count += 1
            except Exception as e:
                print(f"第{ep}集生成请求出错 (尝试 {retry_count+1}/{max_retries}): {str(e)}")
                # 修改：如果错误包含连接断开相关信息，立即终止不再重试
                if "连接已断开" in str(e) or "close message" in str(e):
                    print(f"检测到连接断开错误，不再重试")
                    return episode_content
                
                retry_count += 1
                if retry_count >= max_retries:
                    return f"第{ep}集生成出错: {str(e)}"
                # 修改：增加重试间隔时间
                await asyncio.sleep(5 * retry_count)  # 逐渐增加等待时间
                
        if not episode_content:
            episode_content = f"第{ep}集生成失败，但将继续生成后续内容。"
    except Exception as e:
        print(f"第{ep}集生成出错: {str(e)}")
        episode_content = f"第{ep}集生成出错: {str(e)}"
    
    print(f"第{ep}集生成完成，长度: {len(episode_content)} 字符")
    return episode_content 