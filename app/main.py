from contextlib import asynccontextmanager
import traceback
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .config import settings
from .mcp_client import (
    GithubMcpClient,
    LocalSearchClient,
    MysqlMcpClient,
    MultiMcpClient,
    PlaywrightMcpClient,
)
from .ollama import OllamaClient
from .orchestrator import Assistant

github = GithubMcpClient()
mysql = MysqlMcpClient()
playwright = PlaywrightMcpClient()
local_search = LocalSearchClient(settings.local_search_root)
mcp = MultiMcpClient(
    [github, local_search]
    + ([playwright] if settings.playwright_mcp_enabled else [])
    + ([mysql] if settings.mysql_mcp_enabled else [])
)
assistant = Assistant(mcp, OllamaClient())


@asynccontextmanager
async def lifespan(_: FastAPI):
    await mcp.connect()
    yield
    await mcp.close()


app = FastAPI(title="Qwen GitHub MCP Assistant", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    print("[TRACE] ChatRequest validation failed", flush=True)
    print(f"[TRACE] Validation errors: {exc.errors()}", flush=True)
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )


class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=10000,
        description="The user's natural-language request",
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message must contain non-whitespace text")
        return cleaned


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/mcp/servers")
async def mcp_servers() -> dict[str, Any]:
    """Expose connected MCP servers and their live tools to the frontend."""
    tools = await mcp.tools()
    servers: dict[str, dict[str, Any]] = {}
    for tool in tools:
        server = str(tool.get("server", "Unknown"))
        entry = servers.setdefault(server, {"name": server, "tools": []})
        entry["tools"].append(tool["name"])
    return {"servers": list(servers.values())}


@app.post("/chat")
async def chat(request: ChatRequest) -> dict[str, Any]:
    print(
        "[TRACE] ChatRequest validation passed: message is required, string, "
        "1-10000 characters, and non-whitespace",
        flush=True,
    )
    print(f"[TRACE] User query: {request.message}", flush=True)
    try:
        result = await assistant.answer(request.message)
        calls = result.get("tool_calls", [])
        names = [call["name"] for call in calls]
        result["tool_name"] = (
            names[0] if len(names) == 1 else names if names else None
        )
        print(f"[TRACE] Host answer returned: {result.get('answer', '')}", flush=True)
        print(f"[TRACE] FastAPI response tool_name: {result['tool_name']}", flush=True)
        return result
    except Exception as exc:
        error_detail = f"{type(exc).__name__}: {exc!r}"
        print(f"Chat request failed: {error_detail}", flush=True)
        traceback.print_exc()
        raise HTTPException(status_code=502, detail=error_detail) from exc
