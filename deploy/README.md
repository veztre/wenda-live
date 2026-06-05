# Deploying Wenda-Live to a VPS

Stack: **nginx** (TLS + static + WebSocket upgrade) → **Daphne** (ASGI) → Django,
with **Redis** for the Channels layer and the shared **MySQL** `wenda_db`.

Files here:
- `wenda-live.service` — systemd unit that runs Daphne.
- `nginx-wenda-live.conf` — nginx server block (HTTPS, `/static/`, `/ws/`).

## 1. Code + dependencies
```bash
sudo apt install python3-venv build-essential pkg-config default-libmysqlclient-dev redis-server nginx
git clone <repo> /srv/wenda-live && cd /srv/wenda-live
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## 2. Production `.env` (in /srv/wenda-live/.env, chmod 600)
Copy `.env.example` and set **at least**:
```
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<python -c "import secrets;print(secrets.token_urlsafe(64))">
WENDA_SSO_SECRET=<same value as wenda-quiz>
DJANGO_ALLOWED_HOSTS=live.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://live.example.com
CHANNEL_REDIS_URL=redis://127.0.0.1:6379/0   # REQUIRED — see note
DB_NAME=wenda_db
DB_USER=...
DB_PASSWORD=...
DB_HOST=127.0.0.1
```
> ⚠️ **CHANNEL_REDIS_URL is mandatory.** Without it the app falls back to an
> in-memory channel layer that is per-process, so host and players on different
> Daphne workers can't see each other and live games silently break.

## 3. Static files
```bash
venv/bin/python manage.py collectstatic --noinput   # -> staticfiles/
```

## 4. Services
```bash
sudo cp deploy/wenda-live.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now wenda-live

sudo cp deploy/nginx-wenda-live.conf /etc/nginx/sites-available/wenda-live
sudo ln -s /etc/nginx/sites-available/wenda-live /etc/nginx/sites-enabled/
sudo certbot --nginx -d live.example.com          # issues certs, fills ssl_*
sudo nginx -t && sudo systemctl reload nginx
```

## 5. Verify
```bash
venv/bin/python manage.py check --deploy   # should be clean
systemctl status wenda-live
```
Then load `https://live.example.com`, sign in, and start a game with a second
browser joined as a student to confirm the WebSocket round-trip works.

## Notes
- **Migrations:** the `kahoot_*`/`quiz_*` tables are shared with wenda-quiz.
  Don't run `migrate` for them here unless wenda-quiz hasn't created them yet —
  coordinate schema changes on the wenda-quiz side.
- **HSTS:** off by default. Once the whole domain is HTTPS, set
  `DJANGO_HSTS_SECONDS=31536000` (and consider the include-subdomains/preload
  flags) — it's hard to undo, and the domain is shared with wenda-quiz.
