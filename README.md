# Berlin Events Curation Agent

A private tool for the site editor. You chat with it to dig through Berlin events,
and when you find good ones you can pin them to the map as **Editor's Choice**.
It never saves anything on its own: it suggests, you decide. Logging in needs a
password and a code from your authenticator app.

## Quick setup

```bash
pip install -r requirements.txt
```

Make the password hash and save it as `ADMIN_PW_HASH`:

```bash
ADMIN_PASSWORD='your-password' python3 src/agent/_password.py
```

Start it:

```bash
uvicorn wsgi:app --reload
```

Other settings: `DATABASE_URL` and `DEEPSEEK_API_KEY` are required. `REDIS_URL`,
`AGENT_DATABASE_URL`, `COOKIE_SESSION_SECRET` and `ROOT_PATH` are optional.

## Tests

```bash
pytest
```
