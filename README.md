# SillyTavern 视频助手 (Video Helper)

将 SillyTavern 角色扮演聊天记录转化为动漫风格视频的自动化流水线。

## 架构

```
SillyTavern (浏览器)
    ↓ JS-Slash-Runner 脚本 (video-helper-th.js)
    ↓ 提取消息、UI面板、场景选择
Python 中间件 (middleware_server.py)
    ↓ 调用 LLM 分析场景 → 生成提示词
    ↓ 提交工作流到 ComfyUI API
ComfyUI (支持本地/云端部署)
    ↓ SD文生图 → 关键帧
    ↓ Wan2.1 I2V → 视频
    ↓ RIFE帧插值 → RealESRGAN超分 → FFmpeg拼接
最终视频输出 (MP4)
```

## 目录结构

```
video_project/
├── config.json                  # 配置文件（ComfyUI/LLM/视频参数）
├── middleware_server.py         # Python HTTP 中间件服务器 (端口5000)
├── start.sh                     # 启动脚本
├── test_parser.py               # 聊天解析器测试
│
├── scripts/
│   ├── chat_parser.py           # JSONL聊天记录解析器
│   ├── scene_analyzer.py        # LLM场景分析器（生成提示词）
│   ├── scene_processor.py       # 核心流水线编排器
│   ├── comfyui_client.py        # ComfyUI API客户端（支持Basic Auth）
│   └── video_concat.py          # FFmpeg视频拼接
│
├── workflows/
│   ├── i2v_wan21.json           # 统一工作流：SD文生图 + Wan I2V图生视频
│   ├── i2v_wan21_vhs.json       # 同上，VHS_VideoCombine输出MP4格式
│   └── keyframe_gen.json        # 独立的关键帧生成工作流（SD文生图）
│
└── sillytavern/
    ├── video-helper-th.js       # JS-Slash-Runner适配版脚本（TavernHelper API）
    ├── video-helper.js          # 原始Tampermonkey版本
    └── video-helper-import.json # JS-Slash-Runner导入文件
```

## 快速开始

### 1. 环境要求

- Python 3.8+
- ComfyUI（本地或云端部署，需安装 Wan Video 自定义节点）
- SillyTavern + JS-Slash-Runner 扩展
- LLM API（OpenAI兼容格式）

### 2. 配置

编辑 `config.json`：

```json
{
    "comfyui": {
        "host": "your-comfyui-host",
        "port": 8188,
        "use_https": true,
        "username": "your-username",
        "password": "your-password"
    },
    "llm": {
        "api_base": "https://your-llm-api/v1",
        "api_key": "your-api-key",
        "model": "your-model"
    }
}
```

### 3. 启动中间件

```bash
python middleware_server.py --port 5000 --config config.json
```

### 4. SillyTavern 导入脚本

在 JS-Slash-Runner 扩展中导入 `sillytavern/video-helper-import.json`，点击工具栏的「视频面板」按钮打开面板。

## 统一工作流原理 (i2v_wan21.json)

**核心思想**：在一个 ComfyUI 工作流内完成文生图+图生视频，无需中间文件传输。

```
阶段1: SD文生图（生成关键帧）
  CheckpointLoaderSimple → CLIPTextEncode(正/负) → KSampler → VAEDecode
  → 输出: 关键帧图片 (IMAGE)

阶段2: Wan I2V（关键帧→视频）
  UNETLoader(Wan模型) → CLIPLoader(T5) → VAELoader → CLIPTextEncode
  → WanImageToVideo(start_image=关键帧) → KSampler → VAEDecode
  → SaveAnimatedWEBP / VHS_VideoCombine
  → 输出: 视频文件
```

关键连接：SD的VAEDecode输出 → WanImageToVideo的start_image输入

### 需要的 ComfyUI 自定义节点

- [ComfyUI-WanVideoWrapper](https://github.com/kijai/ComfyUI-WanVideoWrapper) - Wan系列节点
- [ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite) - VHS_VideoCombine

### 需要的模型

| 用途 | 模型 | 说明 |
|------|------|------|
| SD关键帧 | revAnimated_v2Rebirth.safetensors | SD1.5动漫模型 |
| Wan I2V | wan2.1_i2v_480p_14B_fp8_e4m3fn.safetensors | Wan2.1图生视频14B |
| T5编码器 | umt5_xxl_fp8_e4m3fn_scaled.safetensors | Wan专用文本编码器 |
| VAE | wan_2.1_vae.safetensors | Wan专用VAE |

## SillyTavern 脚本开发关键知识

### TavernHelper 消息对象字段

```javascript
// getChatMessages() 返回的实际字段（已验证）
{
    message_id: 0,           // 消息索引
    name: "角色名",           // 发送者
    role: "user"|"assistant",// 角色
    is_hidden: false,        // 是否隐藏
    message: "消息内容",      // 实际文本（不是mes！）
    data: {},                // 元数据
    extra: {},               // 扩展元数据
}
```

### 范围格式

```javascript
TH.getChatMessages("0-109")   // ✅ 单横线，返回110条
TH.getChatMessages("0--110")  // ❌ 双横线，只返回2条！
```

### 工具栏按钮

```javascript
// 使用全局函数（不是TH.on）
appendInexistentScriptButtons([{name: '按钮名', visible: true}]);
eventOn(getButtonEvent('按钮名'), () => { /* 处理函数 */ });
```

## API 端点

中间件服务器提供以下 REST API：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查 |
| POST | /api/analyze | 分析场景（返回场景列表） |
| POST | /api/generate | 开始生成视频（异步任务） |
| GET | /api/progress/{task_id} | 查询任务进度 |

## 加速策略

1. **快速模型**: 使用 `wan2.2-i2v-rapid-aio` 系列（云端预装）
2. **减少帧数**: 81帧→33帧（5秒→2秒）
3. **减少步数**: 30步→15步
4. **多关键帧分段**: SD生成多个关键帧 → 分段I2V → FFmpeg拼接
5. **LoRA加速**: 使用 Wan 加速 LoRA

## License

MIT
