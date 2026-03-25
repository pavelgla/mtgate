import json
import os
import threading
import time
from datetime import datetime, timezone, timedelta

USERS_FILE = "/cache/users.json"
_lock = threading.Lock()


def load_users() -> list[dict]:
    with _lock:
        if not os.path.exists(USERS_FILE):
            return []
        with open(USERS_FILE, "r") as f:
            data = json.load(f)
        return data.get("users", [])


def save_users(users: list[dict]):
    """Atomic write via tmp file."""
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"users": users}, f, indent=2)
    os.replace(tmp, USERS_FILE)


def add_user(name: str, ttl_days: int = 0, ttl_hours: int = 0) -> dict:
    with _lock:
        users = _load_unlocked()
        if any(u["name"] == name for u in users):
            raise ValueError(f"User '{name}' already exists")
        now = datetime.now(timezone.utc)
        expires_at = None
        total_hours = ttl_days * 24 + ttl_hours
        if total_hours > 0:
            expires_at = (now + timedelta(hours=total_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        user = {
            "name": name,
            "secret": os.urandom(16).hex(),
            "enabled": True,
            "active_ip": None,
            "bound_ip": None,
            "last_seen": None,
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": expires_at,
        }
        users.append(user)
        save_users(users)
        return user


def delete_user(name: str) -> bool:
    with _lock:
        users = _load_unlocked()
        new_users = [u for u in users if u["name"] != name]
        if len(new_users) == len(users):
            return False
        save_users(new_users)
        return True


def toggle_user(name: str) -> bool:
    """Toggle enabled state. Returns new enabled value."""
    with _lock:
        users = _load_unlocked()
        for u in users:
            if u["name"] == name:
                u["enabled"] = not u["enabled"]
                save_users(users)
                return u["enabled"]
        raise ValueError(f"User '{name}' not found")


def set_active_ip(name: str, ip: str | None):
    with _lock:
        users = _load_unlocked()
        for u in users:
            if u["name"] == name:
                u["active_ip"] = ip
                save_users(users)
                return
        raise ValueError(f"User '{name}' not found")


def update_device_binding(name: str, ip: str, cooldown_hours: int = 24) -> str:
    """Update device binding for user. Returns action: 'bound', 'ok', 'migrated', 'blocked'."""
    now_ts = datetime.now(timezone.utc).isoformat()
    with _lock:
        users = _load_unlocked()
        for u in users:
            if u["name"] != name:
                continue
            bound_ip = u.get("bound_ip")
            last_seen = u.get("last_seen")

            if bound_ip is None:
                # First connection — bind
                u["bound_ip"] = ip
                u["active_ip"] = ip
                u["last_seen"] = now_ts
                save_users(users)
                return "bound"

            if ip == bound_ip:
                # Same device — update last_seen
                u["active_ip"] = ip
                u["last_seen"] = now_ts
                save_users(users)
                return "ok"

            # Different IP — check cooldown
            if last_seen:
                last_dt = datetime.fromisoformat(last_seen)
                elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            else:
                elapsed_hours = cooldown_hours + 1  # no last_seen → allow migration

            if elapsed_hours >= cooldown_hours:
                # Old device hasn't been seen for a long time — allow migration
                old_ip = bound_ip
                u["bound_ip"] = ip
                u["active_ip"] = ip
                u["last_seen"] = now_ts
                save_users(users)
                return f"migrated:{old_ip}"
            else:
                # Old device still active — block new IP
                return "blocked"

        raise ValueError(f"User '{name}' not found")


def set_comment(name: str, comment: str):
    with _lock:
        users = _load_unlocked()
        for u in users:
            if u["name"] == name:
                u["comment"] = comment.strip()
                save_users(users)
                return
        raise ValueError(f"User '{name}' not found")


def reset_bound_ip(name: str):
    """Reset device binding so next connection can claim the token."""
    with _lock:
        users = _load_unlocked()
        for u in users:
            if u["name"] == name:
                u["bound_ip"] = None
                u["last_seen"] = None
                u["active_ip"] = None
                save_users(users)
                return
        raise ValueError(f"User '{name}' not found")


def get_user_by_secret(secret: str) -> dict | None:
    users = load_users()
    for u in users:
        if u["secret"] == secret:
            expires_at = u.get("expires_at")
            if expires_at:
                exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) >= exp_dt:
                    return None  # token expired
            return u
    return None


def purge_expired_users() -> list[str]:
    """Delete all users whose expires_at has passed. Returns list of deleted names."""
    now = datetime.now(timezone.utc)
    deleted = []
    with _lock:
        users = _load_unlocked()
        remaining = []
        for u in users:
            expires_at = u.get("expires_at")
            if expires_at:
                exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if now >= exp_dt:
                    deleted.append(u["name"])
                    continue
            remaining.append(u)
        if deleted:
            save_users(remaining)
    return deleted


def extend_user(name: str, days: int = 0, hours: int = 0) -> str:
    """Extend user expiry by days/hours from now (or from current expiry if still valid).
    Returns new expires_at string."""
    total_hours = days * 24 + hours
    if total_hours <= 0:
        raise ValueError("days or hours must be positive")
    with _lock:
        users = _load_unlocked()
        for u in users:
            if u["name"] == name:
                now = datetime.now(timezone.utc)
                expires_at = u.get("expires_at")
                if expires_at:
                    exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    base = exp_dt if exp_dt > now else now
                else:
                    base = now
                new_expiry = (base + timedelta(hours=total_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
                u["expires_at"] = new_expiry
                save_users(users)
                return new_expiry
        raise ValueError(f"User '{name}' not found")


def generate_tg_link(secret: str, host: str, port: int, tls_domain: str = "www.google.com") -> str:
    """Generate tg://proxy link. Prefix 'ee' = fake-TLS (MTProto v2).
    Full secret format: ee + secret_hex + hex(tls_domain)
    """
    domain_hex = tls_domain.encode().hex()
    return f"tg://proxy?server={host}&port={port}&secret=ee{secret}{domain_hex}"


def _load_unlocked() -> list[dict]:
    """Load without acquiring lock (call only when lock is held)."""
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, "r") as f:
        data = json.load(f)
    return data.get("users", [])
