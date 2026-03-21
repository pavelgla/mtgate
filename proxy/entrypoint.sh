#!/bin/sh
set -e

CONFIG_SRC="/cache/proxy_config.py"
CONFIG_DST="/app/mtprotoproxy/config.py"

echo "[entrypoint] Waiting for proxy config..."
for i in $(seq 1 30); do
    if [ -f "$CONFIG_SRC" ]; then
        break
    fi
    sleep 1
done

if [ ! -f "$CONFIG_SRC" ]; then
    echo "[entrypoint] Config not found after 30s, creating minimal default"
    printf 'PORT = 3128\nUSERS = {}\nAD_TAG = ""\n' > "$CONFIG_SRC"
fi

copy_config() {
    cp "$CONFIG_SRC" "$CONFIG_DST"
    echo "[entrypoint] Config loaded"
}

copy_config

PROXY_PID=""

start_proxy() {
    python /app/mtprotoproxy/mtprotoproxy.py "$CONFIG_DST" &
    PROXY_PID=$!
    echo "[entrypoint] mtprotoproxy started (pid $PROXY_PID)"
}

reload_proxy() {
    echo "[entrypoint] SIGHUP received, reloading config..."
    copy_config
    if [ -n "$PROXY_PID" ]; then
        kill "$PROXY_PID" 2>/dev/null || true
        wait "$PROXY_PID" 2>/dev/null || true
    fi
    start_proxy
}

trap reload_proxy HUP
trap 'kill $PROXY_PID 2>/dev/null; exit 0' TERM INT

start_proxy

while true; do
    wait "$PROXY_PID" 2>/dev/null || true
    if [ -n "$PROXY_PID" ]; then
        echo "[entrypoint] Proxy exited unexpectedly, restarting..."
        sleep 2
        start_proxy
    fi
done
