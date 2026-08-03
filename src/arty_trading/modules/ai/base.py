"""
Interface de base pour les providers IA (OpenAI, Anthropic, etc.).

Definit le contrat que tous les providers doivent implementer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AIMessage:
    """Message dans une conversation IA."""

    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class AIResponse:
    """Reponse d'un provider IA."""

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class AIProvider(ABC):
    """
    Interface abstraite pour les providers IA.

    Implementations :
    - OpenAIProvider (GPT-4, GPT-4o, etc.)
    - AnthropicProvider (Claude 3.5 Sonnet, etc.)
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[AIMessage],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AIResponse:
        """
        Envoie une conversation a l'IA et retourne sa reponse.

        Args:
            messages: Liste de messages (user/assistant)
            system_prompt: Prompt systeme definissant le contexte
            temperature: Creativite (0 = precis, 1 = creatif)
            max_tokens: Nombre maximum de tokens en sortie

        Returns:
            AIResponse avec le contenu et les metadonnees
        """
        ...

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[AIMessage],
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ):
        """
        Version streaming de chat - yield les chunks de texte.

        Args:
            messages: Liste de messages
            system_prompt: Prompt systeme
            temperature: Creativite
            max_tokens: Tokens max

        Yields:
            str: Chunks de texte au fur et a mesure
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Nom du provider (openai, anthropic)."""
        ...

    @property
    @abstractmethod
    def model(self) -> str:
        """Modele utilise (gpt-4o-mini, claude-3-5-sonnet, etc.)."""
        ...
