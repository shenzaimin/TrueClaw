from __future__ import annotations

from trueclaw.config.schema import AppConfig, ProviderConfig
from trueclaw.llm.mock import MockOpenAICompatibleProvider
from trueclaw.llm.openai_compatible import OpenAICompatibleProvider
from trueclaw.llm.provider import LLMProvider


def make_provider(cfg: AppConfig) -> LLMProvider:
    name = cfg.agents["defaults"].provider
    if name == "mock":
        return MockOpenAICompatibleProvider()
    pcfg = cfg.providers.get(name)
    if pcfg is None:
        raise ValueError(f"provider not configured: {name}")
    if not pcfg.apiKey:
        raise ValueError(
            f"provider {name} requires apiKey "
            f"(env TRUECLAW__providers__{name}__apiKey or config file)"
        )
    return OpenAICompatibleProvider(
        api_base=pcfg.apiBase,
        api_key=pcfg.apiKey,
        timeout_sec=float(pcfg.timeoutSec),
    )


def provider_summary(pcfg: ProviderConfig) -> str:
    key = "set" if pcfg.apiKey else "missing"
    return f"base={pcfg.apiBase} model={pcfg.model} apiKey={key}"
