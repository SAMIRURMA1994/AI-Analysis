import json
from typing import Any

import httpx

from .config import settings


class OllamaClient:
    def __init__(self) -> None:
        self.url = f"{settings.ollama_base_url.rstrip('/')}/api/generate"

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        prompt = "\n\n".join(
            f"{message['role'].upper()}: {message['content']}"
            for message in messages
        )
        payload = {
            "model": settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(self.url, json=payload)
            response.raise_for_status()
            data = response.json()
        return data["response"]


def parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
