// 确保这个函数在全局作用域定义
function displayScript(script) {
    const scriptOutput = document.getElementById('scriptOutput');
    scriptOutput.innerHTML = `<div class="alert alert-success">剧本生成成功！</div>
                             <div class="script-content">${script.replace(/\n/g, '<br>')}</div>`;
}

document.addEventListener('DOMContentLoaded', function() {
    // 获取DOM元素
    const scriptForm = document.getElementById('scriptForm');
    const scriptOutput = document.getElementById('scriptOutput');
    const loadingIndicator = document.getElementById('loading');
    const statusElement = document.getElementById('status');
    const progressContainer = document.getElementById('progressContainer');
    const progressBar = document.getElementById('progressBar');
    const initialContent = document.getElementById('initialContent');
    const episodeNavigation = document.getElementById('episodeNavigation');
    const episodesContainer = document.getElementById('episodesContainer');
    const defaultMessage = document.getElementById('defaultMessage');
    
    let currentTaskId = null; // 当前任务ID
    let currentEpisode = 0;
    let totalEpisodes = 0;
    let isGenerating = false;
    let generationAborted = false;
    let episodesContent = {};  // 存储已生成的各集内容
    
    // 表单提交处理
    scriptForm.addEventListener('submit', async function(event) {
        event.preventDefault();
        
        // 表单验证
        if (!validateForm()) {
            return;
        }
        
        // 重置UI状态
        resetUI();
        isGenerating = true;
        generationAborted = false;
        
        // 显示加载指示器
        loadingIndicator.style.display = 'block';
        defaultMessage.style.display = 'none';
        statusElement.style.display = 'block';
        statusElement.textContent = '正在准备生成...';
        statusElement.className = 'alert alert-info';
        
        // 获取表单数据
        const genre = document.getElementById('genre').value;
        const duration = document.getElementById('duration').value;
        const episodes = parseInt(document.getElementById('episodes').value);
        totalEpisodes = episodes;
        
        // 获取勾选的角色
        const characterCheckboxes = document.querySelectorAll('input[name="characters"]:checked');
        const characters = Array.from(characterCheckboxes).map(checkbox => checkbox.value);
        
        // 准备请求数据
        const requestData = {
            genre: genre,
            duration: duration,
            episodes: episodes,
            characters: characters
        };
        
        // 启动SSE连接
        initSSEConnection(requestData);
    });
    
    // 添加取消按钮 
    const submitButton = document.querySelector('button[type="submit"]');
    const buttonContainer = document.createElement('div');
    buttonContainer.className = 'd-flex justify-content-between';
    
    // 创建生成按钮（复制提交按钮）
    const generateButton = document.createElement('button');
    generateButton.type = 'submit';
    generateButton.className = 'btn btn-primary flex-grow-1';
    generateButton.textContent = submitButton.textContent;
    
    // 创建取消按钮
    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.id = 'cancelBtn';
    cancelButton.className = 'btn btn-danger ms-2';
    cancelButton.textContent = '取消';
    cancelButton.disabled = true;
    
    // 添加到容器
    buttonContainer.appendChild(generateButton);
    buttonContainer.appendChild(cancelButton);
    
    // 替换原来的按钮
    submitButton.parentNode.replaceChild(buttonContainer, submitButton);
    
    // 绑定取消按钮事件
    cancelButton.addEventListener('click', function() {
        if (currentTaskId) {
            cancelGeneration(currentTaskId);
        }
    });
    
    // SSE方式生成
    function initSSEConnection(requestData) {
        try {
            // 启用取消按钮
            const cancelBtn = document.getElementById('cancelBtn');
            if (cancelBtn) {
                cancelBtn.disabled = false;
            }
            
            // 使用Fetch API发送POST请求并处理SSE响应
            fetch('/api/stream/generate-script', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestData)
            })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP错误: ${response.status}`);
                }
                
                // 获取响应的Reader
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                
                // 用于累积SSE事件
                let buffer = '';
                
                // 处理流式数据
                function processStream({done, value}) {
                    // 流结束
                    if (done) {
                        console.log('流结束');
                        completeGeneration();
                        return;
                    }
                    
                    // 解码数据并添加到缓冲区
                    buffer += decoder.decode(value, { stream: true });
                    
                    // 按SSE格式分割事件
                    const events = buffer.split('\n\n');
                    buffer = events.pop() || ''; // 保留最后一个可能不完整的事件
                    
                    // 处理每个事件
                    for (const eventText of events) {
                        if (!eventText.trim()) continue;
                        
                        // 解析事件
                        const eventLines = eventText.split('\n');
                        let eventType = '';
                        let eventData = {};
                        
                        for (const line of eventLines) {
                            if (line.startsWith('event:')) {
                                eventType = line.replace('event:', '').trim();
                            } else if (line.startsWith('data:')) {
                                try {
                                    eventData = JSON.parse(line.replace('data:', '').trim());
                                } catch (e) {
                                    console.error('解析事件数据出错:', e);
                                }
                            }
                        }
                        
                        if (eventType && Object.keys(eventData).length > 0) {
                            // 处理事件
                            handleSSEEvent(eventType, eventData);
                        }
                    }
                    
                    // 继续读取
                    reader.read().then(processStream);
                }
                
                // 开始读取流
                reader.read().then(processStream);
            })
            .catch(error => {
                console.error('连接错误:', error);
                statusElement.textContent = '连接错误: ' + error.message;
                statusElement.className = 'alert alert-danger';
                completeGeneration();
            });
        } catch (error) {
            displayError('创建连接失败: ' + error.message);
            completeGeneration();
        }
    }
    
    // 取消生成
    function cancelGeneration(taskId) {
        fetch(`/api/stream/cancel/${taskId}`, {
            method: 'DELETE'
        })
        .then(response => response.json())
        .then(data => {
            console.log('取消响应:', data);
            statusElement.textContent = '已取消生成';
            statusElement.className = 'alert alert-warning';
            completeGeneration();
        })
        .catch(error => {
            console.error('取消生成出错:', error);
            statusElement.textContent = '取消生成失败: ' + error.message;
            statusElement.className = 'alert alert-danger';
        });
    }
    
    // 处理SSE事件
    function handleSSEEvent(eventType, data) {
        console.log('收到事件类型:', eventType, data);
        
        switch (eventType) {
            case 'task_id':
                currentTaskId = data.task_id;
                console.log('已获取任务ID:', currentTaskId);
                break;
                
            case 'status':
                statusElement.textContent = data.message;
                break;
                
            case 'progress':
                updateProgress(data.current, data.total);
                break;
                
            case 'initial_content':
                displayInitialContent(data.content);
                break;
                
            case 'episode_content_chunk':
                // 处理流式内容片段
                handleEpisodeChunk(data.episode, data.content, data.is_complete);
                break;
                
            case 'episode_content':
                displayEpisodeContent(data.episode, data.content);
                currentEpisode = data.episode;
                updateProgress(data.episode, totalEpisodes);
                break;
                
            case 'complete':
                statusElement.textContent = '剧本生成完成！';
                statusElement.className = 'alert alert-success';
                completeGeneration();
                break;
                
            case 'error':
                displayError(data.message);
                completeGeneration();
                break;
        }
    }
    
    // 完成生成
    function completeGeneration() {
        isGenerating = false;
        loadingIndicator.style.display = 'none';
        
        // 禁用取消按钮
        const cancelBtn = document.getElementById('cancelBtn');
        if (cancelBtn) {
            cancelBtn.disabled = true;
        }
        
        // 添加提取画面描述词按钮
        if (currentTaskId) {
            addExtractPromptsButton();
        }
    }
    
    // 处理流式内容片段
    function handleEpisodeChunk(episodeNumber, content, isComplete) {
        // 创建或获取该集的div
        let episodeDiv = document.getElementById(`episode-${episodeNumber}`);
        if (!episodeDiv) {
            episodeDiv = document.createElement('div');
            episodeDiv.id = `episode-${episodeNumber}`;
            episodeDiv.className = 'episode-content script-content mb-4';
            
            // 添加集标题
            const titleDiv = document.createElement('div');
            titleDiv.className = 'episode-title';
            titleDiv.innerHTML = `<h4>第${episodeNumber}集</h4>`;
            episodeDiv.appendChild(titleDiv);
            
            // 创建内容区域
            const contentDiv = document.createElement('div');
            contentDiv.id = `episode-content-${episodeNumber}`;
            contentDiv.className = 'episode-text';
            episodeDiv.appendChild(contentDiv);
            
            episodesContainer.appendChild(episodeDiv);
        }
        
        // 获取内容显示区域
        const contentDiv = document.getElementById(`episode-content-${episodeNumber}`);
        
        // 追加内容
        if (content) {
            // 格式化新内容并追加到现有内容
            const formattedContent = formatContent(content);
            contentDiv.innerHTML += formattedContent;
        }
        
        // 记录用户当前正在查看的集数
        const currentViewingEpisode = document.getElementById('episodeSelect') ? 
                                    parseInt(document.getElementById('episodeSelect').value) : episodeNumber;
        
        // 只有在用户没有主动选择查看其他集数时，才自动显示当前生成的集数
        if (!document.querySelector('.episode-content[style*="display: block"]') || 
            currentViewingEpisode === episodeNumber) {
            showEpisode(episodeNumber);
        }
        
        // 如果完成了，更新导航状态
        if (isComplete) {
            const indicator = document.getElementById(`indicator-${episodeNumber}`);
            if (indicator) {
                indicator.classList.add('completed');
                const completeIcon = indicator.querySelector('.complete-icon');
                if (completeIcon) {
                    completeIcon.classList.remove('d-none');
                }
            }
            
            // 保存内容
            episodesContent[episodeNumber] = contentDiv.innerHTML;
        }
    }
    
    // 更新进度条
    function updateProgress(current, total) {
        const percent = Math.round((current / total) * 100);
        progressContainer.style.display = 'block';
        progressBar.style.width = `${percent}%`;
        progressBar.textContent = `${percent}%`;
        progressBar.setAttribute('aria-valuenow', percent);
    }
    
    // 显示角色表和目录
    function displayInitialContent(content) {
        initialContent.innerHTML = formatContent(content);
        initialContent.style.display = 'block';
        
        // 创建导航
        createEpisodeNavigation(totalEpisodes);
    }
    
    // 创建分集导航
    function createEpisodeNavigation(episodes) {
        // 创建更灵活的导航结构
        let navHtml = '<div class="episode-nav-container mb-3">';
        
        // 添加集数选择下拉菜单
        navHtml += '<div class="d-flex justify-content-between align-items-center mb-2">';
        navHtml += '<select class="form-select episode-select me-2" id="episodeSelect">';
        for (let i = 1; i <= episodes; i++) {
            navHtml += `<option value="${i}">第${i}集</option>`;
        }
        navHtml += '</select>';
        
        // 添加上一集/下一集按钮
        navHtml += '<div class="btn-group">';
        navHtml += '<button class="btn btn-outline-primary" id="prevEpisode"><i class="bi bi-chevron-left"></i> 上一集</button>';
        navHtml += '<button class="btn btn-outline-primary" id="nextEpisode">下一集 <i class="bi bi-chevron-right"></i></button>';
        navHtml += '</div>';
        navHtml += '</div>';
        
        // 集数进度条
        navHtml += '<div class="episode-progress mb-2">';
        navHtml += `<div class="progress" style="height: 8px;">
                        <div class="progress-bar bg-info" id="episodeProgressBar" role="progressbar" 
                             style="width: 0%" aria-valuenow="0" aria-valuemin="0" 
                             aria-valuemax="100"></div>
                    </div>`;
        navHtml += '</div>';
        
        // 添加集数指示器标签行
        navHtml += '<div class="d-flex flex-wrap episode-indicators mb-2">';
        for (let i = 1; i <= episodes; i++) {
            navHtml += `<span class="episode-indicator" data-episode="${i}" id="indicator-${i}">
                            <span class="episode-number">${i}</span>
                            <span class="complete-icon d-none">✓</span>
                        </span>`;
        }
        navHtml += '</div>';
        
        navHtml += '</div>';
        episodeNavigation.innerHTML = navHtml;
        episodeNavigation.style.display = 'block';
        
        // 添加事件监听器
        document.getElementById('episodeSelect').addEventListener('change', function() {
            showEpisode(this.value);
        });
        
        document.getElementById('prevEpisode').addEventListener('click', function() {
            const currentEp = parseInt(document.getElementById('episodeSelect').value);
            if (currentEp > 1) {
                showEpisode(currentEp - 1);
            }
        });
        
        document.getElementById('nextEpisode').addEventListener('click', function() {
            const currentEp = parseInt(document.getElementById('episodeSelect').value);
            if (currentEp < episodes) {
                showEpisode(currentEp + 1);
            }
        });
        
        document.querySelectorAll('.episode-indicator').forEach(indicator => {
            indicator.addEventListener('click', function() {
                const epNum = this.getAttribute('data-episode');
                showEpisode(epNum);
            });
        });
    }
    
    // 显示特定集
    function showEpisode(episodeNumber) {
        // 隐藏所有集
        document.querySelectorAll('.episode-content').forEach(ep => {
            ep.style.display = 'none';
        });
        
        // 取消所有指示器激活状态
        document.querySelectorAll('.episode-indicator').forEach(indicator => {
            indicator.classList.remove('active');
        });
        
        // 显示选定的集
        const epElement = document.getElementById(`episode-${episodeNumber}`);
        if (epElement) {
            epElement.style.display = 'block';
            
            // 更新选择器值
            document.getElementById('episodeSelect').value = episodeNumber;
            
            // 激活对应指示器
            const indicator = document.getElementById(`indicator-${episodeNumber}`);
            if (indicator) {
                indicator.classList.add('active');
            }
            
            // 更新上一集/下一集按钮状态
            document.getElementById('prevEpisode').disabled = (episodeNumber <= 1);
            document.getElementById('nextEpisode').disabled = (episodeNumber >= totalEpisodes);
            
            // 更新进度条
            const progressPercent = (episodeNumber / totalEpisodes) * 100;
            const episodeProgressBar = document.getElementById('episodeProgressBar');
            episodeProgressBar.style.width = `${progressPercent}%`;
            episodeProgressBar.setAttribute('aria-valuenow', progressPercent);
        }
        
        // 滚动到顶部
        window.scrollTo(0, episodeNavigation.offsetTop - 20);
    }
    
    // 显示单集内容
    function displayEpisodeContent(episodeNumber, content) {
        // 创建或更新该集的div
        let episodeDiv = document.getElementById(`episode-${episodeNumber}`);
        if (!episodeDiv) {
            episodeDiv = document.createElement('div');
            episodeDiv.id = `episode-${episodeNumber}`;
            episodeDiv.className = 'episode-content script-content mb-4';
            episodesContainer.appendChild(episodeDiv);
        }
        
        // 添加集标题和内容
        episodeDiv.innerHTML = `
            <div class="episode-title">
                <h4>第${episodeNumber}集</h4>
            </div>
            <div class="episode-text">
                ${formatContent(content)}
            </div>
        `;
        
        // 更新导航状态
        const indicator = document.getElementById(`indicator-${episodeNumber}`);
        if (indicator) {
            indicator.classList.add('completed');
            const completeIcon = indicator.querySelector('.complete-icon');
            if (completeIcon) {
                completeIcon.classList.remove('d-none');
            }
        }
        
        // 记录用户当前正在查看的集数
        const currentViewingEpisode = document.getElementById('episodeSelect') ? 
                                    parseInt(document.getElementById('episodeSelect').value) : episodeNumber;
        
        // 只有在用户没有主动选择查看其他集数时，才自动显示当前生成的集数
        if (!document.querySelector('.episode-content[style*="display: block"]') || 
            currentViewingEpisode === episodeNumber) {
            showEpisode(episodeNumber);
        }
    }
    
    // 重置UI状态
    function resetUI() {
        initialContent.style.display = 'none';
        initialContent.innerHTML = '';
        episodeNavigation.style.display = 'none';
        episodeNavigation.innerHTML = '';
        episodesContainer.innerHTML = '';
        progressContainer.style.display = 'none';
        progressBar.style.width = '0%';
        progressBar.textContent = '0%';
        statusElement.textContent = '';
        currentEpisode = 0;
        episodesContent = {};
    }
    
    // 表单验证
    function validateForm() {
        // 检查题材是否已选择
        const genre = document.getElementById('genre').value;
        if (!genre) {
            alert('请选择题材');
            return false;
        }
        
        // 检查每集时长是否已选择
        const duration = document.getElementById('duration').value;
        if (!duration) {
            alert('请选择每集时长');
            return false;
        }
        
        // 检查集数是否已填写并有效
        const episodes = document.getElementById('episodes').value;
        if (!episodes || episodes < 1 || episodes > 100) {
            alert('请输入有效的集数 (1-100)');
            return false;
        }
        
        // 检查至少选择了四个角色
        const characterCheckboxes = document.querySelectorAll('input[name="characters"]:checked');
        if (characterCheckboxes.length < 4) {
            alert('请至少选择四个角色');
            return false;
        }
        
        return true;
    }
    
    // 显示错误信息
    function displayError(error) {
        statusElement.innerHTML = `<div class="alert alert-danger">
                                    <strong>错误:</strong> ${error}
                                 </div>`;
        loadingIndicator.style.display = 'none';
    }
    
    // 格式化内容
    function formatContent(text) {
        // 1. 先进行HTML转义 - 确保内容中的HTML标签不会被浏览器解析
        text = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
        
        // 2. 然后将换行符转换为<br>标签
        text = text.replace(/\n/g, '<br>');
        
        return text;
    }

    // 提取画面描述词函数
    function extractScenePrompts(taskId, episode = null) {
        if (!taskId) {
            displayError('未指定任务ID，无法提取画面描述词');
            return;
        }
        
        // 显示加载状态
        const promptsContainer = document.getElementById('promptsContainer') || document.createElement('div');
        if (!document.getElementById('promptsContainer')) {
            promptsContainer.id = 'promptsContainer';
            promptsContainer.className = 'mt-4';
            document.querySelector('.container').appendChild(promptsContainer);
        }
        
        promptsContainer.innerHTML = '<div class="alert alert-info">正在提取画面描述词...</div>';
        
        // 准备请求URL
        let url = `/api/stream/extract-scene-prompts/${taskId}`;
        if (episode) {
            url += `?episode=${episode}`;
        }
        
        // 发送请求
        fetch(url)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP错误: ${response.status}`);
            }
            
            // 获取响应的Reader
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            
            // 创建结果存储对象
            const promptsResult = {};
            
            // 用于累积SSE事件
            let buffer = '';
            
            // 处理流式数据
            function processStream({done, value}) {
                // 流结束
                if (done) {
                    console.log('提取画面描述词流结束');
                    return;
                }
                
                // 解码数据并添加到缓冲区
                buffer += decoder.decode(value, { stream: true });
                
                // 按SSE格式分割事件
                const events = buffer.split('\n\n');
                buffer = events.pop() || ''; // 保留最后一个可能不完整的事件
                
                // 处理每个事件
                for (const eventText of events) {
                    if (!eventText.trim()) continue;
                    
                    // 解析事件
                    const eventLines = eventText.split('\n');
                    let eventType = '';
                    let eventData = {};
                    
                    for (const line of eventLines) {
                        if (line.startsWith('event:')) {
                            eventType = line.replace('event:', '').trim();
                        } else if (line.startsWith('data:')) {
                            try {
                                eventData = JSON.parse(line.replace('data:', '').trim());
                            } catch (e) {
                                console.error('解析事件数据出错:', e);
                            }
                        }
                    }
                    
                    if (eventType && Object.keys(eventData).length > 0) {
                        // 处理事件
                        handlePromptEvent(eventType, eventData, promptsResult);
                    }
                }
                
                // 继续读取
                reader.read().then(processStream);
            }
            
            // 开始读取流
            reader.read().then(processStream);
        })
        .catch(error => {
            console.error('提取画面描述词错误:', error);
            promptsContainer.innerHTML = `<div class="alert alert-danger">提取画面描述词失败: ${error.message}</div>`;
        });
    }

    // 处理画面描述词事件
    function handlePromptEvent(eventType, data, promptsResult) {
        console.log('收到画面描述词事件:', eventType, data);
        
        const promptsContainer = document.getElementById('promptsContainer');
        if (!promptsContainer) return;
        
        switch (eventType) {
            case 'status':
                promptsContainer.innerHTML = `<div class="alert alert-info">${data.message}</div>`;
                break;
                
            case 'episode_prompts':
                // 保存该集的画面描述词
                promptsResult[data.episode] = data.content;
                
                // 更新UI
                updatePromptsUI(promptsResult);
                break;
                
            case 'complete':
                // 更新完成状态
                const alertDiv = promptsContainer.querySelector('.alert');
                if (alertDiv) {
                    alertDiv.className = 'alert alert-success';
                    alertDiv.textContent = '画面描述词提取完成';
                }
                break;
                
            case 'error':
                promptsContainer.innerHTML = `<div class="alert alert-danger">${data.message}</div>`;
                break;
        }
    }

    // 更新画面描述词UI
    function updatePromptsUI(promptsResult) {
        const promptsContainer = document.getElementById('promptsContainer');
        if (!promptsContainer) return;
        
        // 清除现有内容，保留状态提示
        const alertDiv = promptsContainer.querySelector('.alert');
        promptsContainer.innerHTML = '';
        if (alertDiv) promptsContainer.appendChild(alertDiv);
        
        // 创建标签页导航
        let tabsHtml = '<ul class="nav nav-tabs" role="tablist">';
        let contentHtml = '<div class="tab-content mt-2">';
        
        Object.keys(promptsResult).sort((a, b) => parseInt(a) - parseInt(b)).forEach((episode, index) => {
            const isActive = index === 0 ? 'active' : '';
            const id = `episode-prompt-${episode}`;
            
            // 标签页
            tabsHtml += `
                <li class="nav-item" role="presentation">
                    <button class="nav-link ${isActive}" id="${id}-tab" data-bs-toggle="tab" 
                            data-bs-target="#${id}" type="button" role="tab" 
                            aria-controls="${id}" aria-selected="${index === 0}">
                        第${episode}集
                    </button>
                </li>
            `;
            
            // 内容面板
            contentHtml += `
                <div class="tab-pane fade show ${isActive}" id="${id}" role="tabpanel" aria-labelledby="${id}-tab">
                    <div class="card">
                        <div class="card-body">
                            <h5 class="card-title">第${episode}集画面描述词</h5>
                            <pre class="prompt-content">${promptsResult[episode]}</pre>
                            <button class="btn btn-sm btn-primary copy-btn" data-content="${episode}">复制</button>
                        </div>
                    </div>
                </div>
            `;
        });
        
        tabsHtml += '</ul>';
        contentHtml += '</div>';
        
        // 添加到容器
        promptsContainer.innerHTML += tabsHtml + contentHtml;
        
        // 添加复制按钮功能
        promptsContainer.querySelectorAll('.copy-btn').forEach(btn => {
            btn.addEventListener('click', function() {
                const episode = this.getAttribute('data-content');
                const content = promptsResult[episode];
                
                navigator.clipboard.writeText(content)
                    .then(() => {
                        this.textContent = '已复制';
                        this.classList.remove('btn-primary');
                        this.classList.add('btn-success');
                        
                        // 3秒后恢复按钮状态
                        setTimeout(() => {
                            this.textContent = '复制';
                            this.classList.remove('btn-success');
                            this.classList.add('btn-primary');
                        }, 3000);
                    })
                    .catch(err => {
                        console.error('复制失败:', err);
                        this.textContent = '复制失败';
                        this.classList.remove('btn-primary');
                        this.classList.add('btn-danger');
                    });
            });
        });
    }

    // 添加提取画面描述词按钮
    function addExtractPromptsButton() {
        if (!currentTaskId) return;
        
        const container = document.querySelector('.container');
        if (!container) return;
        
        // 如果已存在按钮，不重复添加
        if (document.getElementById('extractPromptsBtn')) return;
        
        // 创建按钮
        const buttonDiv = document.createElement('div');
        buttonDiv.className = 'mt-3 text-center';
        buttonDiv.innerHTML = `
            <button id="extractPromptsBtn" class="btn btn-info" type="button">
                提取画面描述词
            </button>
        `;
        
        // 添加到容器
        container.appendChild(buttonDiv);
        
        // 绑定点击事件
        document.getElementById('extractPromptsBtn').addEventListener('click', function() {
            extractScenePrompts(currentTaskId);
        });
    }
}); 