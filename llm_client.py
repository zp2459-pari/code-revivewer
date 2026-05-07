"""
Unified LLM client supporting multiple providers via OpenAI-compatible API.
Providers: Kimi, DeepSeek, Claude (OpenAI-compatible), OpenAI, etc.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from logger import log


@dataclass
class LLMResponse:
    content: str
    usage: Dict[str, int]
    model: str


class OpenAICompatibleClient:
    """Generic OpenAI-compatible HTTP client."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int = 180,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.chat_url = f"{self.base_url}/chat/completions"

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> LLMResponse:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        if max_tokens:
            payload["max_tokens"] = max_tokens

        if response_format:
            payload["response_format"] = response_format

        try:
            log.debug(
                f"LLM Request -> {self.model} | "
                f"messages={len(messages)} chars={sum(len(m.get('content', '')) for m in messages)}"
            )
            resp = requests.post(
                self.chat_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})

            return LLMResponse(
                content=content,
                usage=usage,
                model=self.model,
            )

        except requests.HTTPError as e:
            log.error(
                f"LLM HTTP Error {e.response.status_code}: {e.response.text[:300]}"
            )
            raise
        except Exception as e:
            log.error(f"LLM Request Failed: {e}")
            raise


def create_client(config: Dict[str, Any]) -> OpenAICompatibleClient:
    """Factory: create client from config dict."""
    return OpenAICompatibleClient(
        api_key=config["api_key"],
        base_url=config["base_url"],
        model=config["model"],
    )
