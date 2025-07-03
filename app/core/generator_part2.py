import json
import httpx
import asyncio
from app.core.config import MODEL_NAME, API_VERSION, REQUEST_TIMEOUT
from app.core.config import EPISODE_TOKEN_LIMIT
from app.utils.text_utils import extract_title_and_directory
from app.utils.storage import save_partial_content
from typing import Optional, Callable, Awaitable

async def generate_episode(ep, genre, episodes, duration, full_script, API_KEY, API_URL, client_id=None, content_callback=None):
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

    #<Based on the detailed scene description above, generate an image prompt describing the environment, atmosphere, or key actions. Structure the prompt to include: descriptions of all characters in the scene, scene setting, color tone and lighting, style keywords and mood. Each image prompt should flow as a cohesive paragraph with logical and orderly content. Avoid abstract terms and bulleted descriptions. The prompt should be directly usable in AI drawing software to generate the corresponding image>

    <角色1>
    (<语气或动作>)
    <角色1的台词内容。>

    △<动作或环境变化，请详细描述>
    #<Based on the detailed scene description above, generate an image prompt describing the environment, atmosphere, or key actions. Structure the prompt to include: descriptions of all characters in the scene, scene setting, color tone and lighting, style keywords and mood. Each image prompt should flow as a cohesive paragraph with logical and orderly content. Avoid abstract terms and bulleted descriptions. The prompt should be directly usable in AI drawing software to generate the corresponding image>

    <角色2>
    (<语气或动作>)
    <角色2的台词内容。>

    △<其他角色的动作，或场景补充描述，请详细描述>
    #<Based on the detailed scene description above, generate an image prompt describing the environment, atmosphere, or key actions. Structure the prompt to include: descriptions of all characters in the scene, scene setting, color tone and lighting, style keywords and mood. Each image prompt should flow as a cohesive paragraph with logical and orderly content. Avoid abstract terms and bulleted descriptions. The prompt should be directly usable in AI drawing software to generate the corresponding image>

    ...

    场次1-n：<场景描述><时间描述>
    ------------------------------------------------
    **出场人物：**<角色n>；<角色n+1>；<其他角色>

    △<场景详细描述，描述环境、氛围或关键动作，请详细描述>

    #<Based on the detailed scene description above, generate an image prompt describing the environment, atmosphere, or key actions. Structure the prompt to include: descriptions of all characters in the scene, scene setting, color tone and lighting, style keywords and mood. Each image prompt should flow as a cohesive paragraph with logical and orderly content. Avoid abstract terms and bulleted descriptions. The prompt should be directly usable in AI drawing software to generate the corresponding image>

    <角色n>
    (<语气或动作>)
    <角色n的台词内容>

    △<动作或环境变化，请详细描述>
    #<Based on the detailed scene description above, generate an image prompt describing the environment, atmosphere, or key actions. Structure the prompt to include: descriptions of all characters in the scene, scene setting, color tone and lighting, style keywords and mood. Each image prompt should flow as a cohesive paragraph with logical and orderly content. Avoid abstract terms and bulleted descriptions. The prompt should be directly usable in AI drawing software to generate the corresponding image>

    <角色n+1>
    (<语气或动作>)
    <角色n+1的台词内容。>

    △<其他角色的动作，或场景补充描述，请详细描述>

    #<Based on the detailed scene description above, generate an image prompt describing the environment, atmosphere, or key actions. Structure the prompt to include: descriptions of all characters in the scene, scene setting, color tone and lighting, style keywords and mood. Each image prompt should flow as a cohesive paragraph with logical and orderly content. Avoid abstract terms and bulleted descriptions. The prompt should be directly usable in AI drawing software to generate the corresponding image>

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
    绘画风格为写实风格，不要使用卡通人物。
    """
    
    try:
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                print(f"第{ep}集 - 尝试 {retry_count+1}/{max_retries}")
                async with httpx.AsyncClient(timeout=120.0) as client:
                    # 创建请求但不等待整个响应完成
                    print(f"开始发送API请求到: {API_URL}")
                    async with client.stream(
                        "POST", 
                        API_URL,
                        headers={"Authorization": f"Bearer {API_KEY}"},
                        json={
                            "model": "claude-3-7-sonnet-20250219",
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.7,
                            "max_tokens": EPISODE_TOKEN_LIMIT,
                            "stream": True
                        },
                        timeout=120.0
                    ) as response:
                        # 检查响应状态
                        print(f"收到API响应，状态码: {response.status_code}")
                        if response.status_code != 200:
                            print(f"API错误响应: {response.status_code}")
                            error_text = await response.text()
                            print(f"错误详情: {error_text}")
                            raise Exception(f"API请求失败，状态码: {response.status_code}")
                        
                        # 处理流式响应
                        episode_content = ""
                        chunk_count = 0
                        
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                try:
                                    # 解码为文本
                                    text_chunk = chunk.decode('utf-8')
                                    # 处理每行数据
                                    for line in text_chunk.split('\n'):
                                        if line.startswith('data: '):
                                            if line.strip() == 'data: [DONE]':
                                                print("收到[DONE]标记，流式响应完成")
                                                break
                                            
                                            try:
                                                data = json.loads(line[6:])
                                                delta = ""
                                                
                                                # 提取文本增量
                                                if "choices" in data and data["choices"]:
                                                    delta = data["choices"][0].get("delta", {}).get("content", "")
                                                elif "type" in data and data.get("type") == "content_block_delta":
                                                    delta = data.get("delta", {}).get("text", "")
                                                
                                                if delta:
                                                    # 重要：同时累积内容
                                                    episode_content += delta
                                                    chunk_count += 1
                                                    
                                                    if chunk_count % 10 == 0:
                                                        print(f"已接收{chunk_count}个文本块，当前内容长度: {len(episode_content)}")
                                                    
                                                    if content_callback:
                                                        await content_callback(delta)
                                            except json.JSONDecodeError as je:
                                                print(f"JSON解析错误: {str(je)}, 行内容: {line[:50]}...")
                                except Exception as e:
                                    print(f"处理流式数据块出错: {str(e)}")
                        
                        print(f"第{ep}集内容生成完成，总长度: {len(episode_content)} 字符")
                        return episode_content
                        
            except httpx.TimeoutException as e:
                print(f"API请求超时: {str(e)}")
                retry_count += 1
                if retry_count >= max_retries:
                    if content_callback:
                        await content_callback("\n\n[生成超时，请刷新重试]")
                    return "生成超时，请刷新重试"
                await asyncio.sleep(2)
            except Exception as e:
                print(f"第{ep}集生成请求出错: {str(e)}")
                retry_count += 1
                if retry_count >= max_retries:
                    if content_callback:
                        await content_callback(f"\n\n[生成失败: {str(e)}]")
                    return f"生成失败: {str(e)}"
                await asyncio.sleep(2)
    except Exception as e:
        print(f"第{ep}集生成过程中发生严重错误: {str(e)}")
        if content_callback:
            await content_callback(f"\n\n[系统错误: {str(e)}]")
        return f"系统错误: {str(e)}" 