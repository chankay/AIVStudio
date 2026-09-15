"""极简认证：用户名/密码登录 + Cookie 会话。

- 用户与会话都存 SQLite（data/drama.db 的 users / sessions 表），服务重启会话不丢
- 首次无用户时允许自助创建管理员（/api/auth/setup 仅此窗口可用）
- 旧 data/auth.json 在首次调用时自动迁移入库（迁移成功后改名为 auth.json.migrated）
"""
import hashlib
import json
import os
import secrets

import db

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(_BASE_DIR, "data", "auth.json")

SESSION_COOKIE = "aiv_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 天

_migrated = False


def _hash_pw(pw: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", pw.encode("utf-8"), bytes.fromhex(salt_hex), 200_000
    ).hex()


def _migrate_auth_json():
    """旧 auth.json → users 表，一次性迁移。成功后原文件改名留档。"""
    global _migrated
    if _migrated or not os.path.exists(AUTH_FILE):
        return
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            users = json.load(f)
        n = 0
        for username, u in users.items():
            if db.create_user(username, u.get("hash", ""), u.get("salt", "")):
                n += 1
        if n:
            os.rename(AUTH_FILE, AUTH_FILE + ".migrated")
            print(f"[auth] 已迁移 {n} 个用户到 SQLite（auth.json → auth.json.migrated）")
        _migrated = True
    except Exception as e:
        print(f"[auth] auth.json 迁移失败（不影响使用）: {e}")


def has_users() -> bool:
    _migrate_auth_json()
    return db.users_exist()


def create_user(username: str, password: str) -> bool:
    """创建账号；用户名已存在返回 False。密码须 >= 6 位。"""
    if not username.strip() or len(password) < 6:
        return False
    salt = secrets.token_hex(16)
    return db.create_user(username.strip(), _hash_pw(password, salt), salt)


def verify(username: str, password: str) -> bool:
    _migrate_auth_json()
    u = db.get_user(username.strip())
    if not u:
        # 比对一次假哈希，抹平用户不存在与密码错误的耗时差
        _hash_pw(password, "00" * 16)
        return False
    return secrets.compare_digest(u["hash"], _hash_pw(password, u["salt"]))


def change_password(username: str, old_password: str, new_password: str) -> str | None:
    """修改密码。成功返回 None，失败返回错误信息。"""
    username = username.strip()
    if not verify(username, old_password):
        return "旧密码错误"
    if len(new_password) < 6:
        return "新密码至少 6 位"
    if old_password == new_password:
        return "新密码不能与旧密码相同"
    salt = secrets.token_hex(16)
    db.update_password(username, _hash_pw(new_password, salt), salt)
    return None


def new_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(token, username.strip(), SESSION_MAX_AGE)
    return token


def user_of(token: str | None) -> str | None:
    return db.session_user(token)


def drop_session(token: str | None):
    db.drop_session(token)
