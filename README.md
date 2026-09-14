# AIVStudio — AI 网剧流水线

编排服务：剧本创意 → LLM 分镜 + 角色档案 → 角色定妆 → 首帧（参考定妆照）→ 图生视频 → TTS 配音 → 混流拼接正片。

## 特性

- **双模式**：`mock`（默认，本地模拟全流程，无需 GPU）/ `real`（对接真实模型后端）
- **V2 流水线**：角色定妆（确认点 A）→ 首帧批量生成（确认点 B）→ 视频 → 配音合成，两个人工确认卡点
- **角色一致性**：首帧生成按镜头出场角色自动携带定妆照作为参考图（多人对手戏传多张）
- **任务队列 + 断点续跑**：任务落 SQLite，服务重启后自动从断点恢复（不重复烧 GPU）
- **模型后端可配置**：页面上在线改各后端 URL/Key/模型名，保存即生效，无需重启
- **媒体独立存储**：按项目分目录（`data/media/{pid}/{frames,designs,videos,audio,finals,tmp}`），存储根可配置

## 启动

```bash
pip install fastapi uvicorn httpx
python server.py
# 打开 http://127.0.0.1:8020
```

环境变量 `MODE=real` 切真实后端（默认 mock）。

## real 模式配置

**方式一（推荐）**：复制 `data/secrets.example.json` 为 `data/secrets.json`，填入真实地址和 key（该文件已 gitignore）。

**方式二**：环境变量（优先级低于 secrets.json）：

```ini
MODE=real
GLM_BASE_URL=...  GLM_API_KEY=...  GLM_MODEL=...
QWEN_IMAGE_URL=...  QWEN_IMAGE_KEY=...  QWEN_IMAGE_MODEL=...
QWEN_EDIT_URL=...   QWEN_EDIT_KEY=...   QWEN_EDIT_MODEL=...
WAN_I2V_URL=...     WAN_I2V_KEY=...     WAN_I2V_MODEL=...
TTS_URL=...         TTS_KEY=...         TTS_MODEL=...
```

也可以启动后在页面右上角「⚙ 模型后端配置」里在线改（落到 `data/config.json`，同样已 gitignore）。

### 模型后端（vLLM-Omni 风格）

| 用途 | 模型 | 接口 |
|------|------|------|
| 剧本分镜 | GLM 系列 | OpenAI 兼容 `/chat/completions` |
| 文生图/定妆 | Qwen-Image-2512 | `/v1/images/generations` |
| 图生图（角色一致性） | Qwen-Image-Edit-2511 | `/v1/images/edits`（multipart，支持多参考图） |
| 图生视频 | Wan2.2-I2V-A14B | `/v1/videos` 异步任务 + 轮询 |
| 配音 | Qwen3-TTS VoiceDesign | OpenAI 兼容 `/v1/audio/speech` |

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | /            | Web 界面 |
| POST | /api/projects | 创建项目 `{title, idea, n_shots, style}` |
| GET  | /api/projects | 项目列表 |
| GET  | /api/projects/{id} | 项目详情（含各镜头进度） |
| DELETE | /api/projects/{id} | 删除项目（含媒体目录） |
| POST | /api/projects/{id}/run | 启动流水线 `?stage=design\|frames\|all` |
| POST | /api/projects/{id}/run_frames | 定妆确认后跑首帧 |
| POST | /api/projects/{id}/run_videos | 首帧确认后跑视频 |
| POST | /api/projects/{id}/tts | 配音 + 音画合成 + 拼接正片 |
| GET  | /api/projects/{id}/tasks | 任务历史（状态 + 断点进度） |
| GET  | /api/projects/{id}/assets | 媒体资产清单与统计 |
| POST | /api/shots/{sid}/dialogue | 改台词（清空则去掉配音） |
| POST | /api/shots/{sid}/regenerate | 重抽 `?mode=frame\|video` |
| POST | /api/characters/{pid}/regenerate | 换角色定妆照 |
| GET/PUT | /api/config | 模型后端配置（key 打码，改完即生效） |
| GET  | /api/config/test | 后端连通性测试 |
| GET  | /api/mode | 模式与五后端健康状态 |

## 目录结构

```
server.py    # FastAPI 主服务 + V2 流水线编排 + 任务队列 + 断点续跑
providers.py # 五类模型后端的 mock/real 双实现，四层配置合并
db.py        # SQLite 数据层（WAL）：projects / tasks / assets 三张表
media.py     # 媒体独立存储：按项目分目录、URL 转换、资产登记、旧数据迁移
web/         # 前端单页（暗色主题，差分更新，实时进度）
data/        # 运行数据（已 gitignore，secrets 也在这里）
```

## 数据存储

- **元数据**：`data/drama.db`（SQLite，WAL 模式）
- **媒体文件**：`data/media/{项目id}/`，存储根可用 `MEDIA_ROOT` 环境变量或 `config.json` 的 `storage.root` 覆盖
- 旧版平铺文件启动时自动迁移
