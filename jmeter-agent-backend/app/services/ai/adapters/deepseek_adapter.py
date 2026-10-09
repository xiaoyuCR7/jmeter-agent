from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from app.services.ai.adapters.base import managed_request, managed_stream, BaseAIAdapter
from app.models.schemas import AIChatMessage


class DeepSeekAdapter(BaseAIAdapter):
    """DeepSeek适配器 (使用OpenAI兼容接口)"""
    
    def __init__(self, api_key: str, model: str, api_base: Optional[str] = None):
        super().__init__(api_key, model, api_base)
        self.client = AsyncOpenAI(api_key=api_key, base_url=api_base or "https://api.deepseek.com")
    
    @managed_request
    async def chat(self, messages: List[AIChatMessage], **kwargs) -> str:
        client = self.client.with_options(max_retries=kwargs['max_retries']) if 'max_retries' in kwargs else self.client
        response = await client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in self._text_messages(messages)],
            temperature=kwargs.get("temperature", 0.2),
            max_tokens=kwargs.get("max_tokens", 8000),
        )
        return response.choices[0].message.content or ""
    
    @managed_stream
    async def chat_stream(self, messages: List[AIChatMessage], **kwargs) -> AsyncIterator[str]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in self._text_messages(messages)],
            temperature=kwargs.get("temperature", 0.2),
            max_tokens=kwargs.get("max_tokens", 8000),
            stream=True,
        )
        try:
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        finally:
            await response.close()
    
    async def generate(self, prompt: str, **kwargs) -> str:
        return await self.chat([AIChatMessage(role="user", content=prompt)], **kwargs)
