"""Known providers and their default models.

A provider is a config entry: name, base URL, and a starter model list with
prices. Anything here can be edited from the dashboard afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelSeed:
    name: str
    alias: str | None = None
    context_length: int | None = None
    input_price_per_m: float = 0.0
    output_price_per_m: float = 0.0
    supports_vision: bool = False
    supports_tools: bool = False


@dataclass(frozen=True)
class ProviderSeed:
    name: str
    label: str
    base_url: str
    docs_url: str
    free_tier: bool
    priority: int
    models: list[ModelSeed] = field(default_factory=list)


# Prices are USD per 1M tokens, checked 2025-10. Free-tier values are 0.
PROVIDERS: list[ProviderSeed] = [
    ProviderSeed(
        name="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        docs_url="https://console.groq.com/docs",
        free_tier=True,
        priority=10,
        models=[
            ModelSeed("llama-3.3-70b-versatile", "Llama 3.3 70B", 131072, 0, 0, False, True),
            ModelSeed("llama-3.1-8b-instant", "Llama 3.1 8B", 131072, 0, 0, False, True),
            ModelSeed("openai/gpt-oss-120b", "GPT-OSS 120B", 131072, 0, 0, False, True),
            ModelSeed("openai/gpt-oss-20b", "GPT-OSS 20B", 131072, 0, 0, False, True),
        ],
    ),
    ProviderSeed(
        name="gemini",
        label="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        docs_url="https://ai.google.dev/gemini-api/docs/openai",
        free_tier=True,
        priority=20,
        models=[
            ModelSeed("gemini-2.5-flash", "Gemini 2.5 Flash", 1048576, 0.30, 2.50, True, True),
            ModelSeed("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", 1048576, 0.10, 0.40, True, True),
            ModelSeed("gemini-2.5-pro", "Gemini 2.5 Pro", 1048576, 1.25, 10.00, True, True),
        ],
    ),
    ProviderSeed(
        name="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        docs_url="https://openrouter.ai/docs",
        free_tier=True,
        priority=30,
        models=[
            ModelSeed("deepseek/deepseek-chat-v3.1:free", "DeepSeek V3.1 (free)", 163840, 0, 0, False, True),
            ModelSeed("meta-llama/llama-3.3-70b-instruct:free", "Llama 3.3 70B (free)", 131072, 0, 0, False, True),
            ModelSeed("qwen/qwen3-235b-a22b:free", "Qwen3 235B (free)", 131072, 0, 0, False, True),
        ],
    ),
    ProviderSeed(
        name="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        docs_url="https://api-docs.deepseek.com",
        free_tier=False,
        priority=40,
        models=[
            ModelSeed("deepseek-chat", "DeepSeek Chat", 65536, 0.27, 1.10, False, True),
            ModelSeed("deepseek-reasoner", "DeepSeek Reasoner", 65536, 0.55, 2.19, False, True),
        ],
    ),
    ProviderSeed(
        name="mistral",
        label="Mistral",
        base_url="https://api.mistral.ai/v1",
        docs_url="https://docs.mistral.ai",
        free_tier=True,
        priority=50,
        models=[
            ModelSeed("mistral-small-latest", "Mistral Small", 131072, 0.10, 0.30, False, True),
            ModelSeed("mistral-large-latest", "Mistral Large", 131072, 2.00, 6.00, False, True),
        ],
    ),
    ProviderSeed(
        name="cerebras",
        label="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        docs_url="https://inference-docs.cerebras.ai",
        free_tier=True,
        priority=60,
        models=[
            ModelSeed("llama-3.3-70b", "Llama 3.3 70B (Cerebras)", 131072, 0, 0, False, True),
        ],
    ),
    ProviderSeed(
        name="together",
        label="Together AI",
        base_url="https://api.together.xyz/v1",
        docs_url="https://docs.together.ai",
        free_tier=False,
        priority=70,
        models=[
            ModelSeed("meta-llama/Llama-3.3-70B-Instruct-Turbo", "Llama 3.3 70B Turbo", 131072, 0.88, 0.88, False, True),
        ],
    ),
    ProviderSeed(
        name="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        docs_url="https://platform.openai.com/docs",
        free_tier=False,
        priority=80,
        models=[
            ModelSeed("gpt-4o-mini", "GPT-4o mini", 128000, 0.15, 0.60, True, True),
            ModelSeed("gpt-4o", "GPT-4o", 128000, 2.50, 10.00, True, True),
        ],
    ),
    ProviderSeed(
        name="custom",
        label="Custom (OpenAI-compatible)",
        base_url="",
        docs_url="",
        free_tier=False,
        priority=200,
        models=[],
    ),
]

BY_NAME: dict[str, ProviderSeed] = {p.name: p for p in PROVIDERS}


def defaults_for(name: str) -> ProviderSeed | None:
    return BY_NAME.get(name)
