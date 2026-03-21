import logging
import re
import subprocess
import threading

import docker

from users import get_user_by_secret, update_device_binding

logger = logging.getLogger(__name__)

CONTAINER_NAME = "mtgate-proxy"
BAN_DURATION = 30  # seconds
DEVICE_COOLDOWN_HOURS = 24  # hours of inactivity before device migration is allowed

# mtprotoproxy log pattern examples:
# "new client connected from 1.2.3.4, secret: abcd..."
# Adjust regex based on actual mtprotoproxy log format
_LOG_PATTERN = re.compile(
    r"(?:new client|connected from)\s+(\d+\.\d+\.\d+\.\d+).*?secret[:\s]+([0-9a-fA-F]{32})",
    re.IGNORECASE,
)
_ALT_PATTERN = re.compile(
    r"(\d+\.\d+\.\d+\.\d+).*?([0-9a-fA-F]{32})",
    re.IGNORECASE,
)


def _ban_ip(ip: str):
    try:
        subprocess.run(
            ["iptables", "-I", "INPUT", "-s", ip, "-j", "DROP"],
            check=True, capture_output=True,
        )
        logger.info("Banned IP %s for %ds", ip, BAN_DURATION)
    except Exception as e:
        logger.error("iptables ban failed for %s: %s", ip, e)


def _unban_ip(ip: str):
    try:
        subprocess.run(
            ["iptables", "-D", "INPUT", "-s", ip, "-j", "DROP"],
            check=True, capture_output=True,
        )
        logger.info("Unbanned IP %s", ip)
    except Exception as e:
        logger.error("iptables unban failed for %s: %s", ip, e)


def _handle_connection(ip: str, secret: str):
    try:
        user = get_user_by_secret(secret)
        if not user:
            # Could be expired token — ban briefly to force disconnect
            _ban_ip(ip)
            threading.Timer(BAN_DURATION, _unban_ip, args=[ip]).start()
            return

        result = update_device_binding(user["name"], ip, cooldown_hours=DEVICE_COOLDOWN_HOURS)

        if result == "bound":
            logger.info("Device bound for %s: %s", user["name"], ip)

        elif result == "ok":
            pass  # same device, no action needed

        elif result.startswith("migrated:"):
            old_ip = result.split(":", 1)[1]
            logger.info("Device migrated for %s: %s → %s (old device inactive >%dh)",
                        user["name"], old_ip, ip, DEVICE_COOLDOWN_HOURS)
            # Temporarily ban old IP in case it tries to reconnect
            _ban_ip(old_ip)
            threading.Timer(BAN_DURATION, _unban_ip, args=[old_ip]).start()

        elif result == "blocked":
            logger.warning("Blocked unauthorized device for %s: %s (bound to different IP)",
                           user["name"], ip)
            _ban_ip(ip)
            threading.Timer(BAN_DURATION, _unban_ip, args=[ip]).start()

    except Exception as e:
        logger.error("Error handling connection event: %s", e)


def _parse_log_line(line: str):
    """Try to extract (ip, secret) from a log line."""
    m = _LOG_PATTERN.search(line)
    if m:
        return m.group(1), m.group(2)
    return None


def _watch_logs():
    logger.info("IP enforcer: starting log watcher for container %s", CONTAINER_NAME)
    while True:
        try:
            client = docker.from_env()
            container = client.containers.get(CONTAINER_NAME)
            for log_bytes in container.logs(stream=True, follow=True, tail=0):
                try:
                    line = log_bytes.decode("utf-8", errors="replace").strip()
                    result = _parse_log_line(line)
                    if result:
                        ip, secret = result
                        _handle_connection(ip, secret)
                except Exception as e:
                    logger.debug("Log parse error: %s", e)
        except docker.errors.NotFound:
            logger.warning("Container %s not found, retrying in 10s", CONTAINER_NAME)
        except Exception as e:
            logger.error("Log watcher error: %s, retrying in 10s", e)
        import time
        time.sleep(10)


def start():
    t = threading.Thread(target=_watch_logs, daemon=True, name="ip-enforcer")
    t.start()
    logger.info("IP enforcer started")
