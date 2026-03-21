import logging
import docker

logger = logging.getLogger(__name__)

CONFIG_PATH = "/cache/proxy_config.py"
CONTAINER_NAME = "mtgate-proxy"


def generate_config(users: list[dict]) -> str:
    enabled = {u["name"]: u["secret"] for u in users if u.get("enabled")}
    users_repr = "\n".join(f'    "{name}": "{secret}",' for name, secret in enabled.items())
    return f"""PORT = 3128
USERS = {{
{users_repr}
}}
AD_TAG = ""
"""


def write_and_reload(users: list[dict]):
    config = generate_config(users)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(config)
    import os
    os.replace(tmp, CONFIG_PATH)
    logger.info("Proxy config written (%d enabled users)", sum(1 for u in users if u.get("enabled")))

    try:
        client = docker.from_env()
        container = client.containers.get(CONTAINER_NAME)
        container.kill(signal="SIGHUP")
        logger.info("SIGHUP sent to %s", CONTAINER_NAME)
    except docker.errors.NotFound:
        logger.warning("Container %s not found, config written but not reloaded", CONTAINER_NAME)
    except Exception as e:
        logger.error("Failed to send SIGHUP: %s", e)
