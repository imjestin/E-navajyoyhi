# Installing E-Navajyothi

A college management system for attendance, marks, leave and notices, with a
public site and a staff/student portal.

You need a Linux server with **Docker** and the **Docker Compose plugin**.
Two containers are started: the application and a PostgreSQL database.

---

## 1. Get the files

```sh
git clone <your-repository-url> enavajyothi
cd enavajyothi
cp .env.example .env
```

## 2. Edit `.env`

Only three lines usually matter:

```ini
POSTGRES_PASSWORD=choose-a-long-random-password
ALLOWED_HOSTS=portal.yourcollege.ac.in,192.168.1.50
CSRF_TRUSTED_ORIGINS=https://portal.yourcollege.ac.in
```

- **`POSTGRES_PASSWORD`** has no default; compose refuses to start without it.
- **`ALLOWED_HOSTS`** must list every name or IP people will type. Miss this and
  Django answers `DisallowedHost` instead of showing the site. On a LAN with no
  domain name, put the server's IP address here.
- **`CSRF_TRUSTED_ORIGINS`** is only needed if you serve the site over HTTPS
  through a proxy. Also set `BEHIND_TLS_PROXY=True` in that case.

You do **not** set a `SECRET_KEY`. One is generated on first start and kept in
the data volume.

## 3. Start it

```sh
docker compose up -d --build
```

The application waits for the database, applies migrations, and starts. Watch it
with `docker compose logs -f web`.

## 4. Complete the setup wizard

Open the server in a browser — `http://your-server:8000`. Every address
redirects to the installer until it is finished.

1. **Checks** — confirms the database is reachable.
2. **Administrator** — create the account you will sign in with. Keep the
   password safe: this is the only administrator, and the installer cannot be
   run again afterwards.
3. **Institution** — college name, contact details, logo and colours. All of it
   is editable later under **Settings**.

Finishing takes you to the sign-in page. From there, add departments, courses,
sessions, staff and students — or import students in bulk from a CSV under
**People → Import Data**.

---

## Everyday operations

### Backups

Two things need backing up: the database and the uploads volume.

```sh
# database
docker compose exec -T db pg_dump -U enavajyothi enavajyothi > backup-$(date +%F).sql

# uploads (logos, photographs) and the secret key
docker run --rm -v enavajyothi_app_data:/data -v "$PWD":/out alpine \
  tar czf /out/uploads-$(date +%F).tar.gz -C /data .
```

Restoring the database:

```sh
docker compose exec -T db psql -U enavajyothi enavajyothi < backup-2026-08-09.sql
```

### Upgrading

```sh
git pull
docker compose up -d --build
```

Migrations run automatically at start-up. Take a backup first.

### Behind an existing nginx or Caddy

Point your proxy at `http://127.0.0.1:8000` and set, in `.env`:

```ini
BEHIND_TLS_PROXY=True
CSRF_TRUSTED_ORIGINS=https://portal.yourcollege.ac.in
```

Then `docker compose up -d`. The application serves its own static files
(via WhiteNoise), so the proxy only needs to forward requests.

### Sending SMS or WhatsApp notices

Optional. Put your Twilio credentials in `.env` (`TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`) and set `TWILIO_ENABLED=True`, then
turn the channel on under **Settings → Notifications**. Credentials are never
entered through the web interface.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `DisallowedHost` | The address you typed is not in `ALLOWED_HOSTS`. |
| CSRF failure on every form | Serving over HTTPS without `CSRF_TRUSTED_ORIGINS` and `BEHIND_TLS_PROXY=True`. |
| `port is already allocated` | Something else uses 8000 or 5432. Change `PORT` in `.env`. |
| Setup wizard will not open | It has already been completed. It is closed permanently by design. |
| Logo disappeared after a rebuild | The `app_data` volume was removed. Restore from your uploads backup. |

### Starting over

This destroys all data:

```sh
docker compose down -v
docker compose up -d --build
```

---

## Running without Docker (development)

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # set SECRET_KEY or let one be generated
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Without `DATABASE_URL` it uses a local SQLite file. To load sample data for a
look around: `python manage.py seed_demo` (and `seed_demo --delete` to remove it).
