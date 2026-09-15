"""LLM / 文生图 / 图生图 / 图生视频 / TTS provider：mock 与 real 双实现。

real 模式对接：
- 剧本分镜: GLM-5.3-Flash (OpenAI 兼容 chat completions)
- 文生图:   Qwen-Image-2512 (vLLM-Omni /v1/images/generations 风格)
- 图生图:   Qwen-Image-Edit-2511 (POST /v1/images/edits，multipart，角色一致性)
- 图生视频: Wan2.2-I2V-A14B (vLLM-Omni POST /v1/videos 异步任务 + 轮询)
- TTS:      VoxCPM2 (OpenAI 兼容 POST /v1/audio/speech)
"""
import asyncio
import base64
import json
import os
import random
import re
import time
import uuid

import httpx  # 统一顶层导入；各函数内不再重复 import

MODE = os.environ.get("MODE", "mock").lower()  # mock | real

# --- 模型后端配置（可在线修改，落盘 data/config.json，改完即生效）---
# 结构 {group: {url, key, model}}，group: llm / image / edit / video / tts
# 优先级：config.json > secrets.json > 环境变量 > 代码内置默认值
# ⚠️ 真实 key 不入库：放在 data/secrets.json（已 gitignore）或环境变量里
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_BASE_DIR, "data", "config.json")
_SECRETS_FILE = os.path.join(_BASE_DIR, "data", "secrets.json")

_DEFAULTS = {
    "llm":   {"url": "", "key": "", "model": "GLM-5.3-Flash"},
    "image": {"url": "", "key": "", "model": "Qwen-Image-2512"},
    "edit":  {"url": "", "key": "", "model": "Qwen-Image-Edit-2511"},
    "video": {"url": "", "key": "", "model": "Wan2.2-I2V-A14B-Diffusers"},
    "tts":   {"url": "", "key": "", "model": "Qwen3-TTS-12Hz-1.7B-VoiceDesign"},
}
_ENV_MAP = {
    "llm":   ("GLM_BASE_URL", "GLM_API_KEY", "GLM_MODEL"),
    "image": ("QWEN_IMAGE_URL", "QWEN_IMAGE_KEY", "QWEN_IMAGE_MODEL"),
    "edit":  ("QWEN_EDIT_URL", "QWEN_EDIT_KEY", "QWEN_EDIT_MODEL"),
    "video": ("WAN_I2V_URL", "WAN_I2V_KEY", "WAN_I2V_MODEL"),
    "tts":   ("TTS_URL", "TTS_KEY", "TTS_MODEL"),
}


def _load_cfg() -> dict:
    """合并四层配置：默认值 <- 环境变量 <- secrets.json <- config.json（在线修改的生效配置）。"""
    cfg = {g: dict(v) for g, v in _DEFAULTS.items()}
    for g, (eu, ek, em) in _ENV_MAP.items():
        if os.environ.get(eu): cfg[g]["url"] = os.environ[eu]
        if os.environ.get(ek): cfg[g]["key"] = os.environ[ek]
        if os.environ.get(em): cfg[g]["model"] = os.environ[em]
    for path in (_SECRETS_FILE, _CONFIG_FILE):   # secrets 先合，config.json（在线改的）最后覆盖
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                for g, v in (saved or {}).items():
                    if g in cfg and isinstance(v, dict):
                        for k in ("url", "key", "model"):
                            if v.get(k):
                                cfg[g][k] = v[k]
            except Exception:
                pass  # 配置文件损坏时退回环境变量/默认值
    return cfg


def get_cfg(group: str) -> dict:
    return _load_cfg()[group]


def all_cfg() -> dict:
    return _load_cfg()


def save_cfg(data: dict):
    """保存在线修改的配置。key 留空表示沿用现有值不落盘；空 url/model 同理。"""
    os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
    current = {}
    if os.path.exists(_CONFIG_FILE):
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                current = json.load(f) or {}
        except Exception:
            current = {}
    for g, v in (data or {}).items():
        if g not in _DEFAULTS or not isinstance(v, dict):
            continue
        cur = current.setdefault(g, {})
        for k in ("url", "model"):
            if v.get(k) and str(v[k]).strip():
                cur[k] = str(v[k]).strip()
        if v.get("key") and str(v["key"]).strip():
            cur["key"] = str(v["key"]).strip()
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)


def __getattr__(name):
    """PEP 562：providers.GLM_BASE_URL 等旧式模块级引用动态转发到新配置体系。"""
    _LEGACY = {
        "GLM_BASE_URL": ("llm", "url"), "GLM_API_KEY": ("llm", "key"), "GLM_MODEL": ("llm", "model"),
        "QWEN_IMAGE_URL": ("image", "url"), "QWEN_IMAGE_KEY": ("image", "key"),
        "QWEN_IMAGE_MODEL": ("image", "model"),
        "WAN_I2V_URL": ("video", "url"), "WAN_I2V_KEY": ("video", "key"), "WAN_I2V_MODEL": ("video", "model"),
        "QWEN_EDIT_URL": ("edit", "url"), "QWEN_EDIT_KEY": ("edit", "key"), "QWEN_EDIT_MODEL": ("edit", "model"),
        "TTS_URL": ("tts", "url"), "TTS_KEY": ("tts", "key"), "TTS_MODEL": ("tts", "model"),
    }
    if name in _LEGACY:
        g, k = _LEGACY[name]
        return get_cfg(g)[k]
    raise AttributeError(f"module 'providers' has no attribute {name!r}")

# 首帧落盘目录（绝对路径，避免服务进程 CWD 漂移导致找不到图）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", "frames")
VIDEOS_DIR = os.path.join(BASE_DIR, "data", "videos")
FRAMES_DIR = DATA_DIR
AUDIO_DIR = os.path.join(BASE_DIR, "data", "audio")
FINALS_DIR = os.path.join(BASE_DIR, "data", "finals")
TMP_DIR = os.path.join(BASE_DIR, "data", "tmp")


def _media_dir(pid: str, kind: str) -> str:
    """媒体独立存储：按项目分目录。media 模块失败时退回旧平铺目录。"""
    try:
        import media
        return media.project_media_dir(pid or "unknown", kind)
    except Exception:
        fallback = {"frames": DATA_DIR, "designs": DATA_DIR, "videos": VIDEOS_DIR,
                    "audio": AUDIO_DIR, "finals": FINALS_DIR, "tmp": TMP_DIR}
        d = fallback.get(kind, DATA_DIR)
        os.makedirs(d, exist_ok=True)
        return d

SHOT_TEMPLATE_PROMPT = (
    "你是资深短剧分镜师。把用户的剧本创意拆成 {n} 个镜头。"
    "输出 JSON 对象（不要其他文字），结构：\n"
    '{{"characters": [{{"name": "角色名", "appearance": "固定外观描述:年龄/发型/服装/气质,一句话,供生图锁定形象"}}],\n'
    '"shots": [{{"shot_id": "S01_01", "description": "镜头级画面描述,可视化细节,不要叙事语言", '
    '"camera": "push-in/pull-out/pan/tilt/static/rack-focus 之一", "duration": 5, '
    '"dialogue": "台词,无则空串", "mood": "氛围关键词", "characters": ["出场角色名"]}}]}}\n'
    "剧本结构要求：第一个镜头必须是 3 秒内抓住注意力的强钩子画面；最后一个镜头留悬念。"
    "出场角色必须从 characters 里选。"
)


# ---------- 剧本 -> 分镜 + 角色档案 ----------

async def gen_storyboard(idea: str, n_shots: int) -> dict:
    """返回 {"characters": [...], "shots": [...]}。"""
    llm = get_cfg("llm")
    if MODE == "real" and llm["key"]:
        return await _storyboard_real(idea, n_shots, llm)
    return await _storyboard_mock(idea, n_shots)


async def _storyboard_real(idea: str, n_shots: int, llm: dict):
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return await _storyboard_call(idea, n_shots, llm)
        except (RuntimeError, json.JSONDecodeError, ValueError) as e:
            last_err = e   # 思考超长（content 空）或 JSON 格式瑕疵均为偶发，自动重试
    raise RuntimeError(f"分镜生成连续 {3} 次失败: {last_err}")


def _loads_loose(text: str):
    """解析 LLM 输出的 JSON；失败时做常见瑕疵修复后重试（尾逗号/全角引号/未转义换行）。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fixed = text
    # 尾逗号 ",}" / ",]"
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    # 全角引号包裹的键值 -> 半角
    fixed = fixed.replace("\u201c", '"').replace("\u201d", '"')
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        # 未转义裸换行（字符串值内）：整体把控制换行替换为空格（JSON 结构换行本就可省）
        fixed2 = fixed.replace("\n", " ").replace("\r", " ").replace("\t", " ")
        return json.loads(fixed2)   # 仍失败则抛原样错误，由上层重试兜底


async def _storyboard_call(idea: str, n_shots: int, llm: dict):
    async with httpx.AsyncClient(trust_env=False, timeout=300) as client:
        r = await client.post(
            f"{llm['url']}/chat/completions",
            headers={"Authorization": f"Bearer {llm['key']}"},
            json={
                "model": llm["model"],
                "messages": [
                    {"role": "system", "content": SHOT_TEMPLATE_PROMPT.format(n=n_shots)},
                    {"role": "user", "content": idea},
                ],
                "temperature": 0.8,
                # 思考型模型：思考 + 正文共享 max_tokens，分镜 JSON 较长，余量必须给足
                "max_tokens": 16384,
            },
        )
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        content = msg.get("content") or ""
    if not content.strip():
        finish = ""
        try:
            finish = r.json()["choices"][0].get("finish_reason") or ""
        except Exception:
            pass
        raise RuntimeError(f"LLM 返回空 content（思考超长被截断, finish_reason={finish}）: {str(msg)[:200]}")
    start, end = content.find("{"), content.rfind("}") + 1
    plan = _loads_loose(content[start:end])
    shots = plan.get("shots") or []
    if not shots:  # 兼容 LLM 直接输出数组的旧格式
        arr_start, arr_end = content.find("["), content.rfind("]") + 1
        shots = _loads_loose(content[arr_start:arr_end])
    for i, s in enumerate(shots):
        s["shot_id"] = s.get("shot_id") or f"S01_{i+1:02d}"
    chars = plan.get("characters") or []
    if not chars:  # 无角色档案时兜底：从分镜里收集
        names = {c for s in shots for c in s.get("characters", []) if c}
        chars = [{"name": n, "appearance": ""} for n in sorted(names)]
    return {"characters": chars, "shots": shots}


async def _storyboard_mock(idea: str, n_shots: int) -> dict:
    await asyncio.sleep(1.5)  # 模拟 LLM 延迟
    characters = [
        {"name": "小满", "appearance": "22岁女孩，深棕色齐肩短发，米色便利店制服围裙，笑容明亮"},
        {"name": "老船长", "appearance": "60岁男性，银白络腮胡，深蓝色旧式船长外套，左眉有一道疤"},
    ]
    scenes = [
        ("深夜空间站便利店，暖黄灯光下货架整齐，女孩在柜台后打哈欠", "push-in", "欢迎光临，末班船还有三小时", "温暖孤寂"),
        ("便利店门口，白发老人拖着旧行李箱进店，风衣上落着星尘", "static", "", "神秘"),
        ("柜台前，老人放下一枚发光的旧车票，特写票面模糊的地球航线", "rack-focus", "这趟车……还开吗？", "怅然"),
        ("女孩拿起车票对光细看，老人在货架间拿起一罐地球牌汽水", "pan", "", "怀旧"),
        ("两人隔着柜台对坐，汽水罐之间车票微微发光", "static", "二十年了，我一直想回家。", "温情"),
        ("老人离去的背影融入走廊灯光，女孩把车票贴在玻璃上，远处飞船起航", "pull-out", "", "希望"),
    ]
    shots = []
    for i in range(n_shots):
        desc, cam, dlg, mood = scenes[i % len(scenes)]
        shots.append({
            "shot_id": f"S01_{i+1:02d}",
            "description": f"{desc}（题材基调：{idea[:30]}）",
            "camera": cam,
            "duration": random.randint(4, 8),
            "dialogue": dlg,
            "mood": mood,
            "characters": [characters[0]["name"]] + ([characters[1]["name"]] if i >= 1 else []),
        })
    return {"characters": characters, "shots": shots}


# ---------- 文生图：首帧 ----------

async def gen_first_frame(shot: dict, style: str, pid: str = "") -> str:
    """返回首帧图片标识（mock 为伪文件名，real 为服务端 URL/base64 落盘路径）。"""
    img = get_cfg("image")
    if MODE == "real" and img["url"]:
        return await _first_frame_real(shot, style, img, pid)
    await asyncio.sleep(2.0)
    return f"mock_frame_{shot['shot_id']}_{uuid.uuid4().hex[:6]}.png"


async def _first_frame_real(shot: dict, style: str, img: dict, pid: str = "") -> str:
    prompt = f"{style}风格影视首帧，{shot['description']}，电影感构图，{shot['mood']}氛围"
    async with httpx.AsyncClient(trust_env=False, timeout=600) as client:
        r = await client.post(
            f"{img['url']}/v1/images/generations",
            headers={"Authorization": f"Bearer {img['key']}"},
            json={
                "model": img["model"],
                "prompt": prompt,
                "size": "1664x928",
                # Qwen-Image 注意：true_cfg_scale=4.0，guidance_scale 无效
                "true_cfg_scale": 4.0,
                "num_inference_steps": 50,
            },
        )
        r.raise_for_status()
        data = r.json()["data"][0]
    out_dir = _media_dir(pid, "frames")
    path = os.path.join(out_dir, f"{shot['shot_id']}_{uuid.uuid4().hex[:6]}.png")
    if "b64_json" in data:
        with open(path, "wb") as f:
            f.write(base64.b64decode(data["b64_json"]))
    elif "url" in data:
        async with httpx.AsyncClient(trust_env=False, timeout=120) as c:
            img = (await c.get(data["url"])).content
        with open(path, "wb") as f:
            f.write(img)
    return path


# ---------- 文生图：角色定妆照 ----------

async def gen_char_design(name: str, appearance: str, style: str, pid: str = "") -> str:
    """生成角色定妆照（正面半身设定照）。返回落盘路径。"""
    img = get_cfg("image")
    if MODE == "real" and img["url"]:
        return await _char_design_real(name, appearance, style, img, pid)
    await asyncio.sleep(1.0)
    return f"mock_design_{name}_{uuid.uuid4().hex[:6]}.png"


async def _char_design_real(name: str, appearance: str, style: str, img: dict, pid: str = "") -> str:
    prompt = (
        f"{style}风格角色设定照，{appearance}，"
        "正面半身像，看向镜头，表情自然，纯色简洁背景，"
        "影视定妆照质感，细节清晰"
    )
    async with httpx.AsyncClient(trust_env=False, timeout=600) as client:
        r = await client.post(
            f"{img['url']}/v1/images/generations",
            headers={"Authorization": f"Bearer {img['key']}"},
            json={
                "model": img["model"],
                "prompt": prompt,
                "size": "928x1664",   # 竖版设定照
                "true_cfg_scale": 4.0,
                "num_inference_steps": 50,
            },
        )
        r.raise_for_status()
        data = r.json()["data"][0]
    out_dir = _media_dir(pid, "designs")
    safe = "".join(c for c in name if c.isalnum())[:12] or "char"
    path = os.path.join(out_dir, f"design_{safe}_{uuid.uuid4().hex[:6]}.png")
    if "b64_json" in data:
        with open(path, "wb") as f:
            f.write(base64.b64decode(data["b64_json"]))
    elif "url" in data:
        async with httpx.AsyncClient(trust_env=False, timeout=120) as c:
            img = (await c.get(data["url"])).content
        with open(path, "wb") as f:
            f.write(img)
    return path


# ---------- 图生图：Qwen-Image-Edit（角色一致性 / 参考图编辑） ----------

async def edit_frame(shot: dict, frame, instruction: str, ref_image="", strength: float = 0.0, pid: str = "") -> str:
    """对首帧做图生图编辑。ref_image 可传角色参考图，实现跨镜头人物统一。

    接口形态（10.1.98.9:8002 实测）：
    - OpenAI 兼容 POST /v1/images/edits，multipart form-data
    - 编辑底图字段是 image（可多文件，frame 可传 list 传多张参考图），可选 reference_image 单文件作参考
    - strength：重绘幅度 0~1，默认 0（服务端自定）。以参考图生成全新构图时建议 0.8+
    - 返回 b64_json（response_format 默认就是）
    """
    bases = frame if isinstance(frame, (list, tuple)) else ([frame] if frame else [])
    # mock 伪文件名直接放行（real 模式必须真实存在）
    if MODE != "mock":
        bad = [p for p in bases if not p or not os.path.exists(p)]
        if bad:
            raise RuntimeError(f"图生图需要有效底图，frame={bad!r}")
    edit = get_cfg("edit")
    headers = {"Authorization": f"Bearer {edit['key']}"}
    # 多张底图用 list of tuples（dict 的同名字段会被覆盖，只能传一张）
    if MODE == "mock":
        files = [("image", ("mock.png", b"\x89PNG\r\n\x1a\n", "image/png"))]
    else:
        files = [("image", (os.path.basename(p), open(p, "rb"), "image/png")) for p in bases]
    if ref_image and os.path.exists(ref_image):
        files.append(("reference_image", (os.path.basename(ref_image), open(ref_image, "rb"), "image/png")))
    data = {
        "model": edit["model"],
        "prompt": instruction,
        "size": "1664x928",
        "num_inference_steps": "40",
        "guidance_scale": "4.0",
    }
    if strength:
        data["strength"] = str(strength)
    if MODE == "mock":
        await asyncio.sleep(1.0)
        resp = {"data": [{"b64_json": ""}]}
        payload = resp["data"][0]
        out_dir = _media_dir(pid, "frames")
        return os.path.join(out_dir, f"{shot['shot_id']}_edit_{uuid.uuid4().hex[:6]}.png")
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=600) as client:
            r = await client.post(f"{edit['url']}/v1/images/edits",
                                  headers=headers, data=data, files=files)
            r.raise_for_status()
            resp = r.json()
    finally:
        for _, f in files:
            f[1].close()
    payload = resp["data"][0]
    out_dir = _media_dir(pid, "frames")
    path = os.path.join(out_dir, f"{shot['shot_id']}_edit_{uuid.uuid4().hex[:6]}.png")
    if "b64_json" in payload:
        with open(path, "wb") as f:
            f.write(base64.b64decode(payload["b64_json"]))
    elif "url" in payload:
        async with httpx.AsyncClient(trust_env=False, timeout=120) as c:
            img = (await c.get(payload["url"])).content
        with open(path, "wb") as f:
            f.write(img)
    return path


# ---------- 图生视频 ----------

async def gen_video(shot: dict, frame: str, attempt: int = 1, on_progress=None, pid: str = "") -> dict:
    """返回 {video, ok}。on_progress(pct: float) 用于实时上报生成进度。"""
    video_cfg = get_cfg("video")
    if MODE == "real" and video_cfg["url"]:
        return await _video_real(shot, frame, on_progress, video_cfg, pid)
    # mock 模式模拟抽卡 + 假进度
    for i in range(10):
        await asyncio.sleep(0.3 + attempt * 0.2)
        if on_progress:
            on_progress((i + 1) * 10)
    ok = random.random() > 0.35
    return {
        "video": f"mock_clip_{shot['shot_id']}_a{attempt}_{uuid.uuid4().hex[:6]}.mp4" if ok else "",
        "ok": ok,
    }


async def _video_real(shot: dict, frame: str, on_progress=None, video_cfg: dict = None, pid: str = "") -> dict:
    """对接 vLLM-Omni 风格 /v1/videos（multipart form-data）。

    实测接口形态（10.1.98.9:8001）：
    - 必须用 form-data（JSON body 会报 prompt missing）
    - 首帧图字段是 input_reference（文件上传）；image_reference 是 base64 字符串字段，勿用
    - 该服务只加载了 I2V 模型：**无图请求会被引擎 500 拒绝**（"No image is provided"），
      所以上层必须保证 frame 有效后才调用本函数，不再做 T2V 降级
    - 任务轮询 GET /v1/videos/{id}，成片下载 /v1/videos/{id}/content
    """
    video_cfg = video_cfg or get_cfg("video")
    prompt = f"{shot['description']} {shot['camera']} camera movement, cinematic, {shot['mood']} mood"
    headers = {"Authorization": f"Bearer {video_cfg['key']}"}
    if not frame or not os.path.exists(frame):
        raise RuntimeError(f"无有效首帧图，I2V 服务拒收无图请求（frame={frame!r}）")
    data = {
        "model": video_cfg["model"],
        "prompt": prompt,
        "width": "1280", "height": "720",
        "num_frames": "81",           # 5 秒 @16fps
        "num_inference_steps": "40",
        "flow_shift": "12.0",         # I2V 推荐
    }
    async with httpx.AsyncClient(trust_env=False, timeout=120) as client:
        with open(frame, "rb") as f:
            r = await client.post(f"{video_cfg['url']}/v1/videos", headers=headers,
                                  data=data, files={"input_reference": f})
        r.raise_for_status()
        task = r.json()
    task_id = task.get("id") or task.get("task_id") or task.get("video_id")
    if not task_id:
        raise RuntimeError(f"视频任务创建失败，响应: {str(task)[:300]}")
    # 轮询异步任务
    async with httpx.AsyncClient(trust_env=False, timeout=60) as client:
        for _ in range(720):  # 最多 1 小时
            await asyncio.sleep(5)
            st = (await client.get(f"{video_cfg['url']}/v1/videos/{task_id}", headers=headers)).json()
            # 防御：详情接口返回 "Video not found" 时跳过本轮（列表接口的 id 是截断格式，勿混用）
            if isinstance(st.get("error"), dict) and st["error"].get("code") == 404:
                continue
            status = (st.get("status") or "").lower()
            if on_progress:
                try:
                    on_progress(float(st.get("progress") or 0) * 100)
                except (TypeError, ValueError):
                    pass
            if status in ("succeeded", "completed", "done", "success"):
                # 成片下载到本地，前端经 /media/videos/ 播放（远端 URL 需认证头，浏览器播不了）
                remote_url = st.get("url") or f"{video_cfg['url']}/v1/videos/{task_id}/content"
                out_dir = _media_dir(pid, "videos")
                local_path = os.path.join(out_dir, f"{shot['shot_id']}_{task_id[-8:]}.mp4")
                if not os.path.exists(local_path):
                    async with httpx.AsyncClient(trust_env=False, timeout=600) as dl:
                        resp = await dl.get(remote_url, headers=headers)
                        resp.raise_for_status()
                        with open(local_path, "wb") as f:
                            f.write(resp.content)
                return {"video": local_path, "ok": True}
            if status in ("failed", "error"):
                err = (st.get("error") or {}).get("message", "") if isinstance(st.get("error"), dict) else st.get("error")
                raise RuntimeError(f"视频任务失败: {str(err)[:200]}")
    return {"video": "", "ok": False}


# ---------- TTS：VoxCPM2 (OpenAI 兼容 /v1/audio/speech) ----------

async def gen_tts(text: str, voice_desc: str = "", shot_id: str = "tts", pid: str = "") -> str:
    """台词转语音，返回 wav 落盘路径。

    接口形态（vllm-omni Qwen3-TTS @ 8004，官方文档 text_to_speech）：
    - OpenAI 兼容 POST /v1/audio/speech，JSON body
    - VoiceDesign 变体支持 instructions 音色描述（自然语言定义音色，无需参考音频）
    - 返回音频二进制（response_format=wav）
    - 失败/无台词返回空串，不阻塞流水线
    """
    if not text or not text.strip():
        return ""
    tts = get_cfg("tts")
    payload = {
        "model": tts["model"],
        "input": text,
        "response_format": "wav",
    }
    # 该部署未配置 speakers，CustomVoice 会 400；Base 需要参考音频克隆。
    # 因此统一走 VoiceDesign：有音色描述用描述，没有则给通用默认描述。
    payload["task_type"] = "VoiceDesign"
    payload["instructions"] = (voice_desc or "自然清晰的普通话旁白，语速平稳，音色中性耐听").strip()
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=300) as client:
            r = await client.post(
                f"{tts['url']}/v1/audio/speech",
                headers={"Authorization": f"Bearer {tts['key']}"},
                json=payload,
            )
            if r.status_code >= 400:
                # 带上响应体，避免 400 只有状态码看不到原因
                detail = (r.text or "").strip()[:200]
                raise RuntimeError(f"HTTP {r.status_code}: {detail}")
            audio = r.content
    except Exception as e:
        raise RuntimeError(f"TTS 合成失败: {str(e)[:150]}") from e
    out_dir = _media_dir(pid, "audio")
    path = os.path.join(out_dir, f"{shot_id}_{uuid.uuid4().hex[:6]}.wav")
    with open(path, "wb") as f:
        f.write(audio)
    return path


# ---------- ffmpeg 合成：视频 + 配音 -> 有声镜头；多镜头 -> 正片 ----------

def _run_ffmpeg(args: list[str], timeout: int = 600):
    import subprocess
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {r.stderr[:300]}")


def mux_video_audio(video: str, audio: str, out: str):
    """视频(无声)+配音 -> 有声视频。视频流直接 copy，音频转 AAC。"""
    _run_ffmpeg(["-i", video, "-i", audio,
                 "-c:v", "copy", "-c:a", "aac", "-shortest", out])


def concat_final(clips: list[str], out: str, pid: str = ""):
    """多段视频按顺序拼接成正片。

    关键点：统一转码时给没有音轨的片段垫一条静音轨（anullsrc）。
    否则「无音轨片段 + 有音轨片段」混合 concat 时，输出会以第一个文件的
    流布局为准，音频流整条丢失——表现为正片完全没声音。
    """
    if not clips:
        raise RuntimeError("没有可拼接的片段")
    import subprocess
    tmp_dir = _media_dir(pid, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    # 1) 统一转码（分辨率/帧率/编码一致才能无损 concat），无音轨的垫静音
    norm = []
    for i, c in enumerate(clips):
        nc = os.path.join(tmp_dir, f"norm_{uuid.uuid4().hex[:6]}.mp4")
        has_audio = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", c],
            capture_output=True, text=True).stdout.strip() != ""
        args = ["-i", c,
                "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
                "-r", "24", "-c:v", "libx264", "-preset", "fast", "-crf", "20"]
        if has_audio:
            args += ["-c:a", "aac", "-ar", "44100", "-ac", "2"]
        else:
            # 垫静音轨：第二路输入 anullsrc，时长对齐视频（-shortest 防止静音源无限长）
            args = (["-i", c,
                     "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                     "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2",
                     "-r", "24", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                     "-c:a", "aac", "-ar", "44100", "-ac", "2", "-shortest", nc])
            _run_ffmpeg(args, timeout=1800)
            norm.append(nc)
            continue
        args.append(nc)
        _run_ffmpeg(args, timeout=1800)
        norm.append(nc)
    # 2) concat list + 拼接
    lst = os.path.join(tmp_dir, f"concat_{uuid.uuid4().hex[:6]}.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for nc in norm:
            f.write(f"file '{nc}'\n")
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out], timeout=1800)
    for nc in norm:
        try:
            os.remove(nc)
        except OSError:
            pass
    os.remove(lst)
