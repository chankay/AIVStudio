"""媒体独立存储层：按项目分目录 + 存储根可配置 + 资产登记。

目录布局（默认根 data/media，可用环境变量 MEDIA_ROOT 或 config.json 的 storage.root 覆盖）：
    {root}/{pid}/frames/    首帧图（含 Edit 产出）
    {root}/{pid}/designs/   角色定妆照
    {root}/{pid}/videos/    镜头成片（I2V 产出 + 音画合成）
    {root}/{pid}/audio/     TTS 配音
    {root}/{pid}/finals/    拼接正片
    {root}/{pid}/tmp/       拼接转码临时文件（可随时清空）

与旧版平铺 data/frames、data/videos 的区别：
- 每个项目一个独立目录，删项目 = 删目录，不用再逐字段找文件
- 存储根可配置（迁到大盘/对象存储网关只改一个配置）
- DB 里 assets 表登记每个文件，可查项目资产清单、算体积
"""
import os
import shutil
import uuid

import db as store

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ROOT = os.path.join(_BASE_DIR, "data", "media")

_KINDS = ("frames", "designs", "videos", "audio", "finals", "tmp")


def _root() -> str:
    """存储根：环境变量 MEDIA_ROOT > config.json storage.root > 默认 data/media。"""
    env = os.environ.get("MEDIA_ROOT")
    if env:
        return env
    cfg_file = os.path.join(_BASE_DIR, "data", "config.json")
    if os.path.exists(cfg_file):
        try:
            import json
            with open(cfg_file, encoding="utf-8") as f:
                cfg = json.load(f) or {}
            r = (cfg.get("storage") or {}).get("root")
            if r:
                return r
        except Exception:
            pass
    return _DEFAULT_ROOT


def media_root() -> str:
    r = _root()
    os.makedirs(r, exist_ok=True)
    return r


def project_media_dir(pid: str, kind: str = "") -> str:
    """项目媒体目录（或其下某类子目录），自动创建。"""
    if kind and kind not in _KINDS:
        raise ValueError(f"未知媒体类型: {kind}（可选 {_KINDS}）")
    d = os.path.join(media_root(), pid, kind) if kind else os.path.join(media_root(), pid)
    os.makedirs(d, exist_ok=True)
    return d


def new_path(pid: str, kind: str, filename: str) -> str:
    """为即将生成的文件分配目标路径（调用方负责写入）。"""
    return os.path.join(project_media_dir(pid, kind), filename)


def gen_name(prefix: str, ext: str) -> str:
    """生成不重名文件名：{prefix}_{rand}.{ext}。"""
    return f"{prefix}_{uuid.uuid4().hex[:6]}.{ext.lstrip('.')}"


def url_for(path: str) -> str:
    """本地路径 -> 前端可访问的 /media/... URL。非存储根下或 mock 伪路径返回空串。"""
    root = os.path.realpath(media_root())
    real = os.path.realpath(path) if path else ""
    if not real.startswith(root + os.sep):
        return ""
    return "/media/" + os.path.relpath(real, root).replace(os.sep, "/")


def register(pid: str, kind: str, path: str, meta: dict | None = None):
    """资产登记入库（projects 里的字段仍存路径，assets 表只做清单/统计）。"""
    try:
        size = os.path.getsize(path) if os.path.exists(path) else 0
    except OSError:
        size = 0
    store.add_asset({
        "id": uuid.uuid4().hex[:8],
        "pid": pid,
        "kind": kind,
        "path": path,
        "size": size,
        "meta": meta or {},
    })


def delete_project_media(pid: str) -> int:
    """删除项目整个媒体目录，返回释放的字节数（估算，删前统计）。"""
    d = os.path.join(media_root(), pid)
    total = 0
    for dirpath, _, filenames in os.walk(d):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    if os.path.exists(d):
        shutil.rmtree(d, ignore_errors=True)
    store.delete_assets_for_pid(pid)
    return total


def project_stats(pid: str) -> dict:
    """项目资产统计：各类文件数 + 体积。"""
    out = {}
    for kind in _KINDS:
        d = os.path.join(media_root(), pid, kind)
        if not os.path.isdir(d):
            continue
        files = []
        for dirpath, _, filenames in os.walk(d):
            files.extend(os.path.join(dirpath, fn) for fn in filenames)
        out[kind] = {"count": len(files), "bytes": sum(
            os.path.getsize(f) for f in files if os.path.exists(f))}
    return out


# ---------------- 旧数据迁移（平铺 data/frames 等 -> 按项目目录） ----------------

def migrate_legacy(projs: dict) -> int:
    """把旧平铺目录里的文件按归属项目搬进新结构，并更新 DB 中的路径。返回搬迁文件数。

    归属判断：文件名以 shot_id / design_角色名 开头的从项目字段里反查。
    """
    moved = 0
    root = media_root()
    for pid, proj in projs.items():
        changed = False
        # 定妆照
        for c in proj.get("characters") or []:
            d = c.get("design") or ""
            if d and os.path.exists(d) and not d.startswith(root):
                dst = new_path(pid, "designs", os.path.basename(d))
                if dst != d:
                    shutil.move(d, dst)
                    moved += 1
                c["design"] = dst
                changed = True
        # 镜头媒体
        for s in proj.get("shots") or []:
            for field, kind in (("frame", "frames"), ("frame_orig", "frames"),
                                ("video", "videos"), ("video_final", "videos"),
                                ("audio", "audio")):
                p = s.get(field) or ""
                if p and os.path.exists(p) and not p.startswith(root):
                    dst = new_path(pid, kind, os.path.basename(p))
                    if dst != p:
                        shutil.move(p, dst)
                        moved += 1
                    s[field] = dst
                    changed = True
            if s.get("video") and str(s["video"]).startswith("http"):
                changed = True  # 远端 URL 保留
        if proj.get("final_video") and os.path.exists(proj["final_video"]) \
                and not proj["final_video"].startswith(root):
            dst = new_path(pid, "finals", os.path.basename(proj["final_video"]))
            shutil.move(proj["final_video"], dst)
            moved += 1
            proj["final_video"] = dst
            changed = True
        if changed:
            store.save_project(proj)
    return moved
