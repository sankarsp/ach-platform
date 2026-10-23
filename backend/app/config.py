from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACH_", env_file=".env", extra="ignore")

    env: str = "sandbox"                      # sandbox | production
    database_url: str = "postgresql+psycopg://ach:ach@localhost:5432/ach"
    # Fernet key for account-number vault; generate with `python -m app.cli gen-key`
    vault_key: str = "q0dS2Z2wQ6pq7ZQd9qJ0b1QH9rWm3yJ8Rk4xT6uV2sA="

    # Institution identity (ODFI / RDFI)
    bank_name: str = "Demo Community Bank"
    bank_routing: str = "021000021"
    fed_routing: str = "011000015"            # immediate destination (FedACH)
    fed_name: str = "FEDERAL RESERVE BANK"
    rdfi_timezone: str = "America/New_York"

    # Risk tuning
    fraud_hold_score: int = 60
    fraud_block_score: int = 90
    ofac_block_ratio: float = 0.92
    ofac_review_ratio: float = 0.85
    large_new_payee_cents: int = 10_000_00
    structuring_threshold_cents: int = 10_000_00
    high_risk_countries: str = "IR,KP,SY,CU,RU,BY,MM"

    # Operations
    scheduler_enabled: bool = False
    scheduler_interval_seconds: int = 60
    itsm_webhook_url: str = ""               # ServiceNow / PagerDuty / JSM endpoint
    webhook_max_attempts: int = 6
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    seed_demo_data: bool = True

    @property
    def is_sandbox(self) -> bool:
        return self.env == "sandbox"


@lru_cache
def get_settings() -> Settings:
    return Settings()
