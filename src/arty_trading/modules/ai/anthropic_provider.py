"""
Provider Anthropic - Utilise l'API Anthropic (Claude 3.5 Sonnet, etc.).

Necessite la variable d'environnement ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.ai.base import AIMessage, AIProvider, AIResponse

logger = get_logger(LogCategory.AI)


class AnthropicProvider(AIProvider):
    """Provider utilisant l'API Anthropic (Claude)."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "claude-3-5-sonnet-20241022",
        max_tokens: int = 2048,
    ) -> None:
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._model = model
        self._max_tokens = max_tokens
        self._client = None

        if not self._api_key:
            logger.warning("Anthropic API key non configuree - mode hors-ligne")

    def _get_client(self):
        """Lazy loading du client Anthropic."""
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic

                self._client = AsyncAnthropic(api_key=self._api_key)
            except ImportError:
                logger.error("Package anthropic non installe. Installez avec: pip install anthropic")
                raise
        return self._client

    @property
    def name(self) -> str:
        return "anthropic"

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
        """Envoie une conversation a Anthropic et retourne la reponse."""
        if not self._api_key:
            return AIResponse(
                content="Erreur : API key Anthropic non configuree. Configurez ANTHROPIC_API_KEY dans le fichier .env",
                model=self._model,
                usage={},
            )

        try:
            client = self._get_client()

            # Anthropic utilise un format legerement different
            api_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

            response = await client.messages.create(
                model=self._model,
                system=system_prompt if system_prompt else "Tu es Arty, un assistant de trading.",
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            content = response.content[0].text if response.content else ""
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            } if response.usage else {}

            logger.info("Anthropic chat | model=%s | tokens=%s", self._model, usage)

            return AIResponse(
                content=content,
                model=self._model,
                usage=usage,
            )
        except Exception as exc:
            logger.error("Erreur Anthropic chat | %s", exc)
            return AIResponse(
                content=f"Erreur lors de la communication avec Anthropic : {exc}",
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
            yield "Erreur : API key Anthropic non configuree"
            return

        try:
            client = self._get_client()

            api_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

            async with client.messages.stream(
                model=self._model,
                system=system_prompt if system_prompt else "Tu es Arty, un assistant de trading.",
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as exc:
            logger.error("Erreur Anthropic stream | %s", exc)
            yield f"Erreur : {exc}"
