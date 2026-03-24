import logging
import os
import re
import smtplib
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps

import requests as http_requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import users as user_store
import proxy_config
import ip_enforcer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(32)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
SERVER_HOST = os.environ.get("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("SERVER_PORT", "8444"))

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "") or SMTP_USER
smtp_enabled = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)

# Geo cache: ip -> {city, isp, country}
_geo_cache: dict[str, dict] = {}
_geo_lock = threading.Lock()

# Connections cache: username -> current_connections
_connections: dict[str, int] = {}
_traffic: dict[str, float] = {}
_connections_lock = threading.Lock()


def _fetch_geo(ip: str) -> dict:
    with _geo_lock:
        if ip in _geo_cache:
            return _geo_cache[ip]
    try:
        r = http_requests.get(f"http://ip-api.com/json/{ip}?fields=city,isp,country", timeout=5)
        data = r.json() if r.ok else {}
    except Exception:
        data = {}
    result = {
        "city": data.get("city", ""),
        "isp": data.get("isp", ""),
        "country": data.get("country", ""),
    }
    with _geo_lock:
        _geo_cache[ip] = result
    return result


def _parse_proxy_stats(known_ips: dict[str, str] | None = None) -> tuple[dict[str, int], dict[str, str], dict[str, float]]:
    """Parse stats blocks from proxy logs.
    Returns (connections_by_user, last_ip_by_user, traffic_mb_by_user).
    known_ips: existing {name: active_ip} mapping used to resolve multi-user blocks.
    """
    try:
        client = __import__("docker").from_env()
        container = client.containers.get("mtgate-proxy")
        logs = container.logs(tail=500).decode("utf-8", errors="ignore")
    except Exception:
        return {}, {}, {}

    known_ips = known_ips or {}
    blocks = re.split(r"Stats for [^\n]*\n", logs)
    connections: dict[str, int] = {}
    last_ip: dict[str, str] = {}
    traffic_mb: dict[str, float] = {}

    for block in reversed(blocks):
        block_users: dict[str, int] = {}
        block_traffic: dict[str, float] = {}
        ips: list[str] = []
        in_ips = False
        for line in block.splitlines():
            s = line.strip()
            m = re.match(r"^([\w][\w-]*): \d+ connects \((\d+) current\),\s*([\d.]+)\s*MB", s)
            if m:
                name, current, mb = m.group(1), int(m.group(2)), float(m.group(3))
                if name not in block_users:
                    block_users[name] = current
                    block_traffic[name] = mb
                in_ips = False
            elif s == "New IPs:":
                in_ips = True
            elif in_ips:
                if re.match(r"^\d+\.\d+\.\d+\.\d+$", s):
                    ips.append(s)
                elif s:
                    in_ips = False

        for name, current in block_users.items():
            if name not in connections:
                connections[name] = current
            traffic_mb[name] = traffic_mb.get(name, 0.0) + block_traffic.get(name, 0.0)

        if not ips:
            continue

        if len(block_users) == 1:
            name = list(block_users.keys())[0]
            if name not in last_ip:
                last_ip[name] = ips[-1]
        else:
            # Multi-user block: subtract IPs already known for active users,
            # assign remaining IPs to users whose IP is unknown.
            active_names = [n for n, c in block_users.items() if c > 0]
            claimed_ips = {known_ips[n] for n in active_names if known_ips.get(n)}
            unclaimed_ips = [ip for ip in ips if ip not in claimed_ips]
            unknown_users = [n for n in active_names if not known_ips.get(n) and n not in last_ip]
            if len(unclaimed_ips) == 1 and len(unknown_users) == 1:
                last_ip[unknown_users[0]] = unclaimed_ips[0]
            # Also confirm known IPs that appear in this block
            for name in active_names:
                if known_ips.get(name) and known_ips[name] in ips and name not in last_ip:
                    last_ip[name] = known_ips[name]

    return connections, last_ip, traffic_mb


def _connections_worker():
    while True:
        time.sleep(30)
        try:
            known = {u["name"]: u.get("active_ip") for u in user_store.load_users() if u.get("active_ip")}
            counts, ips, traffic = _parse_proxy_stats(known_ips=known)
            with _connections_lock:
                _connections.clear()
                _connections.update(counts)
                _traffic.clear()
                _traffic.update(traffic)
            for name, ip in ips.items():
                try:
                    user_store.set_active_ip(name, ip)
                except Exception:
                    pass
        except Exception as e:
            logger.error("Connections worker error: %s", e)

# Brute-force protection
_bf_lock = threading.Lock()
_bf_attempts: dict[str, list[float]] = defaultdict(list)
_bf_blocked: dict[str, float] = {}
MAX_ATTEMPTS = 5
BLOCK_DURATION = 900  # 15 minutes


def _get_ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr).split(",")[0].strip()


def _is_blocked(ip: str) -> bool:
    with _bf_lock:
        blocked_until = _bf_blocked.get(ip)
        if blocked_until and time.time() < blocked_until:
            return True
        if blocked_until:
            del _bf_blocked[ip]
        return False


def _record_failure(ip: str):
    now = time.time()
    with _bf_lock:
        attempts = [t for t in _bf_attempts[ip] if now - t < 600]
        attempts.append(now)
        _bf_attempts[ip] = attempts
        if len(attempts) >= MAX_ATTEMPTS:
            _bf_blocked[ip] = now + BLOCK_DURATION
            logger.warning("Brute-force: blocked %s for %ds", ip, BLOCK_DURATION)


def _record_success(ip: str):
    with _bf_lock:
        _bf_attempts.pop(ip, None)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/")
def index():
    return redirect(url_for("users_list"))


@app.route("/login", methods=["GET", "POST"])
def login():
    ip = _get_ip()
    error = None
    if request.method == "POST":
        if _is_blocked(ip):
            error = "Too many failed attempts. Try again later."
        elif request.form.get("password") == ADMIN_PASSWORD:
            _record_success(ip)
            session["logged_in"] = True
            return redirect(url_for("users_list"))
        else:
            _record_failure(ip)
            error = "Invalid password."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/users", methods=["GET"])
@login_required
def users_list():
    all_users = user_store.load_users()
    with _connections_lock:
        conns = dict(_connections)
        traffic = dict(_traffic)
    for u in all_users:
        u["connections"] = conns.get(u["name"], 0)
        u["traffic_mb"] = traffic.get(u["name"], 0.0)
        ip = u.get("active_ip")
        u["geo"] = _fetch_geo(ip) if ip else {}
    return render_template(
        "users.html",
        users=all_users,
        server_host=SERVER_HOST,
        server_port=SERVER_PORT,
        smtp_enabled=smtp_enabled,
    )


@app.route("/users", methods=["POST"])
@login_required
def create_user():
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    try:
        ttl_days = int(request.form.get("ttl_days", 0) or 0)
        ttl_hours = int(request.form.get("ttl_hours", 0) or 0)
    except ValueError:
        ttl_days = ttl_hours = 0
    try:
        user = user_store.add_user(name, ttl_days=ttl_days, ttl_hours=ttl_hours)
        proxy_config.write_and_reload(user_store.load_users())
        return redirect(url_for("users_list"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 409


@app.route("/users/<name>/delete", methods=["POST"])
@login_required
def delete_user(name: str):
    user_store.delete_user(name)
    proxy_config.write_and_reload(user_store.load_users())
    return redirect(url_for("users_list"))


@app.route("/users/<name>/reset-binding", methods=["POST"])
@login_required
def reset_binding(name: str):
    try:
        user_store.reset_bound_ip(name)
        logger.info("Device binding reset for user '%s'", name)
    except ValueError:
        pass
    return redirect(url_for("users_list"))


@app.route("/users/<name>/toggle", methods=["POST"])
@login_required
def toggle_user(name: str):
    try:
        user_store.toggle_user(name)
        proxy_config.write_and_reload(user_store.load_users())
    except ValueError:
        pass
    return redirect(url_for("users_list"))


@app.route("/users/<name>/comment", methods=["POST"])
@login_required
def set_comment(name: str):
    data = request.get_json(silent=True) or {}
    comment = (data.get("comment") or "").strip()
    try:
        user_store.set_comment(name, comment)
        return jsonify({"ok": True})
    except ValueError:
        return jsonify({"error": "Not found"}), 404


@app.route("/users/<name>/link")
@login_required
def get_link(name: str):
    all_users = user_store.load_users()
    user = next((u for u in all_users if u["name"] == name), None)
    if not user:
        return jsonify({"error": "Not found"}), 404
    link = user_store.generate_tg_link(user["secret"], SERVER_HOST, SERVER_PORT)
    return jsonify({"link": link})


@app.route("/users/<name>/send-link", methods=["POST"])
@login_required
def send_link(name: str):
    if not smtp_enabled:
        return jsonify({"error": "SMTP не настроен"}), 503
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"error": "Email обязателен"}), 400
    all_users = user_store.load_users()
    user = next((u for u in all_users if u["name"] == name), None)
    if not user:
        return jsonify({"error": "Not found"}), 404
    link = user_store.generate_tg_link(user["secret"], SERVER_HOST, SERVER_PORT)
    secret_full = "ee" + user["secret"] + "7777772e676f6f676c652e636f6d"
    html_body = render_template(
        "email_link.html",
        name=name,
        server_host=SERVER_HOST,
        server_port=SERVER_PORT,
        secret=secret_full,
        link=link,
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Подключение к MTProto прокси — {name}"
    msg["From"] = SMTP_FROM
    msg["To"] = email
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as s:
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.sendmail(SMTP_FROM, [email], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                s.ehlo()
                s.starttls()
                s.login(SMTP_USER, SMTP_PASSWORD)
                s.sendmail(SMTP_FROM, [email], msg.as_string())
        logger.info("Sent proxy link for '%s' to %s", name, email)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("SMTP error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/users")
@login_required
def api_users():
    all_users = user_store.load_users()
    with _connections_lock:
        conns = dict(_connections)
        traffic = dict(_traffic)
    for u in all_users:
        u["connections"] = conns.get(u["name"], 0)
        u["traffic_mb"] = traffic.get(u["name"], 0.0)
        ip = u.get("active_ip")
        u["geo"] = _fetch_geo(ip) if ip else {}
    return jsonify(all_users)


@app.route("/api/ping/<name>")
@login_required
def ping_user(name: str):
    all_users = user_store.load_users()
    user = next((u for u in all_users if u["name"] == name), None)
    if not user:
        return jsonify({"error": "Not found"}), 404
    ip = user.get("active_ip")
    if not ip:
        return jsonify({"error": "No active IP"}), 404
    try:
        import subprocess
        result = subprocess.run(
            ["ping", "-c", "3", "-W", "2", ip],
            capture_output=True, text=True, timeout=10,
        )
        # Extract avg from "rtt min/avg/max/mdev = 1.2/3.4/5.6/7.8 ms"
        m = re.search(r"= [\d.]+/([\d.]+)/[\d.]+/[\d.]+ ms", result.stdout)
        if m:
            return jsonify({"ip": ip, "avg_ms": float(m.group(1))})
        if result.returncode != 0:
            return jsonify({"ip": ip, "error": "unreachable"})
        return jsonify({"ip": ip, "error": "parse error"})
    except subprocess.TimeoutExpired:
        return jsonify({"ip": ip, "error": "timeout"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return "ok"


@app.route("/api/services")
@login_required
def api_services():
    import docker as docker_lib
    services = [
        {"name": "mtgate-proxy", "label": "MTProto Proxy"},
        {"name": "mtgate-admin", "label": "Admin Panel"},
        {"name": "mtg",          "label": "MTG"},
        {"name": "mtg-stats",    "label": "MTG Stats"},
    ]
    try:
        client = docker_lib.from_env()
        containers = {c.name: c for c in client.containers.list(all=True)}
    except Exception as e:
        return jsonify({"error": str(e), "services": []})

    result = []
    for svc in services:
        c = containers.get(svc["name"])
        if c is None:
            status = "missing"
            health_status = None
        else:
            status = c.status  # running / exited / paused ...
            try:
                health_status = c.attrs["State"]["Health"]["Status"]  # healthy / unhealthy / starting
            except (KeyError, TypeError):
                health_status = None
        result.append({
            "name": svc["name"],
            "label": svc["label"],
            "status": status,
            "health": health_status,
        })
    return jsonify(result)


def _expiry_worker():
    while True:
        time.sleep(60)
        try:
            deleted = user_store.purge_expired_users()
            if deleted:
                logger.info("Expired users removed: %s", deleted)
                proxy_config.write_and_reload(user_store.load_users())
        except Exception as e:
            logger.error("Expiry worker error: %s", e)


def _init():
    os.makedirs("/cache", exist_ok=True)
    if not os.path.exists("/cache/users.json"):
        user_store.save_users([])
        logger.info("Initialized empty users.json")
    proxy_config.write_and_reload(user_store.load_users())
    ip_enforcer.start()
    threading.Thread(target=_expiry_worker, daemon=True, name="expiry-worker").start()
    threading.Thread(target=_connections_worker, daemon=True, name="connections-worker").start()


if __name__ == "__main__":
    _init()
    app.run(host="0.0.0.0", port=8889, debug=False)
