"""
Module IA Assistant - Assistant intelligent de trading.

L'assistant IA utilise un LLM (OpenAI GPT-4 ou Anthropic Claude) pour :
- Expliquer les signaux de trading
- Analyser les trades fermes
- Resumer les performances
- Repondre aux questions de l'utilisateur
- Suggerrer des ameliorations

L'IA ne peut JAMAIS ouvrir une position seule.
"""

from arty_trading.modules.ai.assistant import AIAssistant
from arty_trading.modules.ai.base import AIProvider, AIResponse, AIMessage

__all__ = ["AIAssistant", "AIProvider", "AIResponse", "AIMessage"]
