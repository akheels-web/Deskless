# Deskless

A modern, self-hostable helpdesk & ticketing system. **Django + DRF, MIT licensed** — own it, brand it, extend it, and keep customer data on your own infrastructure.

Two experiences from one deploy: a **customer portal** (help center, submit & track requests) and an **agent console** (queue, SLAs, reporting, admin).

---

## Features

**Ticketing**
- Workflow: Open → In Progress → Pending → Escalate → Closed, with mandatory notes on key transitions
- Threaded conversations with public replies and internal notes
- Linked tickets with cascade-close and shared resolution
- Categories, tags, groups (teams), and file attachments
- `@mention` teammates in internal notes
- Full audit history per ticket (creation, assignment, status changes, reopen)

**SLA & automation**
- Resolution targets per priority, plus per-group / per-category SLA policies
- Business-hours-aware clock with configurable working days and holidays
- SLA pauses automatically while a ticket is Pending
- Overdue flagging and one-time breach emails
- Triggers: keyword rules that auto-route to a group and set priority
- AI triage: auto-prioritizes new tickets by keywords (or an LLM if configured)

**Customer portal**
- Help-center home with knowledge-base search
- Submit a request (with attachments) — no login required
- Track tickets via a passwordless magic link
- Per-ticket status page; reopen a closed ticket within a configurable window
- CSAT rating on resolution

**Agent console**
- Queue with left-panel views (Open / Mine / Unassigned / Closed / All), search, pagination, bulk actions
- Dashboard widgets: open, overdue, assigned-to-me, unassigned, closed
- Reports: volume by status / priority / agent, average time to resolve, CSAT
- In-console knowledge-base authoring and canned replies

**Administration**
- Roles: customer / agent / admin, managed in-app; add & remove users
- Customer management with CSV and Excel bulk import
- In-app branding: name, logo, theme colors (no redeploy)
- Configurable email (SMTP), SLA, business hours, groups, triggers — all from Settings

**Security & integrations**
- SSO via Google, Microsoft, and Zoho (OAuth)
- Login rate-limiting (django-axes), HTTPS hardening, secure cookies, HSTS
- REST API (token auth) + outbound webhooks on ticket events

---

## Tech stack

Django 6 · Django REST Framework · PostgreSQL (SQLite for dev) · django-allauth · django-axes · gunicorn + WhiteNoise · Docker.

---

## Quick start (local)

Requires Python 3.12+.

```bash
git clone https://github.com/akheels-web/Deskless.git
cd Deskless

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000 for the customer portal, or sign in at
`/accounts/login/` for the agent console. Defaults to SQLite and prints email
to the console — no external services needed to try it.

---

## Key routes

| Route | Description |
|-------|-------------|
| `/` | Customer portal (help center) |
| `/kb/`, `/submit/`, `/track/` | Knowledge base, submit a request, track tickets |
| `/status/<token>/` | Customer status page for a single ticket |
| `/dashboard/` | Agent dashboard |
| `/queue/`, `/t/<id>/` | Ticket queue and detail |
| `/reports/`, `/team/`, `/customers/`, `/settings/` | Admin & reporting |
| `/api/tickets/`, `/api/comments/` | REST API |
| `POST /api/token/` | Obtain an API token |
| `/admin/` | Django admin |

---

## Configuration

Most settings are editable in-app under **Settings** (branding, SMTP, SLA,
business hours, reopen window). Environment variables provide the deploy-time
defaults — see [.env.example](.env.example).

| Variable | Purpose | Default |
|----------|---------|---------|
| `SECRET_KEY` | Django crypto key | insecure dev key |
| `DEBUG` | Debug mode | `True` |
| `ALLOWED_HOSTS` | Comma-separated hostnames | `localhost,127.0.0.1` |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated `https://` origins | empty |
| `DATABASE_URL` | Postgres connection URL | SQLite file |
| `EMAIL_HOST` + `EMAIL_*` | Outbound SMTP fallback | console |
| `IMAP_HOST` + `IMAP_*` | Inbound email-to-ticket | off |
| `OPENAI_API_KEY` | Enable LLM triage | keyword triage |

---

## Scheduled jobs

Run these on a schedule (cron, Task Scheduler, or your platform's scheduler):

```bash
python manage.py fetch_email   # pull inbound email into tickets
python manage.py check_sla     # flag overdue tickets and email breaches (~every 15 min)
```

---

## Deployment

Runs as a container with Postgres via Docker Compose:

```bash
cp .env.example .env           # set SECRET_KEY, ALLOWED_HOSTS, DB_PASSWORD, site URL, etc.
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Put a TLS-terminating reverse proxy (Caddy, nginx, Traefik) in front. Full
production guide — proxy configs, backups, updates, SSO callbacks,
troubleshooting — in **[DEPLOYMENT.md](DEPLOYMENT.md)**.

### Updating

```bash
git pull
docker compose up -d --build   # migrations run automatically on start
```

---

## License

MIT — see [LICENSE](LICENSE).
