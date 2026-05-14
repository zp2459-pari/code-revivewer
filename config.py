"""
Unified configuration for Code Review Agent.
All settings can be overridden via environment variables.
"""

import os
from typing import Dict, Any, Optional


class Config:
    # === Project ===
    PROJECT_ROOT: str = os.getenv("PROJECT_ROOT", os.getcwd())

    # === Primary LLM (Strong model for deep review) ===
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "kimi")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "")

    # === Sub LLM (Cheap/lightweight model for summarization) ===
    SUB_LLM_PROVIDER: str = os.getenv("SUB_LLM_PROVIDER", "kimi")
    SUB_LLM_API_KEY: str = os.getenv("SUB_LLM_API_KEY", "")
    SUB_LLM_BASE_URL: str = os.getenv("SUB_LLM_BASE_URL", "")
    SUB_LLM_MODEL: str = os.getenv("SUB_LLM_MODEL", "")

    # === Review Settings ===
    REVIEW_TEMPERATURE: float = float(os.getenv("REVIEW_TEMPERATURE", "0.1"))
    SUB_TEMPERATURE: float = float(os.getenv("SUB_TEMPERATURE", "0.3"))
    MAX_DIFF_LENGTH: int = int(os.getenv("MAX_DIFF_LENGTH", "100000"))
    MAX_FILES_PER_BATCH: int = int(os.getenv("MAX_FILES_PER_BATCH", "10"))
    MAX_REVIEW_TOKENS: int = int(os.getenv("MAX_REVIEW_TOKENS", "4096"))

    # === Static Analysis ===
    ENABLE_LINTER: bool = os.getenv("ENABLE_LINTER", "true").lower() == "true"

    # === Output ===
    OUTPUT_FORMAT: str = os.getenv("OUTPUT_FORMAT", "json")
    OUTPUT_REPORT_PATH: str = os.getenv("OUTPUT_REPORT_PATH", "review_report.json")

    # === Database & Rules ===
    RULES_JSON_PATH: str = os.getenv("RULES_JSON_PATH", "team_rules.json")
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
    DB_USER: str = os.getenv("DB_USER", "root")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "Lenovo@123")
    DB_NAME: str = os.getenv("DB_NAME", "code_review_db")

    # === Knowledge Graph ===
    ENABLE_KG: bool = os.getenv("ENABLE_KG", "true").lower() == "true"
    KG_CACHE_FILE: str = os.getenv("KG_CACHE_FILE", "kg_cache.pkl")

    # === Git Mode ===
    # "pr" = diff against target branch (default)
    # "patch" = diff HEAD~1..HEAD (single commit / gerrit workflow)
    GIT_MODE: str = os.getenv("GIT_MODE", "pr")
    TARGET_BRANCH: str = os.getenv("TARGET_BRANCH", "")


# Provider defaults
_DEFAULT_MODELS: Dict[str, str] = {
    "kimi": "kimi-k2-5",
    "deepseek": "deepseek-chat",
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-4.1",
}

_DEFAULT_BASE_URLS: Dict[str, str] = {
    "kimi": "https://api.moonshot.cn/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "claude": "https://api.anthropic.com/v1",
    "openai": "https://api.openai.com/v1",
}


def get_llm_config(
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a complete provider config dict with fallbacks."""
    p = provider or Config.LLM_PROVIDER
    key = api_key or Config.LLM_API_KEY or os.getenv(f"{p.upper()}_API_KEY", "")
    url = base_url or Config.LLM_BASE_URL or _DEFAULT_BASE_URLS.get(p, "")
    mdl = model or Config.LLM_MODEL or _DEFAULT_MODELS.get(p, "")

    if not key:
        raise ValueError(
            f"API key for provider '{p}' is missing. "
            f"Set LLM_API_KEY or {p.upper()}_API_KEY env var."
        )

    return {
        "provider": p,
        "api_key": key,
        "base_url": url,
        "model": mdl,
    }


def get_sub_llm_config() -> Dict[str, Any]:
    """Config for the cheap sub-agent (summarizer / context reader)."""
    p = Config.SUB_LLM_PROVIDER or Config.LLM_PROVIDER
    key = (
        Config.SUB_LLM_API_KEY
        or Config.LLM_API_KEY
        or os.getenv(f"{p.upper()}_API_KEY", "")
    )
    url = (
        Config.SUB_LLM_BASE_URL
        or Config.LLM_BASE_URL
        or _DEFAULT_BASE_URLS.get(p, "")
    )
    mdl = (
        Config.SUB_LLM_MODEL
        or Config.LLM_MODEL
        or _DEFAULT_MODELS.get(p, "")
    )

    if not key:
        raise ValueError(
            f"API key for sub-agent provider '{p}' is missing."
        )

    return {
        "provider": p,
        "api_key": key,
        "base_url": url,
        "model": mdl,
    }
