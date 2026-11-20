import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv(override=True)


def _csv(value: str) -> list[str]:
    # `|` allows an individual argument to contain commas, such as
    # GITHUB_TOOLSETS=repos,issues,users,context.
    delimiter = "|" if "|" in value else ","
    return [item.strip() for item in value.split(delimiter) if item.strip()]


def _dsn_database(dsn: str) -> str:
    """Extract the database name from a go-sql-driver/mysql DSN."""
    without_params = dsn.split("?", 1)[0]
    return without_params.rsplit("/", 1)[-1].strip()


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
    github_token: str = os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN", "").strip().strip('"').strip("'")
    github_mcp_command: str = os.getenv("GITHUB_MCP_COMMAND", "docker")
    github_mcp_args: list[str] = None  # type: ignore[assignment]
    playwright_mcp_enabled: bool = os.getenv("PLAYWRIGHT_MCP_ENABLED", "false").lower() == "true"
    playwright_mcp_command: str = os.getenv("PLAYWRIGHT_MCP_COMMAND", "npx")
    playwright_mcp_args: list[str] = None  # type: ignore[assignment]
    mysql_mcp_enabled: bool = os.getenv("MYSQL_MCP_ENABLED", "false").lower() == "true"
    mysql_mcp_command: str = os.getenv("MYSQL_MCP_COMMAND", "docker")
    mysql_mcp_args: list[str] = None  # type: ignore[assignment]
    mysql_dsn: str = os.getenv("MYSQL_DSN", "").strip().strip('"').strip("'")
    mysql_database: str = ""
    local_search_root: str = os.getenv("LOCAL_SEARCH_ROOT", ".")
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "mysql_database", _dsn_database(self.mysql_dsn))
        if self.github_mcp_args is None:
            configured_args = _csv(
                os.getenv(
                    "GITHUB_MCP_ARGS",
                    "run|-i|--rm|-e|GITHUB_PERSONAL_ACCESS_TOKEN|-e|GITHUB_TOOLSETS=repos,issues,pull_requests,users,context|ghcr.io/github/github-mcp-server",
                )
            )
            # Keep older .env files compatible with Docker-based MCP startup.
            if self.github_mcp_command == "docker" and configured_args[:1] != ["run"]:
                configured_args.insert(0, "run")
            object.__setattr__(
                self,
                "github_mcp_args",
                configured_args,
            )
        if self.mysql_mcp_args is None:
            configured_args = _csv(
                os.getenv(
                    "MYSQL_MCP_ARGS",
                    "run|-i|--rm|-e|MYSQL_DSN|-e|MYSQL_MCP_EXTENDED=1|-e|MYSQL_MCP_STRICT_READ_ONLY=1|ghcr.io/askdba/mysql-mcp-server:latest",
                )
            )
            if self.mysql_mcp_command == "docker" and configured_args[:1] != ["run"]:
                configured_args.insert(0, "run")
            object.__setattr__(self, "mysql_mcp_args", configured_args)
        if self.playwright_mcp_args is None:
            configured_args = _csv(
                os.getenv("PLAYWRIGHT_MCP_ARGS", "-y|@playwright/mcp@latest")
            )
            object.__setattr__(self, "playwright_mcp_args", configured_args)


settings = Settings()
