"""Runtime configuration.

Everything is environment-driven; nothing secret is ever read from a file
inside the repository. See `.env.example` for the full annotated list.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AuthMode = Literal["google", "github", "jwt", "static", "none"]
StorageBackend = Literal["supabase", "local"]
EmbeddingProvider = Literal["none", "openai", "gemini"]

#: Fixed by the `VECTOR(1536)` columns in migration 0002. Changing this
#: requires a migration; the value is exported so code can assert on it.
EMBEDDING_DIM = 1536


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="PC_",
    )

    # ----------------------------------------------------------- deployment
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    host: str = "0.0.0.0"  # noqa: S104 - container binds all interfaces by design
    port: int = 8080

    #: Public origin of this service, e.g. https://context.example.com.
    #: Used for OAuth metadata, the RFC 8707 resource identifier and audience
    #: validation, so it MUST match what clients actually dial.
    base_url: AnyHttpUrl = Field(default=AnyHttpUrl("http://localhost:8080"))

    #: Optional high-entropy path segment, e.g. "a7f3...". The MCP endpoint
    #: becomes /mcp/<server_id>. This is defence in depth ONLY - it is not
    #: authentication and the server behaves identically without it.
    server_id: str = ""

    #: Hostnames the server will answer on (DNS-rebinding protection).
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ------------------------------------------------------------- database
    database_url: SecretStr = SecretStr("")
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_statement_timeout_ms: int = 15_000

    # ------------------------------------------------------------------ auth
    auth_mode: AuthMode = "google"

    #: The only identities allowed in. Matched against the token's email and
    #: subject claims. An empty list in a non-dev environment is refused at
    #: startup rather than silently letting the world in.
    allowed_subjects: Annotated[list[str], NoDecode] = Field(default_factory=list)

    oauth_client_id: str = ""
    oauth_client_secret: SecretStr = SecretStr("")
    #: Secret used to sign the tokens this server hands to MCP clients.
    oauth_signing_key: SecretStr = SecretStr("")

    #: External-IdP mode (auth_mode="jwt"): tokens are verified against this
    #: JWKS and their `scope` claim is authoritative for capability scopes.
    jwt_jwks_uri: str = ""
    jwt_issuer: str = ""
    jwt_audience: str = ""

    #: auth_mode="static": dev/test only. Maps bearer token -> principal.
    static_tokens: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)

    #: Capability scopes granted per OAuth client, by client name substring.
    #: e.g. {"chatgpt": ["context:read", "memory:read"], "*": ["*"]}
    #: See personal_context_mcp/auth/scopes.py for the vocabulary.
    client_scope_policy: Annotated[dict[str, list[str]], NoDecode] = Field(default_factory=dict)

    #: Shared secret the local Obsidian worker uses on /worker/*. Separate
    #: from MCP auth: the worker is our own code, not a model.
    worker_api_key: SecretStr = SecretStr("")

    rate_limit_per_minute: int = 120
    rate_limit_burst: int = 40

    # --------------------------------------------------------------- storage
    storage_backend: StorageBackend = "supabase"
    supabase_url: str = ""
    supabase_service_key: SecretStr = SecretStr("")
    supabase_bucket: str = "personal-context-mcp"
    #: Cold-storage bucket for original provider export archives. Never served.
    supabase_archive_bucket: str = "personal-context-mcp-archives"
    local_storage_root: str = ".data/storage"

    signed_url_ttl_seconds: int = 900
    signed_url_ttl_sensitive_seconds: int = 120
    max_artifact_file_bytes: int = 100 * 1024 * 1024
    max_bundle_files: int = 500
    max_bundle_bytes: int = 200 * 1024 * 1024
    #: Archive-bomb guards for provider export ZIPs.
    max_archive_entries: int = 50_000
    max_archive_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024
    max_archive_compression_ratio: int = 200

    # ------------------------------------------------------------ classifier
    classifier: Literal["jev", "rules"] = "jev"
    typesafe_api_key: SecretStr = SecretStr("")
    jev_model: str = "jev-latest"
    jev_timeout_seconds: float = 12.0
    jev_max_retries: int = 2
    #: Jev accepts 64k tokens per request (32k for state + longest question).
    #: We budget conservatively in characters.
    jev_max_state_chars: int = 48_000
    jev_max_project_candidates: int = 40

    auto_route_confidence: float = 0.85
    review_confidence: float = 0.55
    #: Historical bulk imports promote far more conservatively than live work.
    import_auto_route_confidence: float = 0.93
    import_promote_to_vault: bool = False
    #: Sensitivity every imported conversation starts at. The deterministic
    #: screen (importers/sensitivity.py) can only raise it. Set to
    #: highly_sensitive to keep an entire backfill inside the process.
    import_default_sensitivity: Literal["normal", "private", "highly_sensitive"] = "normal"
    import_sensitivity_screen: bool = True
    #: Conversations classified at once during an import or reclassification.
    import_concurrency: int = 4
    #: Reuse a stored Jev answer when the exact same state, candidates,
    #: question set and model were classified before. Makes re-runs
    #: deterministic and free.
    classification_cache: bool = True

    # ------------------------------------------------------------ extraction
    #: Pass 2 of the historical backfill: a frontier model reads the full
    #: transcript of conversations Jev marked for deep extraction.
    extraction_provider: Literal["anthropic", "none"] = "none"
    anthropic_api_key: SecretStr = SecretStr("")
    extraction_model: str = "claude-sonnet-5"
    extraction_max_transcript_chars: int = 120_000
    extraction_concurrency: int = 3

    # ------------------------------------------------------------ embeddings
    embedding_provider: EmbeddingProvider = "none"
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    #: Sensitivity levels that must never be sent to an external embedding or
    #: classification API.
    no_external_sensitivity: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["highly_sensitive"]
    )

    # -------------------------------------------------------------- obsidian
    obsidian_enabled: bool = True
    obsidian_mirror_max_mb: float = 5.0
    obsidian_vault_roots: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=lambda: {
            "inbox": "00-Inbox",
            "daily": "10-Daily",
            "project": "20-Projects",
            "research": "30-Research",
            "wiki": "40-Wiki",
            "concept": "50-Concepts",
            "resource": "60-Resources",
            "atlas": "70-Atlas",
            "shared": "80-Shared",
            "profile": "85-Profiles",
            "system": "90-System",
            "archive": "99-Archive",
        }
    )

    # ------------------------------------------------------------ retrieval
    default_token_budget: int = 2500
    context_search_default_limit: int = 10
    #: Cosine distance below which two memories are treated as the same claim.
    memory_dedup_distance: float = 0.08

    @field_validator(
        "allowed_subjects",
        "allowed_hosts",
        "no_external_sensitivity",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept both JSON arrays and plain comma-separated env values."""
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                return json.loads(v)
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @field_validator("static_tokens", "client_scope_policy", "obsidian_vault_roots", mode="before")
    @classmethod
    def _parse_json_map(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            return json.loads(v) if v else {}
        return v

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @property
    def mcp_path(self) -> str:
        """Path the MCP endpoint is served at."""
        return f"/mcp/{self.server_id}" if self.server_id else "/mcp"

    @property
    def resource_url(self) -> str:
        """RFC 8707 canonical resource identifier for this MCP server."""
        return f"{str(self.base_url).rstrip('/')}{self.mcp_path}"

    def validate_startup(self) -> list[str]:
        """Refuse to boot into an unsafe configuration.

        Returns a list of fatal problems; an empty list means OK. Kept as a
        return value rather than raising so the CLI can print all of them at
        once instead of one per run.
        """
        problems: list[str] = []

        if not self.database_url.get_secret_value():
            problems.append("PC_DATABASE_URL is required.")

        if self.auth_mode == "none":
            if self.is_prod:
                problems.append(
                    "PC_AUTH_MODE=none exposes every tool unauthenticated and is "
                    "refused when PC_ENVIRONMENT=prod."
                )
        elif self.auth_mode == "static":
            if self.is_prod:
                problems.append("PC_AUTH_MODE=static is refused when PC_ENVIRONMENT=prod.")
            if not self.static_tokens:
                problems.append("PC_AUTH_MODE=static requires PC_STATIC_TOKENS.")
        elif self.auth_mode in ("google", "github"):
            if not self.oauth_client_id or not self.oauth_client_secret.get_secret_value():
                problems.append(
                    f"PC_AUTH_MODE={self.auth_mode} requires PC_OAUTH_CLIENT_ID and "
                    "PC_OAUTH_CLIENT_SECRET."
                )
            if not self.allowed_subjects:
                problems.append(
                    "PC_ALLOWED_SUBJECTS must list the account(s) allowed to use this "
                    "server. Without it any account at the identity provider could "
                    "read your entire history."
                )
        elif self.auth_mode == "jwt" and not self.jwt_jwks_uri:
            problems.append("PC_AUTH_MODE=jwt requires PC_JWT_JWKS_URI.")

        if self.is_prod and str(self.base_url).startswith("http://"):
            problems.append("PC_BASE_URL must be https:// in production.")

        if self.storage_backend == "supabase" and not self.supabase_url:
            problems.append("PC_STORAGE_BACKEND=supabase requires PC_SUPABASE_URL.")

        if self.classifier == "jev" and not self.typesafe_api_key.get_secret_value():
            problems.append(
                "PC_CLASSIFIER=jev requires PC_TYPESAFE_API_KEY (or set "
                "PC_CLASSIFIER=rules to run deterministic classification only)."
            )

        if (
            self.extraction_provider == "anthropic"
            and not self.anthropic_api_key.get_secret_value()
        ):
            problems.append("PC_EXTRACTION_PROVIDER=anthropic requires PC_ANTHROPIC_API_KEY.")

        if not 0.0 < self.review_confidence <= self.auto_route_confidence <= 1.0:
            problems.append("Require 0 < PC_REVIEW_CONFIDENCE <= PC_AUTO_ROUTE_CONFIDENCE <= 1.")

        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


SettingsDep = Annotated[Settings, "settings"]
