import os
from decimal import Decimal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "AI Customer Assistant"
    environment: str = "development"
    secret_key: str = "change-me"
    # Where the app is reachable from the outside (WhatsApp webhook, links in alerts).
    public_base_url: str = ""
    database_url: str = "postgresql+psycopg://assistant:assistant@localhost:5432/assistant"
    cors_origins: str = "*"
    timezone: str = "Africa/Tunis"

    # LLM: "openai", "gemini" (Google's free tier works), or "mock" for tests and keyless runs.
    llm_provider: str = "mock"
    openai_api_key: str = ""
    openai_base_url: str = ""
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    chat_model: str = "gpt-5-mini"
    # On a free tier: the demo page says the costs shown are what the paid tier would charge.
    llm_free_tier: bool = False
    # Tried once when the main model is overloaded or rate-limited (free tiers often are).
    fallback_model: str = ""
    # Sent only to reasoning models; "minimal" keeps answers fast and cheap.
    reasoning_effort: str = "minimal"
    embedding_model: str = "text-embedding-3-small"
    # The database column is vector(1536); Gemini's models are asked for 1536 instead of 3072.
    embedding_dimensions: int = 1536
    extraction_model: str = "gpt-5-mini"
    # USD per 1M tokens, the provider's paid price (developers.openai.com/api/docs/pricing,
    # aistudio.google.com/docs/pricing). On a free tier, it's what the same traffic would cost.
    price_input_per_m: Decimal = Decimal("0.25")
    price_cached_input_per_m: Decimal = Decimal("0.025")
    price_output_per_m: Decimal = Decimal("2.00")
    price_embedding_per_m: Decimal = Decimal("0.02")
    usd_to_tnd: Decimal = Decimal("2.95")

    # Retrieval
    retrieval_top_k: int = 5
    # Cosine distance above which a chunk is treated as unrelated to the question.
    retrieval_max_distance: float = 0.75
    history_messages: int = 12

    # Guards for a public demo running on a real API key.
    max_message_chars: int = 1000
    max_messages_per_conversation: int = 40
    max_conversations_per_ip_per_day: int = 25
    daily_budget_usd: Decimal = Decimal("2.00")
    max_pdf_mb: int = 4

    # Where a new lead goes: any of sheets, email, n8n
    lead_sinks: str = "sheets,email"
    followup_delay_minutes: int = 24 * 60

    google_service_account_json: str = ""
    google_sheet_id: str = ""
    # A view-only link shown on the demo page, so visitors can watch their lead arrive.
    public_sheet_url: str = ""

    # Team alerts by e-mail, through any SMTP server (Gmail with an app password, Brevo, OVH...).
    # Port 465 uses SSL, any other port STARTTLS (unless smtp_starttls is off, e.g. Mailpit).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    email_from: str = ""
    # The sales team: one address, or several separated by commas.
    team_email: str = ""

    # WhatsApp Cloud API (Meta): the second channel customers can chat on.
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    # Any random string, typed again in Meta's webhook settings.
    whatsapp_verify_token: str = ""
    # The Meta app's secret: every webhook call is signed with it.
    whatsapp_app_secret: str = ""
    # The business number customers write to, for the wa.me links (e.g. +1 555 123 4567).
    whatsapp_display_number: str = ""
    whatsapp_api_version: str = "v23.0"
    # WhatsApp allows free text only within 24 h of the customer's last message. After that, the
    # follow-up needs an approved template, with {{1}} = first name and {{2}} = business.
    whatsapp_followup_template: str = ""
    whatsapp_template_language: str = "fr"

    n8n_lead_webhook_url: str = ""
    # Bearer token for /internal/* (n8n callbacks and the Vercel schedule).
    internal_secret: str = ""
    # Vercel sends this one as the Bearer token of its scheduled calls.
    cron_secret: str = ""

    admin_password: str = "demo1234"
    access_token_expire_minutes: int = 480
    # Public demo: masks phone numbers in the dashboard and the sheet, shows demo hints.
    demo_mode: bool = True

    @model_validator(mode="after")
    def _check_production(self):
        if self.environment == "production" and self.secret_key == "change-me":
            raise ValueError("SECRET_KEY must be set in production")
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini")
        return self

    @property
    def serverless(self) -> bool:
        return bool(os.getenv("VERCEL"))

    @property
    def base_url(self) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        host = os.getenv("VERCEL_PROJECT_PRODUCTION_URL")
        return f"https://{host}" if host else "http://localhost:8000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def lead_sink_set(self) -> set[str]:
        return {s.strip() for s in self.lead_sinks.split(",") if s.strip()}


settings = Settings()
