# AIVStudio — AI 网剧流水线

编排服务：剧本创意 → LLM 分镜 + 角色档案 → 角色定妆 → 首帧（参考定妆照）→ 图生视频 → TTS 配音 → 混流拼接正片。

## 特性

- **双模式**：`mock`（默认，本地模拟全流程，无需 GPU）/ `real`（对接真实模型后端）
- **V2 流水线**：角色定妆（确认点 A）→ 首帧批量生成（确认点 B）→ 视频 → 配音合成，两个人工确认卡点
- **角色一致性**：首帧生成按镜头出场角色自动携带定妆照作为参考图（多人对手戏传多张）
- **任务队列 + 断点续跑**：任务落 SQLite，服务重启后自动从断点恢复（不重复烧 GPU）
- **登录鉴权**：用户名/密码 + Cookie 会话（PBKDF2 哈希入库），首次使用自助创建管理员，改密后全端强制重登
- **模型后端可配置**：页面上在线改各后端 URL/Key/模型名，保存即生效，无需重启
- **媒体独立存储**：按项目分目录（`data/media/{pid}/{frames,designs,videos,audio,finals,tmp}`），存储根可配置
- **实时推送**：SSE 事件广播 + 差分渲染，播放中的视频不被刷新打断；SSE 不可用自动降级 3s 轮询
- **四页路由前端**：任务列表 / 新建任务 / 任务详情 / 系统配置，hash 路由无刷新切换，移动端自适应

## 启动

```bash
pip install fastapi uvicorn httpx
MODE=real python server.py     # 默认 mock 模式，real 需显式指定
# 打开 http://127.0.0.1:8020
```

首次访问会进入初始化页面，创建管理员账号（密码至少 6 位），之后登录使用。

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

也可以登录后在「⚙️ 系统配置」页在线改（落到 `data/config.json`，同样已 gitignore），该页还可以测试连通性、修改登录密码。

### 模型后端（vLLM-Omni 风格）

| 用途 | 模型 | 接口 |
|------|------|------|
| 剧本分镜 | GLM 系列 | OpenAI 兼容 `/chat/completions` |
| 文生图/定妆 | Qwen-Image-2512 | `/v1/images/generations` |
| 图生图（角色一致性） | Qwen-Image-Edit-2511 | `/v1/images/edits`（multipart，支持多参考图） |
| 图生视频 | Wan2.2-I2V-A14B | `/v1/videos` 异步任务 + 轮询 |
| 配音 | Qwen3-TTS VoiceDesign | OpenAI 兼容 `/v1/audio/speech` |

## API

除 `/login`、静态资源与 `/api/auth/*` 外，所有接口需登录（Cookie 会话）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | /            | Web 界面（未登录 302 → /login） |
| GET/POST | /api/auth/state \| /setup \| /login | 认证：状态 / 首次初始化 / 登录 |
| POST | /api/auth/logout | 退出登录 |
| POST | /api/auth/change_password | 修改密码（验旧密码，改完全会话失效） |
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
| POST | /api/config/test | 后端连通性测试 |
| GET  | /api/mode | 模式与五后端健康状态 |
| GET  | /api/events | SSE 实时事件（数据变更广播） |

## 前端结构

```
web/
index.html    # 应用壳：侧栏导航 + 四页 section + 面包屑
login.html    # 登录页（自动识别首次初始化 / 日常登录）
js/router.js  # hash 路由（#/projects | #/new | #/project/{pid} | #/settings）
js/projects.js  # 任务列表：概要卡差分渲染，新任务置顶
js/new.js       # 新建任务：提交后跳详情页看进度
js/detail.js    # 任务详情：角色档案/确认横幅/镜头网格/正片；媒体节点复用（刷新不打断播放）
js/settings.js  # 系统配置：五后端在线配置 + 连通性测试 + 修改密码
js/app.js       # 健康状态（侧栏状态灯）+ SSE 推送（降级轮询），只刷新当前路由页
js/common.js    # 共享常量/工具（状态文案、运镜术语中文化、toast、大图查看）
js/api.js       # fetch 封装（401 自动跳登录）
css/styles.css  # 暗色主题（侧栏布局，窄屏折为顶栏）
```

静态资源引用带 `?v=时间戳` 版本号，改前端后需同步更新（防缓存）。

## 目录结构

```
server.py    # FastAPI 主服务 + V2 流水线编排 + 任务队列 + 断点续跑 + 登录中间件
providers.py # 五类模型后端的 mock/real 双实现，四层配置合并
db.py        # SQLite 数据层（WAL）：projects / tasks / assets / users / sessions 五张表
auth.py      # 认证逻辑：PBKDF2 密码哈希、会话签发/校验、auth.json 自动迁移
media.py     # 媒体独立存储：按项目分目录、URL 转换、资产登记、旧数据迁移
web/         # 前端（见上方结构）
data/        # 运行数据（已 gitignore，secrets/auth 也在这里）
```

## 数据存储

- **元数据**：`data/drama.db`（SQLite，WAL 模式）——项目、任务、资产、用户、会话五张表
- **媒体文件**：`data/media/{项目id}/`，存储根可用 `MEDIA_ROOT` 环境变量或 `config.json` 的 `storage.root` 覆盖
- **账号**：存 `users` 表；旧版 `data/auth.json` 首次使用时自动迁移入库并改名 `.migrated` 留档
- 旧版平铺媒体文件启动时自动迁移

## 路线图

- [x] 任务队列化 + 断点续跑
- [x] SQLite 数据层
- [x] 媒体独立存储
- [x] SSE 推送 + 前端模块化
- [x] 登录鉴权 + 密码可配置
- [x] 四页路由 + 侧栏布局
- [ ] 登录防爆破（失败锁定）
- [ ] 数据库定时备份
- [ ] 任务完成通知（webhook）
- [ ] Docker Compose 部署
