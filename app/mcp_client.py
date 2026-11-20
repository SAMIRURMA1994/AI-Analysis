from contextlib import AsyncExitStack
import asyncio
import fnmatch
import json
import os
from pathlib import Path
import shutil
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import httpx

from .config import settings


class StdioMcpClient:
    """Maintains one stdio MCP connection to an MCP server."""

    def __init__(self, label: str, command: str, args: list[str], env: dict[str, str]) -> None:
        self.label = label
        self.command = command
        self.args = args
        self.env = env
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def connect(self) -> None:
        await self._check_mcp_runtime()

        params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env={
                **os.environ,
                **self.env,
            },
        )
        read_stream, write_stream = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
        try:
            await self.session.initialize()
        except Exception as exc:
            if self.label == "MySQL":
                raise RuntimeError(
                    "MySQL MCP closed its connection during startup. Check MYSQL_DSN, "
                    "MySQL credentials, database name, and that the database is reachable "
                    f"from Docker. Original error: {exc}"
                ) from exc
            raise
        await self._print_available_tools()

    async def _check_mcp_runtime(self) -> None:
        """Fail early with a useful message when the MCP runtime is unavailable."""
        command = self.command
        if not shutil.which(command):
            raise RuntimeError(
                f"{self.label} MCP command '{command}' was not found on PATH. "
                "Install Docker Desktop or configure GITHUB_MCP_COMMAND."
            )

        if command.lower() != "docker":
            return

        process = await asyncio.create_subprocess_exec(
            command,
            "info",
            "--format",
            "{{.ServerVersion}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode(errors="replace").strip()
            raise RuntimeError(
                f"Docker is installed but unavailable for {self.label} MCP. "
                "Start Docker Desktop and wait until it reports 'Running', "
                f"then restart this API. Docker said: {detail}"
            )

        print(
            f"Docker daemon is available (server {stdout.decode(errors='replace').strip()})",
            flush=True,
        )

    async def _print_authenticated_user(self) -> None:
        """Print the GitHub account for this token without exposing the token."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    "https://api.github.com/user",
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {settings.github_token}",
                    },
                )
                response.raise_for_status()
                user = response.json()
            self.authenticated_user = user.get("login")
            print(f"GitHub authenticated user: {user.get('login')}", flush=True)
        except Exception as exc:
            print(f"GitHub identity check failed: {exc}", flush=True)

    async def _print_available_tools(self) -> None:
        """Print every tool exposed by the connected MCP server."""
        try:
            available_tools = await self.tools()
            print(f"Available {self.label} MCP tools:", flush=True)
            for tool in available_tools:
                print(f"- {tool['name']}", flush=True)
        except Exception as exc:
            print(f"GitHub tool discovery failed: {exc}", flush=True)

    async def close(self) -> None:
        try:
            await self._stack.aclose()
        finally:
            self.session = None

    async def tools(self) -> list[dict[str, Any]]:
        if not self.session:
            raise RuntimeError("GitHub MCP client is not connected")
        result = await self.session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                # MCP SDK releases have used both spellings for this field.
                "input_schema": getattr(
                    tool,
                    "inputSchema",
                    getattr(tool, "input_schema", {}),
                ),
            }
            for tool in result.tools
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self.session:
            raise RuntimeError("GitHub MCP client is not connected")
        result = await self.session.call_tool(name, arguments)
        return [getattr(item, "text", item) for item in result.content]


class GithubMcpClient(StdioMcpClient):
    """GitHub MCP connection plus token identity used for repository defaults."""

    def __init__(self) -> None:
        super().__init__(
            "GitHub",
            settings.github_mcp_command,
            settings.github_mcp_args,
            {"GITHUB_PERSONAL_ACCESS_TOKEN": settings.github_token},
        )
        self.authenticated_user: str | None = None

    async def connect(self) -> None:
        if not settings.github_token:
            raise RuntimeError("GITHUB_PERSONAL_ACCESS_TOKEN is not configured")
        await super().connect()
        await self._print_authenticated_user()


class PlaywrightMcpClient(StdioMcpClient):
    """Local, open-source browser automation MCP server."""

    def __init__(self) -> None:
        super().__init__(
            "Playwright",
            settings.playwright_mcp_command,
            settings.playwright_mcp_args,
            {},
        )


class MysqlMcpClient(StdioMcpClient):
    """Optional MySQL MCP connection using a DSN passed to the container."""

    def __init__(self) -> None:
        super().__init__(
            "MySQL",
            settings.mysql_mcp_command,
            settings.mysql_mcp_args,
            {"MYSQL_DSN": settings.mysql_dsn},
        )

    async def connect(self) -> None:
        await super().connect()
        default_database = settings.mysql_database or "<none; all databases mode>"
        print(
            f"[MYSQL] Configured default database: {default_database}",
            flush=True,
        )
        try:
            databases = await self.call("list_databases", {})
            print(
                f"[MYSQL] Databases visible to this MySQL user: "
                f"{str(databases)[:4000]}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"[MYSQL WARNING] Could not list visible databases: {exc}",
                flush=True,
            )


class LocalSearchClient:
    """Read-only local project search exposed through the same tool interface."""

    label = "Local Search"

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self._ignored_directories = {
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
        }
        self._ignored_files = {".env", "*.pem", "*.key", "*.token"}

    async def connect(self) -> None:
        if not self.root.exists():
            raise RuntimeError(f"LOCAL_SEARCH_ROOT does not exist: {self.root}")
        print(f"Local search root: {self.root}", flush=True)

    async def close(self) -> None:
        return None

    async def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "search_local_files",
                "description": (
                    "Search text in project files. Use for questions about local source code, "
                    "configuration, documentation, or tests. This tool is read-only."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Text to find"},
                        "path": {
                            "type": "string",
                            "description": "Optional relative directory or file path",
                        },
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                    "required": ["query"],
                },
                "server": self.label,
            }
        ]

    def _allowed(self, path: Path) -> bool:
        relative_parts = path.relative_to(self.root).parts
        if any(part in self._ignored_directories for part in relative_parts):
            return False
        return not any(
            fnmatch.fnmatch(path.name.lower(), pattern.lower())
            for pattern in self._ignored_files
        )

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if name != "search_local_files":
            raise RuntimeError(f"Unknown local search tool: {name}")
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")
        max_results = min(max(int(arguments.get("max_results", 20)), 1), 100)
        requested_path = str(arguments.get("path", ".")).strip() or "."
        target = (self.root / requested_path).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError("path must stay inside LOCAL_SEARCH_ROOT")
        if not target.exists():
            raise FileNotFoundError(f"Search path does not exist: {requested_path}")

        paths = [target] if target.is_file() else target.rglob("*")
        matches: list[dict[str, Any]] = []
        for path in paths:
            if len(matches) >= max_results or not path.is_file() or not self._allowed(path):
                continue
            try:
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if query.casefold() in line.casefold():
                            matches.append(
                                {
                                    "path": str(path.relative_to(self.root)),
                                    "line": line_number,
                                    "text": line.strip()[:500],
                                }
                            )
                            if len(matches) >= max_results:
                                break
            except (OSError, UnicodeError):
                continue
        return [json.dumps({"query": query, "matches": matches}, ensure_ascii=False)]


class MultiMcpClient:
    """Routes tool discovery and calls across multiple MCP servers."""

    def __init__(self, clients: list[Any]) -> None:
        self.clients = clients
        self.authenticated_user = next(
            (getattr(client, "authenticated_user", None) for client in clients), None
        )
        self._tool_clients: dict[str, StdioMcpClient] = {}
        self._tools_by_server: dict[str, list[dict[str, Any]]] = {}
        self.failed_servers: dict[str, str] = {}

    async def connect(self) -> None:
        for client in self.clients:
            try:
                await client.connect()
            except Exception as exc:
                if client.label == "MySQL":
                    # MySQL is optional. Keep FastAPI and the other MCP
                    # servers available when its container or database is down.
                    self.failed_servers[client.label] = str(exc)
                    print(
                        f"[MCP WARNING] {client.label} MCP unavailable; "
                        f"continuing without it: {exc}",
                        flush=True,
                    )
                    continue
                # Do not close an already-entered stdio AsyncExitStack here.
                # Uvicorn performs lifespan cleanup after startup failure, and
                # closing it from this nested failure path can mask the real
                # error with an AnyIO cancel-scope exception.
                raise RuntimeError(
                    f"{client.label} MCP startup failed: {exc}"
                ) from exc
            server_tools = await client.tools()
            self._tools_by_server[client.label] = [
                {**tool, "server": client.label} for tool in server_tools
            ]
            if not self.authenticated_user:
                self.authenticated_user = getattr(client, "authenticated_user", None)

    async def close(self) -> None:
        for client in reversed(self.clients):
            try:
                await client.close()
            except Exception as exc:
                print(
                    f"[MCP WARNING] Could not close {client.label} MCP cleanly: {exc}",
                    flush=True,
                )

    async def tools(self) -> list[dict[str, Any]]:
        if self._tools_by_server:
            all_tools = [
                tool
                for server_tools in self._tools_by_server.values()
                for tool in server_tools
            ]
            self._tool_clients = {
                tool["name"]: next(
                    client for client in self.clients if client.label == tool["server"]
                )
                for tool in all_tools
            }
            return all_tools

        all_tools: list[dict[str, Any]] = []
        self._tool_clients = {}
        for client in self.clients:
            for tool in await client.tools():
                name = tool["name"]
                if name in self._tool_clients:
                    raise RuntimeError(
                        f"Duplicate MCP tool name '{name}' from {client.label}; "
                        "tool names must be unique."
                    )
                tool = {**tool, "server": client.label}
                all_tools.append(tool)
                self._tool_clients[name] = client
        return all_tools

    async def tools_for_server(self, label: str) -> list[dict[str, Any]]:
        """Return only the tools owned by one selected MCP server."""
        if label not in self._tools_by_server:
            await self.tools()
        return list(self._tools_by_server.get(label, []))

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        client = self._tool_clients.get(name)
        if client is None:
            await self.tools()
            client = self._tool_clients.get(name)
        if client is None:
            raise RuntimeError(f"MCP tool '{name}' is not available")
        return await client.call(name, arguments)
