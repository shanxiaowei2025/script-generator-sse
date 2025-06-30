# 剧本生成器 SSE API

基于Claude-3-7-Sonnet的AI剧本生成服务，采用Server-Sent Events (SSE)流式响应技术，可以生成完整的短剧剧本，包括角色表、分集目录和详细剧情。支持实时提取画面描述词并调用RunningHub API，集成MinIO对象存储。

## 特性

- 基于SSE的流式响应，实时传输生成内容
- 支持多任务并行生成和管理
- 可取消正在进行的生成任务
- 支持实时提取剧本中的画面描述词
- 集成RunningHub工作流API，自动处理提示词
- 自动图片下载和MinIO对象存储
- PDF剧本生成和导出功能
- 高质量的剧本生成效果
- 模块化架构设计

## 项目结构

```
app/
├── api/                    # API路由和控制器
│   ├── stream_router.py   # 流式API路由
│   ├── models.py          # API数据模型
│   └── __init__.py
├── core/                   # 核心功能和配置
│   ├── config.py          # 应用配置
│   ├── generator.py       # 核心生成器
│   ├── generator_part2.py # 生成器扩展
│   ├── init.py           # 初始化模块
│   └── __init__.py
├── models/                 # 数据模型和Schema
│   ├── schema.py          # 数据结构定义
│   └── __init__.py
├── services/               # 业务服务模块
│   ├── script_generation.py  # 剧本生成服务
│   ├── scene_prompts.py      # 场景提示词服务
│   ├── runninghub.py         # RunningHub集成服务
│   ├── image_processing.py   # 图片处理服务
│   ├── pdf_generation.py     # PDF生成服务
│   ├── task_queue.py         # 任务队列管理
│   └── __init__.py
├── storage/                # 存储目录
│   ├── generation_states/  # 生成状态存储
│   ├── partial_contents/   # 部分内容存储
│   ├── pdfs/              # PDF文件存储
│   └── images/            # 图片文件存储
├── utils/                  # 工具函数
│   ├── storage.py         # 存储工具
│   ├── minio_storage.py   # MinIO存储客户端
│   ├── image_downloader.py # 图片下载工具
│   ├── runninghub_api.py  # RunningHub API客户端
│   ├── text_utils.py      # 文本处理工具
│   ├── pdf_generator.py   # PDF生成工具
│   └── __init__.py
├── static/                 # 静态文件
└── main.py                # 应用入口点
```

## 安装和运行

1. 克隆仓库

```bash
git clone <repository-url>
cd <repository-directory>
```

2. 安装依赖

```bash
pip3 install -r requirements.txt
```

3. 设置环境变量

创建`.env`文件或设置以下环境变量:

```
# API配置
API_KEY=your_anthropic_api_key
API_URL=https://api.anthropic.com/v1/messages

# 应用配置
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=False

# RunningHub API配置
# 创建任务API
RUNNINGHUB_CREATE_API_URL=https://www.runninghub.cn/task/openapi/create
# 查询任务状态API
RUNNINGHUB_STATUS_API_URL=https://www.runninghub.cn/task/openapi/queryStatus
# 查询任务结果API
RUNNINGHUB_RESULT_API_URL=https://www.runninghub.cn/task/openapi/queryResult
# API密钥和工作流配置
RUNNINGHUB_API_KEY=your_runninghub_api_key
RUNNINGHUB_WORKFLOW_ID=your_workflow_id
RUNNINGHUB_NODE_ID=your_node_id

# MinIO对象存储配置
MINIO_ENABLED=true
MINIO_URL=localhost:9000
MINIO_ACCESS_KEY=your_access_key
MINIO_SECRET_KEY=your_secret_key
MINIO_BUCKET=script-generator
MINIO_SECURE=false

# 文件存储配置
SAVE_FILES_LOCALLY=false
```

4. 运行服务

```bash
source venv/bin/activate 

uvicorn app.main:app --host=0.0.0.0 --port=8003 --reload

# 或
python -m app.main
```

服务将在 `http://localhost:8003` 启动。

## API端点

### 流式生成剧本

```
POST /api/stream/generate-script
```

请求示例:

```json
{
  "genre": "都市职场",
  "duration": "3分钟",
  "episodes": 8,
  "characters": ["张明,男,28", "李婷,女,25", "王总,男,45"]
}
```

响应：返回SSE格式的事件流，包括task_id、进度更新和内容片段。

### 取消正在生成的剧本

```
DELETE /api/stream/cancel/{task_id}
```

### 提取画面描述词

```
POST /api/stream/extract-scene-prompts/{task_id}
```

请求参数:
- `task_id`: 任务ID，路径参数
- `episode`: 可选，请求体参数，指定要提取的集数

请求示例:

```json
{
  "task_id": "21f8d32a-1c3e-4e69-8123-afed96e7a321",
  "episode": 1
}
```

响应：返回SSE格式的事件流，包含提取的画面描述词。

### 通过RunningHub处理画面提示词

```
POST /api/stream/process-prompts-with-runninghub/{task_id}
```

请求参数:
- `task_id`: 任务ID，路径参数
- `episode`: 可选，请求体参数，指定要处理的集数

请求示例:

```json
{
  "task_id": "21f8d32a-1c3e-4e69-8123-afed96e7a321",
  "episode": 1
}
```

响应：返回SSE格式的事件流，包含RunningHub API处理结果，包括任务创建和任务状态信息。

### 查询RunningHub任务状态

```
POST /api/runninghub/task-status
```

请求参数:
- `task_id`: RunningHub任务ID

请求示例:

```json
{
  "task_id": "1920031617669115905"
}
```

响应：返回RunningHub任务的状态信息，如果任务已完成，则同时返回任务结果。

### 查询RunningHub任务结果

```
POST /api/runninghub/task-result
```

请求参数:
- `task_id`: RunningHub任务ID

请求示例:

```json
{
  "task_id": "1920031617669115905"
}
```

响应：返回RunningHub任务的结果信息。

## 前端使用

访问 `http://localhost:8000` 使用内置的Web界面。

前端JavaScript示例:

```javascript
// 发起剧本生成请求
fetch('/api/stream/generate-script', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestData)
})
.then(response => {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    
    function processStream({done, value}) {
        if (done) return;
        
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';
        
        for (const event of events) {
            // 处理SSE事件
            // ...
        }
        
        reader.read().then(processStream);
    }
    
    reader.read().then(processStream);
});

// 取消生成
fetch(`/api/stream/cancel/${taskId}`, { method: 'DELETE' });

// 提取画面描述词
fetch(`/api/stream/extract-scene-prompts/${taskId}`);

// 使用RunningHub处理画面提示词
fetch(`/api/stream/process-prompts-with-runninghub/${taskId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ episode: 1 }) // 可选，指定处理特定集数
});

// 查询RunningHub任务状态
fetch('/api/runninghub/task-status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: '1920031617669115905' })
})
.then(response => response.json())
.then(data => console.log(data));

// 查询RunningHub任务结果
fetch('/api/runninghub/task-result', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task_id: '1920031617669115905' })
})
.then(response => response.json())
.then(data => console.log(data));
```

## 许可

MIT License 