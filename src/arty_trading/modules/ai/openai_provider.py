"""
Provider OpenAI - Utilise l'API OpenAI (GPT-4, GPT-4o, etc.).

Necessite la variable d'environnement OPENAI_API_KEY.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.ai.base import AIMessage, AIProvider, AIResponse

logger = get_logger(LogCategory.AI)


class OpenAIProvider(AIProvider):
    """Provider utilisant l'API OpenAI."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "gpt-4o-mini",
        max_tokens: int = 2048,
    ) -> None:
        self._api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._model = model
        self._max_tokens = max_tokens
        self._client = None

        if not self._api_key:
            logger.warning("OpenAI API key non configuree - mode hors-ligne")

    def _get_client(self):
        """Lazy loading du client OpenAI."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(api_key=self._api_key)
            except ImportError:
                logger.error("Package openai non installe. Installez avec: pip install openai")
                raise
        return self._client

    @property
    def name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return self._model

    async def chat(
        self,
        messages: list[AIMessage],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AIResponse:
        """Envoie une conversation a OpenAI et retourne la reponse."""
        if not self._api_key:
            return AIResponse(
                content="Erreur : API key OpenAI non configuree. Configurez OPENAI_API_KEY dans le fichier .env",
                model=self._model,
                usage={},
            )

        try:
            client = self._get_client()

            # Construire les messages
            api_messages = []
            if system_prompt:
                api_messages.append({"role": "system", "content": system_prompt})
            for msg in messages:
                api_messages.append({"role": msg.role, "content": msg.content})

            response = await client.chat.completions.create(
                model=self._model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.choices[0].message.content or ""
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            } if response.usage else {}

            logger.info("OpenAI chat | model=%s | tokens=%s", self._model, usage.get("total_tokens", 0))

            return AIResponse(
                content=content,
                model=self._model,
                usage=usage,
            )
        except Exception as exc:
            logger.error("Erreur OpenAI chat | %s", exc)
            return AIResponse(
                content=f"Erreur lors de la communication avec OpenAI : {exc}",
                model=self._model,
                usage={},
            )

    async def stream_chat(
        self,
        messages: list[AIMessage],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        """Version streaming de chat."""
        if not self._api_key:
            yield "Erreur : API key OpenAI non configuree"
            return

        try:
            client = self._get_client()

            api_messages = []
            if system_prompt:
                api_messages.append({"role": "system", "content": system_prompt})
            for msg in messages:
                api_messages.append({"role": msg.role, "content": msg.content})

            stream = await client.chat.completions.create(
                model=self._model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as exc:
            logger.error("Erreur OpenAI stream | %s", exc)
            yield f"Erreur : {exc}"
