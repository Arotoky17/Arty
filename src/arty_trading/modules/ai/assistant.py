"""
Assistant IA principal - Orchestre le provider IA et les modules Arty.

L'assistant peut :
- Expliquer les signaux
- Analyser les trades
- Resumer les performances
- Repondre aux questions
- Suggerrer des ameliorations

L'IA ne peut JAMAIS ouvrir une position seule.
"""

from __future__ import annotations

from typing import Any

from arty_trading.core.enums import LogCategory
from arty_trading.logging.logger import get_logger
from arty_trading.modules.ai.base import AIMessage, AIProvider, AIResponse
from arty_trading.modules.ai.anthropic_provider import AnthropicProvider
from arty_trading.modules.ai.openai_provider import OpenAIProvider

logger = get_logger(LogCategory.AI)

# Prompt systeme par defaut
DEFAULT_SYSTEM_PROMPT = """Tu es Arty, un assistant IA specialise en trading Forex Smart Money Concepts (SMC/ICT).

Tu aides le trader a :
1. Comprendre les signaux generes par la plateforme
2. Analyser les trades passes (reussites et echecs)
3. Resumer les performances de trading
4. Repondre aux questions sur le marche, les strategies et les concepts SMC
5. Suggerrer des ameliorations (ajustements de risque, strategies, timeframes)

REGLES DE SECURITE :
- Tu ne peux JAMAIS ouvrir, fermer ou modifier une position
- Tu ne fais que des suggestions et des analyses
- Tu rappelles toujours les regles de gestion du risque
- Tu es honnete sur les risques et les incertitudes

CONCEPTS SMC que tu connais :
- Break Of Structure (BOS), Change Of Character (CHoCH), Market Structure Shift (MSS)
- Fair Value Gap (FVG), Inverse FVG (IFVG)
- Order Block, Breaker Block, Mitigation Block
- Liquidity Sweep, Equal High, Equal Low
- Premium/Discount, Optimal Trade Entry (OTE)
- Sessions (Londres, New York, Asie), Kill Zones

Reponds toujours en francais, de maniere claire et professionnelle."""


class AIAssistant:
    """
    Assistant IA principal d'Arty.

    Utilise un provider IA (OpenAI ou Anthropic) pour generer
    des reponses contextuelles sur le trading.
    """

    def __init__(
        self,
        provider: AIProvider | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> None:
        self._provider = provider
        self._system_prompt = system_prompt
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._history: list[AIMessage] = []

    @classmethod
    def from_settings(cls, settings: Any) -> AIAssistant:
        """
        Cree un assistant depuis les parametres de configuration.

        Args:
            settings: Instance de AISettings
        """
        provider_name = getattr(settings, "provider", "openai").lower()

        if provider_name == "anthropic":
            provider = AnthropicProvider(
                api_key=getattr(settings, "anthropic_api_key", ""),
                model=getattr(settings, "anthropic_model", "claude-3-5-sonnet-20241022"),
                max_tokens=getattr(settings, "max_tokens", 2048),
            )
        else:
            provider = OpenAIProvider(
                api_key=getattr(settings, "api_key", ""),
                model=getattr(settings, "model", "gpt-4o-mini"),
                max_tokens=getattr(settings, "max_tokens", 2048),
            )

        return cls(
            provider=provider,
            system_prompt=getattr(settings, "system_prompt", DEFAULT_SYSTEM_PROMPT),
            temperature=getattr(settings, "temperature", 0.7),
            max_tokens=getattr(settings, "max_tokens", 2048),
        )

    @property
    def provider(self) -> AIProvider | None:
        """Retourne le provider IA utilise."""
        return self._provider

    @property
    def history(self) -> list[AIMessage]:
        """Retourne l'historique de la conversation."""
        return self._history

    def clear_history(self) -> None:
        """Vide l'historique de conversation."""
        self._history.clear()

    async def chat(self, user_message: str) -> AIResponse:
        """
        Pose une question a l'assistant IA.

        Args:
            user_message: Question ou message de l'utilisateur

        Returns:
            AIResponse avec la reponse de l'IA
        """
        if self._provider is None:
            return AIResponse(
                content="Assistant IA non configure. Ajoutez OPENAI_API_KEY ou ANTHROPIC_API_KEY dans le fichier .env",
                model="none",
                usage={},
            )

        # Ajouter le message utilisateur a l'historique
        self._history.append(AIMessage(role="user", content=user_message))

        # Appeler le provider
        response = await self._provider.chat(
            messages=self._history,
            system_prompt=self._system_prompt,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        # Ajouter la reponse a l'historique
        self._history.append(AIMessage(role="assistant", content=response.content))

        logger.info("AI chat | provider=%s | model=%s", self._provider.name, self._provider.model)

        return response

    async def explain_signal(self, signal_data: dict) -> AIResponse:
        """
        Demande a l'IA d'expliquer un signal de trading.

        Args:
            signal_data: Dictionnaire contenant les donnees du signal
                        (symbol, direction, entry, sl, tp, confidence, strategy, smc_concepts)

        Returns:
            AIResponse avec l'explication du signal
        """
        prompt = self._build_signal_explanation_prompt(signal_data)
        return await self.chat(prompt)

    async def analyze_trade(self, trade_data: dict) -> AIResponse:
        """
        Demande a l'IA d'analyser un trade ferme.

        Args:
            trade_data: Dictionnaire avec les donnees du trade
                        (symbol, direction, entry, exit, profit, duration, result)

        Returns:
            AIResponse avec l'analyse du trade
        """
        prompt = self._build_trade_analysis_prompt(trade_data)
        return await self.chat(prompt)

    async def performance_summary(self, stats_data: dict) -> AIResponse:
        """
        Demande a l'IA de resumer les performances de trading.

        Args:
            stats_data: Dictionnaire avec les statistiques
                        (total_trades, win_rate, profit_factor, drawdown, etc.)

        Returns:
            AIResponse avec le resume des performances
        """
        prompt = self._build_performance_summary_prompt(stats_data)
        return await self.chat(prompt)

    async def suggest_improvements(self, context_data: dict) -> AIResponse:
        """
        Demande a l'IA de suggerer des ameliorations.

        Args:
            context_data: Dictionnaire avec le contexte actuel
                          (strategies actives, risk config, recent trades, etc.)

        Returns:
            AIResponse avec les suggestions
        """
        prompt = self._build_improvement_prompt(context_data)
        return await self.chat(prompt)

    def _build_signal_explanation_prompt(self, signal: dict) -> str:
        """Construit le prompt pour expliquer un signal."""
        return f"""Analyse et explique ce signal de trading genere par la plateforme Arty :

**Signal :**
- Symbole : {signal.get('symbol', 'N/A')}
- Direction : {signal.get('direction', 'N/A')}
- Prix d'entree : {signal.get('entry_price', 'N/A')}
- Stop Loss : {signal.get('stop_loss', 'N/A')}
- Take Profit : {signal.get('take_profit', 'N/A')}
- Confiance : {signal.get('confidence', 'N/A')}/1.0
- Strategie : {signal.get('strategy_name', 'N/A')}
- Ratio Risque/Rendement : {signal.get('risk_reward_ratio', 'N/A')}

**Concepts SMC detectes :**
{signal.get('smc_concepts', 'Aucun concept detecte')}

**Justification :**
{signal.get('justification', 'Aucune justification fournie')}

Peux-tu :
1. Expliquer pourquoi ce signal a ete genere
2. Analyser la coherence des concepts SMC
3. Evaluer la qualite du ratio risque/rendement
4. Donner ton avis sur le niveau de confiance
5. Rappeler les risques eventuels"""

    def _build_trade_analysis_prompt(self, trade: dict) -> str:
        """Construit le prompt pour analyser un trade."""
        result = trade.get('result', 'N/A')
        profit = trade.get('profit', 'N/A')
        direction = trade.get('direction', 'N/A')
        symbol = trade.get('symbol', 'N/A')

        return f"""Analyse ce trade ferme :

**Trade :**
- Symbole : {symbol}
- Direction : {direction}
- Prix d'entree : {trade.get('entry_price', 'N/A')}
- Prix de sortie : {trade.get('exit_price', 'N/A')}
- Profit/Perte : {profit}
- Resultat : {result}
- Duree : {trade.get('duration', 'N/A')}
- Strategie : {trade.get('strategy_name', 'N/A')}
- Stop Loss : {trade.get('stop_loss', 'N/A')}
- Take Profit : {trade.get('take_profit', 'N/A')}

Peux-tu :
1. Analyser ce qui s'est bien passe ou mal passe
2. Identifier si le trade respectait les regles de la strategie
3. Suggerer ce qui aurait pu etre ameliore
4. Donner une note sur 10 pour la qualite de l'execution"""

    def _build_performance_summary_prompt(self, stats: dict) -> str:
        """Construit le prompt pour resumer les performances."""
        return f"""Resume les performances de trading de la plateforme Arty :

**Statistiques :**
- Trades totaux : {stats.get('total_trades', 0)}
- Trades gagnants : {stats.get('winning_trades', 0)}
- Trades perdants : {stats.get('losing_trades', 0)}
- Taux de reussite : {stats.get('win_rate', 0)}%
- Profit Factor : {stats.get('profit_factor', 'N/A')}
- Profit total : {stats.get('total_profit', 'N/A')}
- Solde initial : {stats.get('initial_balance', 'N/A')}
- Solde final : {stats.get('final_balance', 'N/A')}
- Drawdown maximal : {stats.get('max_drawdown', 0)}%
- Sharpe Ratio : {stats.get('sharpe_ratio', 'N/A')}
- Expectancy : {stats.get('expectancy', 'N/A')}
- Rendement total : {stats.get('total_return_pct', 0)}%
- Series de gains max : {stats.get('max_consecutive_wins', 0)}
- Series de pertes max : {stats.get('max_consecutive_losses', 0)}

Peux-tu :
1. Donner un resume global des performances en 3-4 phrases
2. Identifier les points forts
3. Identifier les points faibles
4. Donner des recommandations pour ameliorer les performances"""

    def _build_improvement_prompt(self, context: dict) -> str:
        """Construit le prompt pour suggerer des ameliorations."""
        return f"""Analyse la configuration actuelle d'Arty et suggerer des ameliorations :

**Configuration actuelle :**
- Strategies actives : {context.get('active_strategies', 'N/A')}
- Concepts SMC actifs : {context.get('active_concepts', 'N/A')}
- Risque par trade : {context.get('risk_per_trade', 'N/A')}
- Positions max : {context.get('max_open_positions', 'N/A')}
- Drawdown max : {context.get('max_drawdown', 'N/A')}
- Symboles : {context.get('symbols', 'N/A')}
- Timeframe : {context.get('timeframe', 'N/A')}

**Performances recentes :**
- Taux de reussite recent : {context.get('recent_win_rate', 'N/A')}
- Trades recents : {context.get('recent_trades_count', 0)}

Peux-tu :
1. Analyser la configuration actuelle
2. Identifier 3 ameliorations prioritaires
3. Suggerer des ajustements de parametres
4. Recommander des strategies a activer/desactiver"""
