"""
Central configuration. All secrets come from environment variables (.env).
Never hardcode API keys — .env is gitignored, .env.example has only placeholders.
"""
import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Settings:
    # ── Core API keys ────────────────────────────────────────────────────
    apollo_api_key: str = os.getenv("APOLLO_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")

    # ── Email delivery ───────────────────────────────────────────────────
    # Gmail: set GMAIL_CREDENTIALS_PATH to your credentials.json path.
    # First real run opens a browser for OAuth consent → saves token.json.
    gmail_credentials_path: str = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    # SendGrid: alternative to Gmail for transactional email
    sendgrid_api_key: str = os.getenv("SENDGRID_API_KEY", "")

    # ── LinkedIn browser automation ──────────────────────────────────────
    # Used by linkedin_executor.py (Playwright). Not LinkedIn's official API.
    linkedin_email: str = os.getenv("LINKEDIN_EMAIL", "")
    linkedin_password: str = os.getenv("LINKEDIN_PASSWORD", "")
    # Legacy key field — kept for backward compat, not used by executor
    linkedin_api_key: str = os.getenv("LINKEDIN_API_KEY", "")

    # ── Execution mode ───────────────────────────────────────────────────
    # human_approval_mode=True: AI fills forms, human clicks Send
    # human_approval_mode=False: fully autonomous (use with rate limits)
    human_approval_mode: bool = os.getenv("HUMAN_APPROVAL_MODE", "true").lower() == "true"

    # ── Research cache ───────────────────────────────────────────────────
    research_cache_ttl_hours: int = int(os.getenv("RESEARCH_CACHE_TTL_HOURS", "168"))  # 1 week

    # ── LLM pricing ─────────────────────────────────────────────────────
    # Approximate — verify against Google's current Gemini pricing page.
    gemini_input_cost_per_1k: float = float(os.getenv("GEMINI_INPUT_COST_PER_1K", "0.00015"))
    gemini_output_cost_per_1k: float = float(os.getenv("GEMINI_OUTPUT_COST_PER_1K", "0.0006"))

    # ── Database ─────────────────────────────────────────────────────────
    # Postgres in production: postgresql+psycopg2://user:pass@host:5432/gtm
    # Falls back to SQLite for local dev/testing (no server required)
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///gtm.db")

    # ── ICP targeting ───────────────────────────────────────────────────
    icp_titles: tuple = ("Founder", "CEO", "Head of Sales", "VP Sales", "Head of Growth")
    icp_industries: tuple = ("SaaS", "Legal Tech", "Document Management")
    icp_company_size_min: int = 5
    icp_company_size_max: int = 200

    # ── Sender identity ──────────────────────────────────────────────────
    sender_name: str = os.getenv("SENDER_NAME", "Your Name")
    sender_company: str = os.getenv("SENDER_COMPANY", "SuperDocs")
    sender_product_pitch: str = os.getenv(
        "SENDER_PRODUCT_PITCH",
        "SuperDocs helps teams generate, review and manage documents with AI.",
    )

    # ── Follow-up scheduling ─────────────────────────────────────────────
    follow_up_delay_days: int = int(os.getenv("FOLLOW_UP_DELAY_DAYS", "3"))
    max_follow_ups: int = int(os.getenv("MAX_FOLLOW_UPS", "2"))

    # ── Mock mode ────────────────────────────────────────────────────────
    # True = no external API calls (safe for demos and tests)
    mock_mode: bool = os.getenv("GTM_MOCK_MODE", "true").lower() == "true"


settings = Settings()
