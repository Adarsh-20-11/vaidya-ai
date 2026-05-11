"""
config/settings.py
Central configuration for the Vaidya-AI pipeline.
All environment variables and business-logic thresholds live here.
"""

import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class SupabaseConfig:
    url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    key: str = field(default_factory=lambda: os.getenv("SUPABASE_KEY", ""))

    def is_configured(self) -> bool:
        return bool(self.url and self.key)


@dataclass
class TwilioConfig:
    account_sid: str = field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID", ""))
    auth_token: str = field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN", ""))
    whatsapp_from: str = field(default_factory=lambda: os.getenv("TWILIO_WHATSAPP_FROM", ""))
    alert_number: str = field(default_factory=lambda: os.getenv("ALERT_WHATSAPP_NUMBER", ""))

    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.whatsapp_from)


@dataclass
class PipelineConfig:
    # Where Marg exports are dropped (local path or mounted drive)
    export_dir: str = field(default_factory=lambda: os.getenv("MARG_EXPORT_DIR", "./exports"))

    # Local fallback output directory
    output_dir: str = field(default_factory=lambda: os.getenv("OUTPUT_DIR", "./output"))

    # If True, parse and transform but don't write to Supabase
    dry_run: bool = field(default_factory=lambda: os.getenv("DRY_RUN", "false").lower() == "true")

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))


@dataclass
class BusinessRules:
    """
    Thresholds that drive intelligence computation.
    These are intentionally separate from code so they can be tuned
    without touching pipeline logic.
    """

    # Stock health thresholds
    critical_days_remaining: int = 14       # Flag as CRITICAL if stock runs out in <= N days
    watch_days_remaining: int = 30          # Flag as WATCH if <= N days
    dead_stock_days: int = 90               # Flag as dead if no sales in N days

    # Margin thresholds
    critical_margin_pct: float = 3.0        # Margin below this = critical alert
    watch_margin_pct: float = 8.0           # Margin below this = watch

    # Velocity windows (days)
    velocity_short_window: int = 7
    velocity_medium_window: int = 30
    velocity_long_window: int = 90

    # Minimum snapshots required before velocity is considered reliable
    min_snapshots_for_velocity: int = 7

    # Negative stock: flag but don't treat as critical stockout
    flag_negative_stock: bool = True

    # Items with MRP = 0 are excluded from margin calculations
    exclude_zero_mrp: bool = True

    # Placeholder supplier names to treat as "unknown"
    unknown_supplier_values: list = field(default_factory=lambda: [
        "-BLANK-", "BLANK", "", "ZZZZZ Z 100", "0", None
    ])

    # Known cities for parsing Item Day Book bill headers
    # (where city is sometimes concatenated with party name without delimiter)
    # Magadh Wellness operates primarily in Bihar — add more as needed
    known_cities: list = field(default_factory=lambda: [
        "GAYA", "PATNA", "SASARAM", "NAWADA", "AURANGABAD",
        "NALANDA", "BODH GAYA", "JEHANABAD", "ARWAL",
        "GAYA JI", "RAFIGANJ", "SHERGHATI", "TIKARI", "BARH",
    ])


# Singleton instances — import these directly
supabase_config = SupabaseConfig()
twilio_config = TwilioConfig()
pipeline_config = PipelineConfig()
business_rules = BusinessRules()
