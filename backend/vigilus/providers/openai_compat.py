"""OpenAI-compatible provider implementation (Ollama, LM Studio, vLLM, etc)."""

from __future__ import annotations

import httpx

from vigilus.providers.openai_provider import OpenAIProvider


class OpenAICompatProvider(OpenAIProvider):
    """Adapter for OpenAI-compatible endpoints."""

    def __init__(
        self, 
        base_url: str, 
        api_key: str | None = None, 
        default_model: str = "gpt-3.5-turbo",
        extra_headers: dict | None = None
    ):
        self.api_key = api_key or "sk-no-key-required"
        self.default_model = default_model
        self.base_url = base_url.rstrip("/")
        
        # We need to initialize the underlying OpenAIProvider client with custom base_url
        import openai
        
        default_headers = {}
        if extra_headers:
            default_headers.update(extra_headers)
            
        self.client = openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            default_headers=default_headers,
        )

    async def test_connection(self) -> dict:
        """Fetch models from the compat endpoint, falling back to a chat call."""
        try:
            async with httpx.AsyncClient() as client:
                headers = {}
                if self.api_key and self.api_key != "sk-no-key-required":
                    headers["Authorization"] = f"Bearer {self.api_key}"
                # Add any extra headers from the client
                headers.update(self.client._custom_headers)

                resp = await client.get(f"{self.base_url}/models", headers=headers, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()

                models = [m.get("id") for m in data.get("data", [])]
                return {"ok": True, "models": models}
        except Exception as models_error:
            # Some providers (e.g. Z.AI coding plan) restrict which routes a key
            # may call and reject /models. Verify with a minimal chat completion
            # against the default model instead.
            try:
                await self.client.chat.completions.create(
                    model=self.default_model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=16,
                )
                return {"ok": True, "models": [self.default_model]}
            except Exception as chat_error:
                return {
                    "ok": False,
                    "error": (
                        f"Model listing failed ({models_error}); "
                        f"chat completion with '{self.default_model}' also failed: {chat_error}"
                    ),
                }
