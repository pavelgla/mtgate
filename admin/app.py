import logging
import os
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
from functools import wraps

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
    return render_template(
        "users.html",
        users=all_users,
        server_host=SERVER_HOST,
        server_port=SERVER_PORT,
    )


@app.route("/users", methods=["POST"])
@login_required
def create_user():
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    try:
        user = user_store.add_user(name)
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


@app.route("/users/<name>/link")
@login_required
def get_link(name: str):
    all_users = user_store.load_users()
    user = next((u for u in all_users if u["name"] == name), None)
    if not user:
        return jsonify({"error": "Not found"}), 404
    link = user_store.generate_tg_link(user["secret"], SERVER_HOST, SERVER_PORT)
    return jsonify({"link": link})


@app.route("/api/users")
@login_required
def api_users():
    all_users = user_store.load_users()
    return jsonify(all_users)


@app.route("/health")
def health():
    return "ok"


def _init():
    os.makedirs("/cache", exist_ok=True)
    if not os.path.exists("/cache/users.json"):
        user_store.save_users([])
        logger.info("Initialized empty users.json")
    proxy_config.write_and_reload(user_store.load_users())
    ip_enforcer.start()


if __name__ == "__main__":
    _init()
    app.run(host="0.0.0.0", port=8889, debug=False)
