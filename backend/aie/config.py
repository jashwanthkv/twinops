"""
aie/config.py — AIE application configuration via Pydantic BaseSettings.

All settings are read from environment variables prefixed with AIE_.
Example: AIE_GROQ_API_KEY maps to AIEConfig.groq_api_key
"""

from pathlib import Path
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class AIEConfig(BaseSettings):
    """Central configuration for the AIE (AI Engine) module.

    Values are loaded from environment variables (prefix AIE_ or standard names),
    then from a .env file if present, then from defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="AIE_",
        env_file=(_ENV_PATH, ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # LLM / Groq
    # ------------------------------------------------------------------ #
    groq_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GROQ_API_KEY", "AIE_GROQ_API_KEY", "groq_api_key"),
        description="Groq API key",
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        validation_alias=AliasChoices("GROQ_MODEL", "AIE_GROQ_MODEL", "groq_model"),
        description="Groq model identifier",
    )

    def get_groq_api_keys(self) -> list[str]:
        """Collect all configured Groq API keys for concurrent pooling and failover."""
        import os
        from dotenv import load_dotenv

        if _ENV_PATH.exists():
            load_dotenv(_ENV_PATH)

        keys: list[str] = []
        for env_name in [
            "GROQ_API_KEY",
            "GROQ_API_KEY1",
            "GROQ_API_KEY2",
            "GROQ_API_KEY_1",
            "GROQ_API_KEY_2",
            "AIE_GROQ_API_KEY",
        ]:
            val = os.environ.get(env_name, "").strip()
            if val and val not in keys:
                keys.append(val)
        if self.groq_api_key and self.groq_api_key.strip() not in keys:
            keys.append(self.groq_api_key.strip())
        return keys

    # ------------------------------------------------------------------ #
    # Application server
    # ------------------------------------------------------------------ #
    app_host: str = Field(default="0.0.0.0", description="Bind host")
    app_port: int = Field(default=8000, description="Bind port")
    debug: bool = Field(default=False, description="Enable debug mode")

    # ------------------------------------------------------------------ #
    # Workspace
    # ------------------------------------------------------------------ #
    workspace_base_dir: str = Field(
        default="./aie_workspaces",
        description="Base directory for repository clones",
    )

    # ------------------------------------------------------------------ #
    # GitHub (optional — for private repos)
    # ------------------------------------------------------------------ #
    github_token: str = Field(default="", description="GitHub personal access token")

    # ------------------------------------------------------------------ #
    # MongoDB & Redis Storage Layer
    # ------------------------------------------------------------------ #
    mongodb_uri: str = Field(
        default="",
        validation_alias=AliasChoices("MONGODBURI", "MONGODB_URI", "AIE_MONGODB_URI", "mongodb_uri"),
        description="MongoDB connection URI",
    )
    mongodb_database: str = Field(
        default="twinops_aie",
        validation_alias=AliasChoices("MONGODB_DATABASE", "MONGODB_DB", "AIE_MONGODB_DB", "mongodb_database"),
        description="MongoDB database name",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("REDIS_URL", "AIE_REDIS_URL", "redis_url"),
        description="Redis connection URL",
    )

    # ------------------------------------------------------------------ #
    # Vector store (ChromaDB)
    # ------------------------------------------------------------------ #
    chroma_mode: str = Field(
        default="memory",
        description="'memory' for in-process, path string for persistence",
    )

    # ------------------------------------------------------------------ #
    # Embedding model
    # ------------------------------------------------------------------ #
    embedding_model: str = Field(
        default="all-MiniLM-L6-v2",
        description="Sentence-Transformers model name",
    )

    # ------------------------------------------------------------------ #
    # Analysis limits / safety
    # ------------------------------------------------------------------ #
    max_file_size_bytes: int = Field(
        default=512 * 1024,  # 512 KB
        description="Maximum size of a single file read during analysis",
    )
    max_files_per_repo: int = Field(
        default=5_000,
        description="Maximum number of files to index per repository",
    )
    analysis_timeout_seconds: int = Field(
        default=600,
        description="Hard wall-clock timeout for a single analysis run",
    )

    # ------------------------------------------------------------------ #
    # CORS
    # ------------------------------------------------------------------ #
    cors_allow_origins: list[str] = Field(
        default=["*"],
        description="CORS allowed origins (use ['*'] for dev)",
    )


# Module-level singleton — import this everywhere
settings = AIEConfig()
