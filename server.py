"""AI 网剧流水线编排 Web 服务（FastAPI）。

启动: /Users/cc/.workbuddy/binaries/python/envs/default/bin/python server.py
访问: http://127.0.0.1:8020
"""
import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import providers
import media as media_store
import auth

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI(title="AI Drama Pipeline")

# 媒体静态服务：挂存储根整体（按项目分目录，URL 形如 /media/{pid}/frames/xxx.png）
MEDIA_ROOT = media_store.media_root()
app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")
# 前端静态资源（css/js 已从单文件拆分；页面本体由 / 路由返回）
app.mount("/web", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")), name="web")

# ---------------- 登录鉴权（Cookie 会话；未配置账号前开放自助初始化） ----------------

# 无需登录即可访问的路径前缀（静态资源 + 认证接口本身）
PUBLIC_PREFIXES = ("/web/", "/media/", "/api/auth/")


def _session_user(request: Request) -> str | None:
    return auth.user_of(request.cookies.get(auth.SESSION_COOKIE))


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path == "/login" or path.startswith(PUBLIC_PREFIXES):
        return await call_next(request)
    if not _session_user(request):
        if path.startswith("/api/"):
            return JSONResponse({"detail": "未登录"}, status_code=401)
        # 页面请求（含 /）：直接带跳转目标重定向到登录页
        return HTMLResponse("", status_code=302, headers={"Location": f"/login?next={path}"})
    return await call_next(request)


class AuthIn(BaseModel):
    username: str
    password: str


@app.get("/api/auth/state")
async def auth_state():
    """前端判断：是否已有账号（决定显示登录还是初始化）。"""
    return {"has_users": auth.has_users()}


@app.post("/api/auth/setup")
async def auth_setup(body: AuthIn):
    """仅当系统里还没有任何账号时可用：创建第一个（管理员）账号。"""
    if auth.has_users():
        raise HTTPException(403, "已存在账号，请直接登录")
    if not auth.create_user(body.username, body.password):
        raise HTTPException(400, "创建失败：用户名为空或密码少于 6 位")
    token = auth.new_session(body.username.strip())
    resp = JSONResponse({"ok": True, "username": body.username.strip()})
    resp.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.post("/api/auth/login")
async def auth_login(body: AuthIn):
    if not auth.verify(body.username, body.password):
        raise HTTPException(401, "用户名或密码错误")
    token = auth.new_session(body.username.strip())
    resp = JSONResponse({"ok": True, "username": body.username.strip()})
    resp.set_cookie(auth.SESSION_COOKIE, token, max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax")
    return resp


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    auth.drop_session(request.cookies.get(auth.SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "login.html"))


# ---------------- 数据层（SQLite，单文件库 data/drama.db） ----------------

import db as store


def _load_projects() -> dict:
    """{pid: proj_dict}，与结构化前的 JSON 版本同构，业务代码零改动。"""
    return store.load_projects()


# ---------------- SSE 实时推送（事件广播：数据变更 -> 前端拉一次） ----------------

import collections

class SseHub:
    """极简 SSE hub：版本号 + 每客户端队列。

    数据变更只 bump 版本并入队轻量事件（不含数据本身），前端收到后调一次
    现有的 /api/projects 做差分渲染——省流且复用已有渲染逻辑。
    """
    def __init__(self):
        self.version = 0
        self._subs: dict[int, asyncio.Queue] = {}
        self._n = 0

    def publish(self, what: str = "update"):
        self.version += 1
        evt = {"v": self.version, "what": what, "ts": time.time()}
        for q in list(self._subs.values()):
            try:
                q.put_nowait(evt)
            except Exception:
                pass

    async def subscribe(self):
        self._n += 1
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subs[self._n] = q
        try:
            # 先发一个当前版本，前端立即对齐
            yield f"data: {json.dumps({'v': self.version, 'what': 'hello'})}\n\n"
            while True:
                evt = await q.get()
                yield f"data: {json.dumps(evt)}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            self._subs.pop(self._n, None)

    @property
    def n_clients(self) -> int:
        return len(self._subs)

sse_hub = SseHub()


@app.get("/api/events")
async def sse_events():
    """SSE 端点。事件格式 data: {"v": 版本号, "what": "update"}。"""
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        sse_hub.subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _save_projects(data: dict):
    """保存一个或多个项目；传整表 dict 时逐个 upsert（各自独立行，互不覆盖）。"""
    changed = False
    for proj in data.values():
        store.save_project(proj)
        changed = True
    if changed:
        sse_hub.publish("update")


def _save_one(proj: dict):
    """只保存单个被修改的项目（内部流水线任务推荐用这个，减少全表写放大）。"""
    store.save_project(proj)
    sse_hub.publish("update")


class ProjectIn(BaseModel):
    title: str
    idea: str
    n_shots: int = 6
    style: str = "写实电影感"


# ---------------- API ----------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html"))


@app.post("/api/projects")
async def create_project(p: ProjectIn):
    projects = _load_projects()
    pid = uuid.uuid4().hex[:8]
    projects[pid] = {
        "id": pid,
        "title": p.title,
        "idea": p.idea,
        "style": p.style,
        "n_shots": p.n_shots,
        "status": "created",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "shots": [],
        "log": [],
    }
    _save_projects(projects)
    return projects[pid]


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str):
    """删除项目。媒体按项目独立存储，直接删整个媒体目录。运行中项目先拒绝。"""
    if pid in _RUNNING:
        raise HTTPException(409, "项目运行中，等跑完或重启服务后再删")
    projects = _load_projects()
    if pid not in projects:
        raise HTTPException(404)
    proj = projects.pop(pid)
    freed = media_store.delete_project_media(pid)   # 目录整体删除 + 资产表清理
    store.delete_project(pid)
    sse_hub.publish("delete")
    return {"msg": f"已删除项目 {proj['title']}，释放 {freed // 1024 // 1024}MB 媒体空间"}


class DialogueIn(BaseModel):
    dialogue: str = ""


@app.post("/api/shots/{sid}/dialogue")
async def update_dialogue(sid: str, body: DialogueIn):
    """修改/清空某镜头台词（配音前可随时调整）。清空时同步删除已生成的配音。"""
    projects = _load_projects()
    for proj in projects.values():
        for shot in proj["shots"]:
            if shot["shot_id"] == sid:
                shot["dialogue"] = body.dialogue.strip()
                if not shot["dialogue"] and shot.get("audio") and os.path.exists(shot["audio"]):
                    try:
                        os.remove(shot["audio"])
                    except OSError:
                        pass
                    shot["audio"] = ""
                    shot["audio_path"] = ""
                store.save_project(proj)
                sse_hub.publish("update")
                return {"msg": f"台词已更新：{sid}"}
    raise HTTPException(404)


def _media_view(proj: dict) -> dict:
    """把本地文件路径转成前端可访问的 /media/... URL（mock 伪文件名原样保留）。"""
    import copy
    out = copy.deepcopy(proj)
    for s in out.get("shots", []):
        if s.get("frame"):
            u = media_store.url_for(s["frame"])
            if u:
                s["frame_url"] = u
        if s.get("video"):
            u = media_store.url_for(s["video"])
            if u:
                s["video_url"] = u
            elif str(s["video"]).startswith("http"):
                s["video_url"] = s["video"]   # 旧数据遗留的远端 URL，直接透传
        if s.get("video_final"):
            u = media_store.url_for(s["video_final"])
            if u:
                s["video_final_url"] = u
    for c in out.get("characters") or []:
        u = media_store.url_for(c.get("design") or "")
        if u:
            c["design_url"] = u
    u = media_store.url_for(out.get("final_video") or "")
    if u:
        out["final_url"] = u
    return out


@app.get("/api/projects")
async def list_projects():
    return [_media_view(p) for p in _load_projects().values()]


@app.get("/api/projects/{pid}")
async def get_project(pid: str):
    projects = _load_projects()
    if pid not in projects:
        raise HTTPException(404)
    return _media_view(projects[pid])


@app.get("/api/projects/{pid}/tasks")
async def list_tasks(pid: str):
    """项目的任务历史（含状态与断点进度）。"""
    if pid not in _load_projects():
        raise HTTPException(404)
    out = []
    for t in store.tasks_for_pid(pid):
        t["progress"] = json.loads(t.get("progress") or "{}")
        out.append(t)
    return out


@app.get("/api/projects/{pid}/assets")
async def project_assets(pid: str):
    """项目资产：登记清单 + 各类媒体统计。"""
    projects = _load_projects()
    if pid not in projects:
        raise HTTPException(404)
    return {"stats": media_store.project_stats(pid), "assets": store.assets_for_pid(pid)}


@app.get("/api/mode")
async def mode_info():
    cfg = providers.all_cfg()
    # 探测各图像/视频后端是否可达（刚配置时可能未启动）
    async def _probe(url: str, key: str) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                return (await client.get(f"{url}/v1/models",
                                         headers={"Authorization": f"Bearer {key}"})).status_code == 200
        except Exception:
            return False

    llm_up = bool(cfg["llm"]["url"] and cfg["llm"]["key"])
    image_up = await _probe(cfg["image"]["url"], cfg["image"]["key"])
    wan_up = await _probe(cfg["video"]["url"], cfg["video"]["key"])
    edit_up = await _probe(cfg["edit"]["url"], cfg["edit"]["key"])
    tts_up = await _probe(cfg["tts"]["url"], cfg["tts"]["key"])
    note = "后端已配置" if llm_up else "未配置 real 后端，仅 mock 可用"
    if not image_up:
        note += f"；文生图服务 {cfg['image']['url']} 暂不可达，首帧/定妆环节会失败"
    if not wan_up:
        note += f"；视频服务 {cfg['video']['url']} 暂不可达，视频环节会失败"
    if not edit_up:
        note += f"；图生图服务 {cfg['edit']['url']} 暂不可达，角色一致性环节跳过"
    if not tts_up:
        note += f"；TTS 服务 {cfg['tts']['url']} 暂不可达，配音环节不可用"
    return {
        "mode": providers.MODE,
        "llm_up": llm_up,
        "image_up": image_up,
        "wan_up": wan_up,
        "edit_up": edit_up,
        "tts_up": tts_up,
        "note": note,
    }


# ---------------- 模型后端配置（在线可改，落盘 config.json） ----------------

@app.get("/api/config")
async def get_config():
    """返回各组后端配置；key 打码显示（有值显示掩码，前端据此判断是否已设）。"""
    cfg = providers.all_cfg()
    out = {}
    for g, v in cfg.items():
        key = v.get("key") or ""
        out[g] = {
            "url": v.get("url") or "",
            "model": v.get("model") or "",
            "key_set": bool(key),
            "key_mask": (key[:4] + "****" + key[-4:]) if len(key) > 8 else ("****" if key else ""),
        }
    return out


@app.put("/api/config")
async def update_config(body: dict):
    """保存在线配置。key 传空 = 不修改现有 key；改完立即生效（每次调用动态读取）。"""
    providers.save_cfg(body or {})
    return {"msg": "配置已保存并生效"}


class ConfigTest(BaseModel):
    group: str


@app.post("/api/config/test")
async def test_config(body: ConfigTest):
    """连通性测试：探测指定组的后端是否可达。"""
    cfg = providers.get_cfg(body.group)
    ok = False
    err = ""
    try:
        import httpx
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            if body.group == "llm":
                # LLM 网关不一定有 /v1/models，用最小 chat 请求探测
                r = await client.post(f"{cfg['url']}/chat/completions",
                                      headers={"Authorization": f"Bearer {cfg['key']}"},
                                      json={"model": cfg["model"], "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1})
                ok = r.status_code == 200
            else:
                r = await client.get(f"{cfg['url']}/v1/models",
                                     headers={"Authorization": f"Bearer {cfg['key']}"})
                ok = r.status_code == 200
            if not ok:
                err = f"HTTP {r.status_code}"
    except Exception as e:
        err = str(e)[:150]
    return {"ok": ok, "error": err}


# 运行中流水线跟踪（仅内存：服务重启后自然清空，不会死锁）
_RUNNING: set[str] = set()


# ---------------- 任务队列化：任务落库 + 断点续跑 ----------------

def _enqueue(pid: str, kind: str, stage: str, payload: dict | None = None) -> str:
    """创建任务记录并启动执行。kind: design/frames/videos/tts/all。"""
    tid = uuid.uuid4().hex[:8]
    store.create_task(tid, pid, kind, stage, payload)
    sse_hub.publish("task")
    asyncio.create_task(_task_runner(tid, pid, stage))
    return tid


async def _task_runner(tid: str, pid: str, stage: str):
    """任务执行包装：running -> done/failed 状态落库。服务重启后 unfinished_tasks() 捞回来续跑。"""
    store.update_task(tid, status="running")
    try:
        if stage == "videos":
            await _run_videos_only(pid, tid=tid)
        elif stage == "frames_from_design":
            await _run_frames_only(pid, tid=tid)
        elif stage == "tts":
            await _tts_and_mux(pid)
        else:
            await _pipeline(pid, stage, tid=tid)
        store.finish_task(tid, "done")
        sse_hub.publish("task")
    except asyncio.CancelledError:
        store.finish_task(tid, "canceled")
        raise
    except Exception as e:
        store.finish_task(tid, "failed", error=str(e)[:300])
        projects = _load_projects()
        if pid in projects:
            projects[pid]["status"] = "error"
            projects[pid]["log"].append(f"[{time.strftime('%H:%M:%S')}] 任务异常终止: {str(e)[:200]}")
            _save_one(projects[pid])
    finally:
        _RUNNING.discard(pid)


def _task_progress(tid: str, **kv):
    """流水线阶段进度上报（断点信息：跑到哪一步了）。"""
    try:
        store.update_task(tid, progress=kv)
    except Exception:
        pass  # 进度上报失败不影响主流程


async def _resume_unfinished():
    """服务启动时恢复未完成任务：running 视为被重启打断，重新入队续跑。"""
    tasks = store.unfinished_tasks()
    if not tasks:
        return
    resumed = []
    for t in tasks:
        pid = t["pid"]
        projects = _load_projects()
        if pid not in projects:
            store.finish_task(t["id"], "failed", error="项目已删除")
            continue
        if pid in _RUNNING:
            continue  # 同一项目只恢复一个任务，其余保持 queued 等下一轮
        _RUNNING.add(pid)
        resumed.append(f"{t['kind']}({t['id']})")
        prog = json.loads(t["progress"] or "{}")
        stage = t["stage"] or "all"
        # 断点续跑：design 阶段已完成就跳到 frames，frames 完成就跳到 videos
        if prog.get("design_done") and stage in ("all", "design"):
            stage = "frames_from_design" if not prog.get("frames_done") else "videos"
        if prog.get("frames_done") and stage in ("all", "design", "frames", "frames_from_design"):
            stage = "videos"
        asyncio.create_task(_task_runner(t["id"], pid, stage))
    if resumed:
        projects = _load_projects()
        for pid in list(_RUNNING):
            if pid in projects:
                projects[pid]["log"].append(
                    f"[{time.strftime('%H:%M:%S')}] 服务重启，恢复任务：{', '.join(resumed)}"
                )
                _save_one(projects[pid])


@app.post("/api/projects/{pid}/run")
async def run_pipeline(pid: str, stage: str = "all"):
    """启动流水线。?stage=design 只跑到定妆照确认；?stage=frames 跑到首帧确认；默认 all。"""
    projects = _load_projects()
    if pid not in projects:
        raise HTTPException(404)
    if pid in _RUNNING:
        return {"msg": "已在运行中"}
    _RUNNING.add(pid)
    _enqueue(pid, "pipeline", stage)
    return {"msg": f"流水线已启动（stage={stage}）"}


@app.post("/api/projects/{pid}/run_frames")
async def run_frames(pid: str):
    """定妆确认后，启动首帧阶段（跳过分镜/定妆，直接批量首帧）。"""
    projects = _load_projects()
    if pid not in projects:
        raise HTTPException(404)
    if pid in _RUNNING:
        return {"msg": "已在运行中"}
    if not projects[pid].get("characters"):
        raise HTTPException(400, "还没有角色档案，先跑定妆阶段")
    _RUNNING.add(pid)
    _enqueue(pid, "frames", "frames_from_design")
    return {"msg": "首帧阶段已启动"}


@app.post("/api/projects/{pid}/run_videos")
async def run_videos(pid: str):
    """首帧确认后，启动视频阶段（跳过首帧生成，直接 I2V）。"""
    projects = _load_projects()
    if pid not in projects:
        raise HTTPException(404)
    if pid in _RUNNING:
        return {"msg": "已在运行中"}
    proj = projects[pid]
    ready = [s for s in proj["shots"] if s.get("frame")]
    if not ready:
        raise HTTPException(400, "还没有任何首帧，先跑首帧阶段")
    _RUNNING.add(pid)
    _enqueue(pid, "videos", "videos")
    return {"msg": f"视频阶段已启动（{len(ready)} 个镜头有首帧）"}


async def _pipeline_guard(pid: str, stage: str = "all"):
    """旧入口兼容（regenerate 等直接调用的路径不走任务表）。"""
    try:
        if stage == "videos":
            await _run_videos_only(pid)
        elif stage == "frames_from_design":
            await _run_frames_only(pid)
        else:
            await _pipeline(pid, stage)
    finally:
        _RUNNING.discard(pid)


async def _run_frames_only(pid: str, tid: str = ""):
    """只跑首帧阶段（定妆已确认），完成后停在设计好的确认点。"""
    projects = _load_projects()
    proj = projects[pid]
    proj["status"] = "running"
    _save_projects(projects)
    _task_progress(tid, phase="frames") if tid else None
    await _gen_all_frames(proj, projects)
    proj["status"] = "frames_ready"
    _save_projects(projects)
    if tid:
        _task_progress(tid, frames_done=True)


async def _run_videos_only(pid: str, tid: str = ""):
    """只跑视频阶段（首帧已确认）。"""
    projects = _load_projects()
    proj = projects[pid]
    proj["status"] = "running"
    proj["pipeline_started_at"] = time.time()
    _save_projects(projects)
    if tid:
        _task_progress(tid, phase="videos")
    for shot in proj["shots"]:
        if shot["video_status"] != "done":
            await _produce_video(proj, shot)
            if tid:
                done = sum(1 for s in proj["shots"] if s["video_status"] == "done")
                _task_progress(tid, videos_done=done, videos_total=len(proj["shots"]))
    proj["status"] = "done"
    proj["elapsed_total"] = round(time.time() - proj.get("pipeline_started_at", time.time()))
    done = sum(1 for s in proj["shots"] if s["video_status"] == "done")
    proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 视频阶段完成：{done}/{len(proj['shots'])} 成片，总耗时 {proj['elapsed_total']}s")
    _save_projects(projects)


@app.post("/api/shots/{sid}/regenerate")
async def regenerate_shot(sid: str, mode: str = "video"):
    """重抽镜头。mode=frame 只重生成首帧（含角色统一），mode=video 重跑视频。"""
    projects = _load_projects()
    for proj in projects.values():
        for shot in proj["shots"]:
            if shot["shot_id"] == sid:
                if mode == "frame":
                    shot["frame"] = ""
                    shot["video_status"] = "pending"
                    asyncio.create_task(_regen_frame(proj, shot))
                    _save_projects(projects)
                    return {"msg": f"镜头 {sid} 首帧重抽中"}
                asyncio.create_task(_regen_video(proj, shot))
                _save_projects(projects)
                return {"msg": f"镜头 {sid} 视频重抽中"}
    raise HTTPException(404)


class CharName(BaseModel):
    name: str


@app.post("/api/characters/{pid}/regenerate")
async def regenerate_char(pid: str, body: CharName):
    """重抽某角色的定妆照。"""
    projects = _load_projects()
    if pid not in projects:
        raise HTTPException(404)
    proj = projects[pid]
    char = next((c for c in proj.get("characters", []) if c["name"] == body.name), None)
    if not char:
        raise HTTPException(404, "角色不存在")
    char["design"] = ""
    _RUNNING.add(pid)

    async def _do():
        try:
            await _gen_char_design(proj, char)
        finally:
            _RUNNING.discard(pid)
    asyncio.create_task(_do())
    _save_projects(projects)
    return {"msg": f"角色 {body.name} 定妆重抽中"}


async def _regen_frame(proj: dict, shot: dict):
    """重抽单镜头首帧：按镜头出场角色把定妆照一起传给 Edit（与批量阶段同源）；
    一个定妆照都没有时退回「文生图 + Edit 统一」的旧逻辑。"""
    _RUNNING.add(proj["id"])
    try:
        char_refs = _shot_char_refs(proj, shot)
        if char_refs:
            await _gen_frame_from_ref(proj, shot, char_refs)
        else:
            await _gen_frame_only(proj, shot)
            if shot.get("frame") and proj.get("char_ref"):
                await _unify_character(proj, shot, proj["char_ref"])
    finally:
        _RUNNING.discard(proj["id"])


async def _regen_video(proj: dict, shot: dict):
    _RUNNING.add(proj["id"])
    try:
        await _produce_video(proj, shot)
    finally:
        _RUNNING.discard(proj["id"])


# ---------------- 配音与合成 ----------------

class VoiceIn(BaseModel):
    voice_desc: str = ""   # 音色描述，如「年轻男性，声音低沉温暖」；空则用默认音色


@app.post("/api/projects/{pid}/tts")
async def gen_all_tts(pid: str, body: VoiceIn | None = None):
    """为所有已有视频的镜头生成配音（有 dialogue 才合成），随后音画合成。"""
    projects = _load_projects()
    if pid not in projects:
        raise HTTPException(404)
    if pid in _RUNNING:
        return {"msg": "已在运行中"}
    proj = projects[pid]
    targets = [s for s in proj["shots"] if s.get("video") and os.path.exists(s["video"])]
    if not targets:
        raise HTTPException(400, "还没有成片视频，先跑完视频阶段")
    voice_desc = (body.voice_desc if body else "") or proj.get("voice_desc", "")
    proj["voice_desc"] = voice_desc
    _RUNNING.add(pid)
    _enqueue(pid, "tts", "tts")
    return {"msg": f"配音+合成已启动（{len(targets)} 个镜头）"}


async def _tts_and_mux_guard(pid: str, tid: str = ""):
    try:
        await _tts_and_mux(pid)
    finally:
        _RUNNING.discard(pid)


async def _tts_and_mux(pid: str):
    """逐镜头：dialogue -> TTS -> 音画合成；然后拼接正片。"""
    import providers
    projects = _load_projects()
    proj = projects[pid]
    proj["status"] = "dubbing"
    proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 配音阶段开始（音色：{proj.get('voice_desc') or '默认'}）")
    _save_projects(projects)

    clips = []
    for shot in proj["shots"]:
        video = shot.get("video", "")
        if not video or not os.path.exists(video):
            continue
        audio_path = ""
        dialogue = (shot.get("dialogue") or "").strip()
        if dialogue:
            try:
                audio_path = await providers.gen_tts(dialogue, proj.get("voice_desc", ""),
                                                     shot_id=shot["shot_id"], pid=pid)
                shot["audio"] = audio_path
                media_store.register(pid, "audio", audio_path, {"shot": shot["shot_id"]})
            except Exception as e:
                proj["log"].append(f"[{time.strftime('%H:%M:%S')}] {shot['shot_id']} TTS 失败（保留无声视频）: {str(e)[:120]}")
                _save_one(proj)
        if audio_path and os.path.exists(audio_path):
            muxed = media_store.new_path(pid, "videos",
                                         f"{shot['shot_id']}_mux_{uuid.uuid4().hex[:6]}.mp4")
            try:
                providers.mux_video_audio(video, audio_path, muxed)
                shot["video_final"] = muxed
                media_store.register(pid, "videos", muxed, {"shot": shot["shot_id"], "muxed": True})
                clips.append(muxed)
            except Exception as e:
                proj["log"].append(f"[{time.strftime('%H:%M:%S')}] {shot['shot_id']} 音画合成失败（保留无声视频）: {str(e)[:120]}")
                _save_one(proj)
                clips.append(video)
        else:
            clips.append(video)

    # 拼接正片
    if clips:
        final = media_store.new_path(pid, "finals", f"{pid}_final_{uuid.uuid4().hex[:6]}.mp4")
        try:
            providers.concat_final(clips, final, pid=pid)
            proj["final_video"] = final
            media_store.register(pid, "finals", final, {"clips": len(clips)})
            proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 正片拼接完成：{len(clips)} 个镜头")
        except Exception as e:
            proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 正片拼接失败: {str(e)[:150]}")

    proj["status"] = "done"
    _save_projects(projects)


@app.get("/api/projects/{pid}/final")
async def get_final(pid: str):
    """获取正片文件（流式播放/下载）。"""
    projects = _load_projects()
    if pid not in projects:
        raise HTTPException(404)
    final = projects[pid].get("final_video")
    if not final or not os.path.exists(final):
        raise HTTPException(404, "还没有正片，先跑配音合成")
    return FileResponse(final, media_type="video/mp4", filename=os.path.basename(final))


# ---------------- 流水线编排 ----------------

async def _pipeline(pid: str, stage: str = "all", tid: str = ""):
    """stage: design=跑到定妆确认; frames=定妆+首帧后停; all=一跑到底。
    tid 非空时上报阶段进度（断点续跑依据：design_done / frames_done）。"""
    projects = _load_projects()
    proj = projects[pid]
    proj["status"] = "running"
    proj["pipeline_started_at"] = time.time()
    proj["stage"] = stage
    _save_projects(projects)

    # 阶段1: LLM 分镜 + 角色档案（已有镜头则复用）
    if not proj.get("shots"):
        proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 开始分镜生成（{providers.MODE} 模式）")
        _save_projects(projects)
        if tid:
            _task_progress(tid, phase="storyboard")
        try:
            plan = await providers.gen_storyboard(proj["idea"], proj["n_shots"])
            for s in plan["shots"]:
                s.update({"frame": "", "video": "", "video_status": "pending", "attempts": 0})
            proj["shots"] = plan["shots"]
            proj["characters"] = plan.get("characters", [])
            proj["log"].append(
                f"[{time.strftime('%H:%M:%S')}] 分镜完成，共 {len(plan['shots'])} 个镜头，"
                f"{len(proj['characters'])} 个角色：{'、'.join(c['name'] for c in proj['characters']) or '（未识别）'}"
            )
            _save_projects(projects)
        except Exception as e:
            proj["status"] = "error"
            proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 分镜出错: {e}")
            _save_projects(projects)
            raise  # 交给 _task_runner 记 failed，不再静默吞掉

    # 阶段2: 角色定妆（每个主要角色生成设定照，人工确认后才进首帧）
    chars = proj.get("characters") or []
    todo_designs = [c for c in chars if not c.get("design")]
    if todo_designs:
        proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 角色定妆：{len(todo_designs)} 个角色（文生图设定照）")
        _save_projects(projects)
        if tid:
            _task_progress(tid, phase="design")
        await asyncio.gather(
            *[_gen_char_design(proj, c) for c in todo_designs],
            return_exceptions=True,
        )
        ok = sum(1 for c in chars if c.get("design"))
        proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 定妆完成：{ok}/{len(chars)} 张，请检查角色形象")

    if stage == "design":
        proj["status"] = "design_ready"
        _save_projects(projects)
        if tid:
            _task_progress(tid, design_done=True, phase="design_ready")
        return

    if tid:
        _task_progress(tid, design_done=True)

    # 阶段3: 首帧批量生成（参考图 = 主角色定妆照）
    await _gen_all_frames(proj, projects)

    if stage in ("frames", "frames_from_design"):
        proj["status"] = "frames_ready"
        _save_projects(projects)
        if tid:
            _task_progress(tid, frames_done=True, phase="frames_ready")
        return

    if tid:
        _task_progress(tid, frames_done=True)

    # 阶段4: 逐镜头视频（串行，视频后端一次跑一条）
    for shot in proj["shots"]:
        if shot["video_status"] != "done":
            await _produce_video(proj, shot)
            if tid:
                done = sum(1 for s in proj["shots"] if s["video_status"] == "done")
                _task_progress(tid, videos_done=done, videos_total=len(proj["shots"]))
    proj["status"] = "done"
    proj["elapsed_total"] = round(time.time() - proj.get("pipeline_started_at", time.time()))
    done = sum(1 for s in proj["shots"] if s["video_status"] == "done")
    proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 全部镜头完成：{done}/{len(proj['shots'])} 成片，总耗时 {proj['elapsed_total']}s")
    _save_projects(projects)


def _main_char_ref(proj: dict) -> str:
    """主角色定妆照路径（第一个有 design 的角色），兜底取第一个镜头首帧。
    mock 模式下 design 是伪文件名，直接透传。"""
    for c in proj.get("characters") or []:
        d = c.get("design") or ""
        if d and (providers.MODE == "mock" or os.path.exists(d)):
            return d
    return next((s["frame"] for s in proj.get("shots", []) if s.get("frame")), "")


def _shot_char_refs(proj: dict, shot: dict) -> list:
    """该镜头出场角色的全部定妆照（有 design 的优先，按角色顺序）。
    mock 模式下伪文件名直接透传。"""
    shot_chars = shot.get("characters") or []
    refs = []
    for c in proj.get("characters") or []:
        d = c.get("design") or ""
        if c["name"] in shot_chars and d and (providers.MODE == "mock" or os.path.exists(d)):
            refs.append(d)
    # 一个定妆照都没匹配上时退回主角色定妆照，再不行退回第一个镜头首帧
    if not refs:
        main = _main_char_ref(proj)
        if main:
            refs = [main]
    return refs


async def _gen_all_frames(proj: dict, projects: dict):
    """首帧批量：每个镜头按出场角色把对应定妆照全部作为参考图生图（人物从源头锁定）。"""
    todo_frames = [s for s in proj["shots"] if not s.get("frame")]
    if not todo_frames:
        return
    anchor = todo_frames[0]
    anchor_refs = _shot_char_refs(proj, anchor)
    proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 生成定妆首帧：{anchor['shot_id']}（参考：{len(anchor_refs)} 张角色定妆照）")
    _save_projects(projects)

    if anchor_refs:
        await _gen_frame_from_ref(proj, anchor, anchor_refs)
    else:
        await _gen_frame_only(proj, anchor)
    ref_frame = anchor.get("frame") or next((s["frame"] for s in proj["shots"] if s.get("frame")), "")
    if not ref_frame:
        proj["status"] = "error"
        proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 定妆首帧生成失败，首帧阶段终止（页面可点「重试首帧」重跑）")
        _save_projects(projects)
        return

    # 其余镜头：各自按出场角色传定妆照，匹配不上时退回锚点首帧
    rest = [s for s in todo_frames[1:] if not s.get("frame")]
    if rest:
        proj["char_ref"] = ref_frame
        proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 参考图图生图：{len(rest)} 个镜头（Edit 模型，按镜头出场角色传定妆照）")
        _save_projects(projects)
        await asyncio.gather(
            *[_gen_frame_from_ref(proj, s, _shot_char_refs(proj, s) or ref_frame) for s in rest],
            return_exceptions=True,
        )

    ok_n = sum(1 for s in proj["shots"] if s.get("frame"))
    proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 首帧阶段完成：{ok_n}/{len(proj['shots'])} 张，请检查确认")
    _save_projects(projects)


async def _gen_char_design(proj: dict, char: dict):
    """生成单个角色的定妆照。"""
    t0 = time.time()
    for attempt in range(1, 4):
        try:
            design = await providers.gen_char_design(char["name"], char.get("appearance", ""), proj["style"], pid=proj["id"])
            if design and (providers.MODE == "mock" or os.path.exists(design)):
                char["design"] = design
                char["design_elapsed"] = round(time.time() - t0)
                media_store.register(proj["id"], "designs", design, {"char": char["name"]})
                _save_one(proj)
                return
        except Exception as e:
            if attempt == 3:
                proj["log"].append(f"[{time.strftime('%H:%M:%S')}] 角色 {char['name']} 定妆失败: {str(e)[:120]}")
                _save_one(proj)
                return
            await asyncio.sleep(2)


async def _gen_frame_only(proj: dict, shot: dict):
    """只生成首帧（供并发调用）。"""
    shot["video_status"] = "generating_frame"
    _save_one(proj)
    t0 = time.time()
    for f_attempt in range(1, 4):
        try:
            frame = await providers.gen_first_frame(shot, proj["style"], pid=proj["id"])
            if frame and (providers.MODE == "mock" or os.path.exists(frame)):
                shot["frame"] = frame
                shot["frame_elapsed"] = round(time.time() - t0)
                shot["video_status"] = "pending"   # 首帧好了，等视频阶段
                media_store.register(proj["id"], "frames", frame, {"shot": shot["shot_id"]})
                _save_one(proj)
                return
        except Exception as e:
            if f_attempt == 3:
                shot["video_status"] = "frame_failed"
                proj["log"].append(f"[{time.strftime('%H:%M:%S')}] {shot['shot_id']} 首帧失败: {str(e)[:120]}")
                _save_one(proj)
                return
            await asyncio.sleep(2)


async def _gen_frame_from_ref(proj: dict, shot: dict, ref_frame):
    """以角色定妆照为参考做图生图（新场景新构图，人物形象锁定）。
    ref_frame 可传 list：该镜头所有出场角色的定妆照一起传。"""
    shot["video_status"] = "generating_frame"
    _save_one(proj)
    t0 = time.time()
    n_refs = len(ref_frame) if isinstance(ref_frame, (list, tuple)) else 1
    ref_desc = "参考图中的人物为主角" if n_refs == 1 else f"参考图中的{n_refs}个人物都按各自形象出现在画面里"
    instruction = (
        f"以{ref_desc}，生成新画面：{shot['description']}。"
        f"{proj['style']}风格，电影感构图，{shot['mood']}氛围。"
        f"人物长相、发型、服装与各自参考图完全一致，画面场景、姿势、景别按描述重新构图"
    )
    for attempt in range(1, 4):
        try:
            frame = await providers.edit_frame(shot, ref_frame, instruction=instruction, strength=0.85, pid=proj["id"])
            if frame and (providers.MODE == "mock" or os.path.exists(frame)):
                shot["frame"] = frame
                shot["frame_elapsed"] = round(time.time() - t0)
                shot["video_status"] = "pending"
                shot["char_unified"] = True   # 参考图直接生成，天然统一
                media_store.register(proj["id"], "frames", frame, {"shot": shot["shot_id"], "via": "edit"})
                _save_one(proj)
                return
        except Exception as e:
            if attempt == 3:
                shot["video_status"] = "frame_failed"
                proj["log"].append(f"[{time.strftime('%H:%M:%S')}] {shot['shot_id']} 参考图生成失败: {str(e)[:120]}")
                _save_one(proj)
                return
            await asyncio.sleep(2)


async def _unify_character(proj: dict, shot: dict, ref_frame: str):
    """用 Edit 模型把镜头人物统一成参考图形象。失败保留原首帧。"""
    try:
        edited = await providers.edit_frame(
            shot, shot["frame"],
            instruction="将画面中的人物形象替换为参考图中的人物，保持人物长相、发型、服装完全一致，保持原图的姿势、构图、光线不变",
            ref_image=ref_frame, pid=proj["id"],
        )
        if edited and os.path.exists(edited):
            shot["frame_orig"] = shot["frame"]   # 保留编辑前原图
            shot["frame"] = edited
            shot["char_unified"] = True
            media_store.register(proj["id"], "frames", edited, {"shot": shot["shot_id"], "via": "unify"})
            _save_one(proj)
    except Exception as e:
        proj["log"].append(f"[{time.strftime('%H:%M:%S')}] {shot['shot_id']} 角色统一失败（保留原首帧）: {str(e)[:100]}")
        _save_one(proj)


async def _produce_video(proj: dict, shot: dict):
    """视频阶段（首帧已确认）：I2V + 抽卡重试。"""
    shot["started_at"] = time.time()
    frame = shot.get("frame", "")
    # mock 伪路径没有真实文件，放行（与 providers 侧的 mock 逻辑对齐）
    if not frame or (providers.MODE != "mock" and not os.path.exists(frame)):
        shot["video_status"] = "failed"
        _save_one(proj)
        return

    for attempt in range(1, 4):  # 最多抽 3 次卡
        shot["attempts"] = attempt
        t1 = time.time()
        shot["progress"] = 0
        _save_one(proj)

        def _report(pct: float):
            shot["progress"] = round(pct, 1)
            shot["elapsed"] = round(time.time() - shot["started_at"])
            _save_one(proj)

        try:
            result = await providers.gen_video(shot, frame, attempt, on_progress=_report, pid=proj["id"])
        except Exception as e:
            result = {"video": "", "ok": False}
            proj["log"].append(f"[{time.strftime('%H:%M:%S')}] {shot['shot_id']} 第{attempt}次抽卡异常: {str(e)[:150]}")
        shot["video_elapsed"] = round(time.time() - t1)  # 本次视频生成耗时
        if result["ok"]:
            shot["video"] = result["video"]
            shot["video_status"] = "done"
            shot["progress"] = 100
            shot["elapsed"] = round(time.time() - shot["started_at"])
            media_store.register(proj["id"], "videos", result["video"],
                                 {"shot": shot["shot_id"], "attempt": attempt})
            break
        shot["video_status"] = f"retry({attempt})"
        _save_one(proj)
    else:
        shot["video_status"] = "failed"
        shot["elapsed"] = round(time.time() - shot["started_at"])
    _save_one(proj)


@app.on_event("startup")
async def _on_startup():
    """启动钩子：旧平铺媒体迁移到按项目目录 + 断点续跑恢复。"""
    async def _delayed_init():
        await asyncio.sleep(1)
        try:
            moved = media_store.migrate_legacy(_load_projects())
            if moved:
                print(f"[media] 旧媒体迁移完成：{moved} 个文件归入项目目录")
        except Exception as e:
            print(f"[media] 迁移失败（不影响运行，可用脚本重跑）: {e}")
        await asyncio.sleep(2)
        try:
            await _resume_unfinished()
        except Exception as e:
            print(f"[resume] 恢复未完成任务失败: {e}")
    asyncio.create_task(_delayed_init())


if __name__ == "__main__":
    import uvicorn
    print(f"Mode: {providers.MODE}  ->  http://127.0.0.1:8020")
    uvicorn.run(app, host="127.0.0.1", port=8020)
