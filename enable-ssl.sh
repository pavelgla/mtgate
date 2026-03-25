#!/bin/bash
# Run AFTER certbot successfully issues the certificate for tefobi.ru
# Usage: sudo bash /opt/mtgate/enable-ssl.sh
set -e

TEMPLATE=/opt/vless-vpn/nginx/nginx.conf.template

echo "[1/3] Writing final nginx template with HTTPS for tefobi.ru..."

# Read current template up to the tefobi HTTPS comment block, then append live config
# We replace the entire commented section with the real server blocks

sudo tee "$TEMPLATE" > /dev/null << 'TEMPLATE_EOF'
user nginx;
worker_processes auto;
error_log /dev/stderr warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

stream {
    log_format stream_basic '$proxy_protocol_addr [$time_local] $protocol '
                            '$status $bytes_sent $bytes_received '
                            '$session_time "$ssl_preread_server_name"';
    access_log /dev/stdout stream_basic;

    map $ssl_preread_server_name $backend {
        ${DOMAIN}       web_https;
        tefobi.ru       tefobi_https;
        www.tefobi.ru   tefobi_https;
        default         xray_vless;
    }

    upstream web_https {
        server 127.0.0.1:8443;
    }

    upstream tefobi_https {
        server 127.0.0.1:8444;
    }

    upstream xray_vless {
        server xray:443;
    }

    server {
        listen 443;
        ssl_preread on;
        proxy_pass $backend;
        proxy_connect_timeout 10s;
        proxy_timeout 600s;
    }
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    log_format main '$proxy_protocol_addr - [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent"';
    access_log /dev/stdout main;

    sendfile        on;
    tcp_nopush      on;
    tcp_nodelay     on;
    keepalive_timeout 65;
    server_tokens off;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    # HTTP → HTTPS redirect (main domain)
    server {
        listen 80;
        server_name ${DOMAIN};

        location /nginx-health {
            return 200 "ok\n";
            add_header Content-Type text/plain;
        }

        location / {
            return 301 https://$host$request_uri;
        }
    }

    # tefobi.ru — HTTP: acme-challenge + redirect to HTTPS
    server {
        listen 80;
        server_name tefobi.ru www.tefobi.ru;

        location /.well-known/acme-challenge/ {
            root /var/www/tefobi;
        }

        location / {
            return 301 https://tefobi.ru$request_uri;
        }
    }

    # HTTPS web panel (main domain)
    server {
        listen 8443 ssl;
        server_name ${DOMAIN};

        ssl_certificate     /etc/nginx/certs/fullchain.pem;
        ssl_certificate_key /etc/nginx/certs/privkey.pem;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;
        ssl_prefer_server_ciphers on;
        ssl_session_cache   shared:SSL:10m;
        ssl_session_timeout 10m;

        add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
        add_header X-Frame-Options DENY always;
        add_header X-Content-Type-Options nosniff always;

        client_max_body_size 10m;

        location /api/ {
            proxy_pass http://api:3000/;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 60s;
            proxy_connect_timeout 10s;
        }

        location / {
            proxy_pass http://frontend:80;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_read_timeout 30s;
            proxy_connect_timeout 10s;
        }
    }

    # tefobi.ru — HTTPS landing page
    server {
        listen 8444 ssl;
        server_name tefobi.ru;

        ssl_certificate     /etc/letsencrypt/live/tefobi.ru/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/tefobi.ru/privkey.pem;
        ssl_protocols       TLSv1.2 TLSv1.3;
        ssl_ciphers         HIGH:!aNULL:!MD5;
        ssl_prefer_server_ciphers on;
        ssl_session_cache   shared:SSL:10m;
        ssl_session_timeout 10m;

        add_header Strict-Transport-Security "max-age=31536000" always;

        root /var/www/tefobi;
        index index.html;

        location / {
            try_files $uri $uri/ /index.html;
        }
    }

    # www.tefobi.ru → tefobi.ru redirect
    server {
        listen 8444 ssl;
        server_name www.tefobi.ru;

        ssl_certificate     /etc/letsencrypt/live/tefobi.ru/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/tefobi.ru/privkey.pem;

        return 301 https://tefobi.ru$request_uri;
    }
}
TEMPLATE_EOF

echo "[2/3] Rebuilding nginx image..."
cd /opt/vless-vpn
sudo docker compose build nginx

echo "[3/3] Restarting nginx..."
sudo docker compose up -d nginx

echo ""
echo "Done! Verify:"
echo "  curl -I https://tefobi.ru"
echo "  curl -I https://www.tefobi.ru  (should 301 to tefobi.ru)"
