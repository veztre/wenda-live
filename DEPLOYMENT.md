# Wenda-Live — Deployment Guide

How to deploy Wenda-Live to a Linux VPS (the live instance runs on a DigitalOcean
droplet at **https://wenda-live.veztre.site**).

Wenda-Live is the live multiplayer quiz sibling of **Wenda-Quiz**. It needs
WebSockets, so it runs under an **ASGI** server (Daphne) rather than gunicorn,
with **Redis** as the channel layer holding live game state and the **shared
`wenda_db` MySQL** database for questions, users, and grades.

```
Internet ──► nginx (TLS, HTTP→HTTPS, static files, /ws/ upgrade)
                │  proxy
                ▼
           Daphne ASGI  (127.0.0.1:8001)
              │            │
              ▼            ▼
         Redis 5.x    MySQL  wenda_db  (shared with wenda-quiz)
        (channel layer)
```

---

## 1. Prerequisites

On the server:

- Ubuntu/Debian Linux, a non-root sudo user (the live host uses `deploy`).
- **Python 3.11+** with `venv`.
- **MySQL** reachable, with the shared `wenda_db` database and a user (the app
  uses `DB_USER=wenda`). Wenda-Live reads/writes `kahoot_*` tables in this DB.
- **Redis** (`redis-server`) — apt-installable; it is *not* present by default
  and the app silently falls back to an in-memory channel layer without it,
  which breaks multi-process live games.
- **nginx** + **certbot** for TLS.
- A DNS A record pointing your hostname (e.g. `wenda-live.veztre.site`) at the
  server's public IP.
- The `WENDA_SSO_SECRET` value from the **wenda-quiz** `.env` (must match exactly
  for single-sign-on to work).

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev build-essential \
    redis-server nginx certbot python3-certbot-nginx \
    default-libmysqlclient-dev pkg-config   # needed to build mysqlclient
sudo systemctl enable --now redis-server
```

---

## 2. Get the code

The live deployment checks out under the service user's home, **`/home/deploy/wenda-live`**.
(The templates in `deploy/` say `/srv/wenda-live` — adjust paths to wherever you
actually clone. This guide uses `/home/deploy/wenda-live`.)

```bash
sudo adduser --disabled-password deploy        # if it doesn't exist
sudo su - deploy
git clone <repo-url> ~/wenda-live
cd ~/wenda-live
```

> If the droplet was cloned earlier and is missing the security-hardening
> settings (`SECURE_SSL_REDIRECT`, etc.), just `git pull` — a stale checkout is
> a real gotcha that triggers Django's W008 deploy warning.

---

## 3. Python environment

```bash
cd ~/wenda-live
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

> **Do not** bump `redis` past 5.x. `requirements.txt` pins `redis==5.2.1`
> (`redis>=5,<6`) on purpose: redis-py ≥6 applies its socket timeout to blocking
> reads, so `channels_redis`'s long `BZPOPMIN` receive raises `TimeoutError` and
> drops live WebSocket consumers mid-game.

---

## 4. Configure `.env`

Copy the example and fill in real values. `.env` is gitignored.

```bash
cp .env.example .env
chmod 600 .env
```

Production values that matter (the ones that were wrong on the first deploy
attempt are called out):

```ini
DJANGO_SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(64))">
DJANGO_DEBUG=0                                  # MUST be 0 in prod
DJANGO_ALLOWED_HOSTS=wenda-live.veztre.site
DJANGO_CSRF_TRUSTED_ORIGINS=https://wenda-live.veztre.site

# Shared SSO secret — MUST be identical to wenda-quiz's WENDA_SSO_SECRET
WENDA_SSO_SECRET=<same value as wenda-quiz>

# Shared MySQL
DB_ENGINE=django.db.backends.mysql
DB_NAME=wenda_db
DB_USER=wenda
DB_PASSWORD=<db password>
DB_HOST=127.0.0.1
DB_PORT=3306

# Channel layer — MUST be set, or it falls back to in-memory and live games break
CHANNEL_REDIS_URL=redis://127.0.0.1:6379/0

# HSTS: leave at 0 until you are certain the WHOLE domain is HTTPS. It is
# effectively irreversible for its lifetime and this domain is shared with
# wenda-quiz, so includeSubDomains/preload would force HTTPS on the sibling too.
DJANGO_HSTS_SECONDS=0
```

With `DJANGO_DEBUG=0`, the settings automatically enable `SECURE_SSL_REDIRECT`,
`SESSION_COOKIE_SECURE`, and `CSRF_COOKIE_SECURE`. The app trusts the
`X-Forwarded-Proto` header nginx sends, so it knows the original request was
HTTPS and won't redirect-loop.

---

## 5. Database migrations  ⚠️ read this carefully

The database is **shared** with wenda-quiz, and they share one
`django_migrations` table. This causes two things you must handle:

**a. Never run a bare `migrate`.** Always scope to the `wenda_live` app, or you
will touch wenda-quiz's migration state.

**b. `InconsistentMigrationHistory` on first migrate.** wenda-quiz has already
applied `admin.0001`, whose `swappable_dependency` points at the user model =
`wenda_live.0001` (unapplied here, because `AUTH_USER_MODEL='wenda_live.User'`).
Django refuses to proceed. Fix: fake-record `wenda_live.0001` (it creates **zero
real tables** — all its models are `managed=False`, just a mirror of wenda's
tables), then migrate the rest:

```bash
cd ~/wenda-live

# One-time: record the no-op initial migration so admin's dependency is satisfied
venv/bin/python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'configWendaLive.settings')
django.setup()
from django.db.migrations.recorder import MigrationRecorder
from django.db import connection
MigrationRecorder(connection).record_applied('wenda_live', '0001_initial')
"

# Create the kahoot_* tables (migrations 0002–0005). SCOPED to wenda_live.
venv/bin/python manage.py migrate wenda_live
```

For **future** migrations, the rule stays the same:

```bash
venv/bin/python manage.py migrate wenda_live    # never a bare `migrate`
```

---

## 6. Static files

```bash
venv/bin/python manage.py collectstatic --noinput
```

This populates `staticfiles/`, which nginx serves directly (see below).

---

## 7. systemd service (Daphne)

A template lives at `deploy/wenda-live.service`. The live unit was hand-edited
to use the `/home/deploy` paths and `User=deploy`. Install it:

```bash
sudo tee /etc/systemd/system/wenda-live.service >/dev/null <<'UNIT'
[Unit]
Description=Wenda-Live ASGI server (Daphne)
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=simple
User=deploy
Group=deploy
WorkingDirectory=/home/deploy/wenda-live
EnvironmentFile=/home/deploy/wenda-live/.env
ExecStart=/home/deploy/wenda-live/venv/bin/daphne -b 127.0.0.1 -p 8001 configWendaLive.asgi:application
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now wenda-live
sudo systemctl status wenda-live
```

Daphne binds **loopback only** (`127.0.0.1:8001`); nginx is the public entry
point.

---

## 8. nginx + TLS

A template lives at `deploy/nginx-wenda-live.conf`. The key parts: serve
`/static/` from disk, do the WebSocket `Upgrade` dance on `/ws/`, and use long
proxy timeouts so a full game's WebSocket isn't cut at 60s.

```bash
sudo tee /etc/nginx/sites-available/wenda-live >/dev/null <<'NGINX'
upstream wenda_live_asgi { server 127.0.0.1:8001; }

server {
    listen 80;
    listen [::]:80;
    server_name wenda-live.veztre.site;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name wenda-live.veztre.site;

    # certbot fills in these two lines:
    ssl_certificate     /etc/letsencrypt/live/wenda-live.veztre.site/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/wenda-live.veztre.site/privkey.pem;

    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    client_max_body_size 10M;

    location /static/ {
        alias /home/deploy/wenda-live/staticfiles/;
        access_log off;
        expires 30d;
    }

    location /ws/ {
        proxy_pass http://wenda_live_asgi;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_pass http://wenda_live_asgi;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX

sudo ln -sf /etc/nginx/sites-available/wenda-live /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Issue/attach the certificate (certbot edits the ssl_ lines automatically):
sudo certbot --nginx -d wenda-live.veztre.site
```

> certbot can share a Let's Encrypt account with the sibling wenda-quiz site —
> no separate registration needed.

---

## 9. Verify

```bash
curl -I https://wenda-live.veztre.site/         # 200/302 over HTTPS
sudo journalctl -u wenda-live -f                 # live logs
```

Then end-to-end: open the host flow, create a game, join as a player in another
browser, and advance a question. The player should connect over `wss://` and
the round should progress. (The live deploy was verified this way.)

---

## 10. Redeploying after changes

```bash
sudo su - deploy
cd ~/wenda-live
git pull

# only if dependencies changed:
venv/bin/pip install -r requirements.txt
# (if redis ever got bumped, force it back: venv/bin/pip install 'redis<6')

# only if there are new wenda_live migrations:
venv/bin/python manage.py migrate wenda_live      # NEVER a bare migrate

# only if static assets changed:
venv/bin/python manage.py collectstatic --noinput

exit
sudo systemctl restart wenda-live
```

---

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| WebSocket connects then drops mid-game | redis-py ≥6 installed. Pin `redis==5.2.1`. |
| Live game state not shared across players | `CHANNEL_REDIS_URL` empty (in-memory fallback) or `redis-server` not running. |
| `InconsistentMigrationHistory` for `admin.0001`/`wenda_live.0001` | Fake-record `wenda_live.0001` (§5), then `migrate wenda_live`. |
| Django W008 / missing `SECURE_SSL_REDIRECT` | Stale checkout — `git pull`. Confirm `DJANGO_DEBUG=0`. |
| Redirect loop on HTTPS | nginx must send `X-Forwarded-Proto $scheme`; Django reads it via `SECURE_PROXY_SSL_HEADER`. |
| 502 Bad Gateway | Daphne not running — `sudo systemctl status wenda-live`, `journalctl -u wenda-live -f`. |
| CSRF 403 on POST | Add the scheme+host to `DJANGO_CSRF_TRUSTED_ORIGINS`. |
| SSO from wenda-quiz fails | `WENDA_SSO_SECRET` must be byte-identical in both apps' `.env`. |

**Logs:** `sudo journalctl -u wenda-live -f` (sibling: `-u wenda-quiz`).

**Sibling app:** wenda-quiz runs separately as `wenda-quiz.service` (gunicorn)
behind its own nginx site. If its site returns 502, it may just be stopped:
`sudo systemctl enable --now wenda-quiz`. It is unrelated to wenda-live.
