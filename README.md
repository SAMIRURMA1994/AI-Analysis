# Qwen GitHub MCP Assistant

This project connects `qwen2.5:1.5b` running through Ollama to GitHub and optional MySQL MCP servers.

## Requirements

- Python 3.11+
- Docker Desktop or Docker Engine
- A publicly reachable Ollama endpoint on EC2 with `qwen2.5:1.5b` available
- A GitHub personal access token with only the permissions this assistant needs

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The project is configured to call the EC2 Ollama endpoint from `OLLAMA_BASE_URL`.
Use the public URL without `/api/generate`; the application appends
`/api/generate` automatically. For example:

```env
OLLAMA_BASE_URL=http://40.176.52.34:11434
OLLAMA_MODEL=qwen2.5:1.5b
```

For production, protect the public Ollama endpoint with HTTPS and authentication
or a private network/VPN. Do not expose an unauthenticated Ollama port directly
to the internet.

The GitHub integration uses GitHub's official MCP server image:
`ghcr.io/github/github-mcp-server`. Docker must be running before starting the
application. The configured toolsets are `repos`, `issues`, `users`, and
`context`, which includes repository operations such as `list_branches`.

### Optional MySQL MCP

Set these values in `.env` to enable MySQL. The MySQL MCP server uses the free
open-source image `ghcr.io/askdba/mysql-mcp-server:latest` and is configured
read-only by default:

```env
MYSQL_MCP_ENABLED=true
MYSQL_DSN=appuser:password@tcp(host.docker.internal:3306)/my_database?parseTime=true
```

Use `host.docker.internal` when MySQL is running on the Windows host. If MySQL
is another Docker Compose service, use its service name instead, for example
`tcp(mysql:3306)`. Create a dedicated MySQL user with only the needed
permissions; do not use the root account. MySQL must be reachable from Docker
before starting the API. When enabled, the assistant automatically routes
GitHub questions to GitHub and database questions to MySQL.

On Windows, activate with `.venv\\Scripts\\Activate.ps1` and copy `.env.example` to `.env`.

Test it:

```bash
curl -X POST http://127.0.0.1:8000/chat \\
  -H "Content-Type: application/json" \\
  -d '{"message":"List open issues in octocat/Hello-World"}'
```

The host launches the GitHub MCP server over stdio, discovers its tools, asks Qwen whether a tool is needed, executes only a discovered tool, and then asks Qwen to produce the final natural-language response.

The application also exposes a read-only local tool named `search_local_files`.
It searches text files below `LOCAL_SEARCH_ROOT` (default: the project directory)
and ignores `.env`, virtual environments, Git metadata, and common dependency
directories. For example: `search local files for LOCAL_SEARCH_ROOT`.

Do not expose the GitHub token in the request body or commit `.env`.
