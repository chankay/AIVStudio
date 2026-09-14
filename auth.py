"""极简认证：用户名/密码登录 + Cookie 会话。

- 用户存 data/auth.json（PBKDF2-SHA256 加盐哈希，gitignore）
- 首次无用户时允许自助创建管理员（/api/auth/setup 仅此窗口可用）
- 会话 token 仅存内存，服务重启需重新登录（可接受：任务由队列自己续跑）
"""
import hashlib
import json
import os
import secrets

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(_BASE_DIR, "data", "auth.json")

SESSION_COOKIE = "aiv_session"
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 天

_sessions: dict[str, str] = {}  # token -> username


def _hash_pw(pw: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", pw.encode("utf-8"), bytes.fromhex(salt_hex), 200_000
    ).hex()


def _load_users() -> dict:
    try:
        with open(AUTH_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_users(users: dict):
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    fd = os.open(AUTH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def has_users() -> bool:
    return bool(_load_users())


def create_user(username: str, password: str) -> bool:
    """创建账号；用户名已存在返回 False。密码须 >= 6 位。"""
    if not username.strip() or len(password) < 6:
        return False
    users = _load_users()
    if username.strip() in users:
        return False
    salt = secrets.token_hex(16)
    users[username.strip()] = {"salt": salt, "hash": _hash_pw(password, salt)}
    _save_users(users)
    return True


def verify(username: str, password: str) -> bool:
    u = _load_users().get(username.strip())
    if not u:
        # 比对一次假哈希，抹平用户不存在与密码错误的耗时差
        _hash_pw(password, "00" * 16)
        return False
    return secrets.compare_digest(u["hash"], _hash_pw(password, u["salt"]))


def new_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = username
    return token


def user_of(token: str | None) -> str | None:
    if not token:
        return None
    return _sessions.get(token)


def drop_session(token: str | None):
    if token:
        _sessions.pop(token, None)
