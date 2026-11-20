# Orbit Agents frontend

This is a buildless local frontend for the Qwen + GitHub MCP backend.

## Run

From the project root:

```powershell
python -m http.server 5173 --directory frontend
```

Open:

```text
http://127.0.0.1:5173
```

The login is currently a UI-only demo. Any non-empty username and password are accepted. GitHub chat requests are sent to:

```text
http://127.0.0.1:8000/chat
```
