"""Runtime configuration, read once from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent


def _load_dotenv(path: Path) -> None:
    """Read KEY=value lines from .env into the environment (real environment variables win)."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if not os.environ.get("KINSHIELD_SKIP_DOTENV"):
    _load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("KINSHIELD_DATA_DIR") or str(ROOT / "data")).resolve())

    # Nebius Token Factory (OpenAI-compatible)
    nebius_api_key: str = field(default_factory=lambda: _env("NEBIUS_API_KEY"))
    nebius_base_url: str = field(
        default_factory=lambda: _env("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1")
    )
    # Model routing. Verify IDs with scripts/check_models.py before relying on them.
    model_fast: str = field(default_factory=lambda: _env("KS_MODEL_FAST", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"))
    model_main: str = field(default_factory=lambda: _env("KS_MODEL_MAIN", "nvidia/nemotron-3-super-120b-a12b"))
    model_deep: str = field(default_factory=lambda: _env("KS_MODEL_DEEP", "nvidia/Nemotron-3-Ultra-550b-a55b"))
    model_vision: str = field(default_factory=lambda: _env("KS_MODEL_VISION", "nvidia/Nemotron-3.5-Super-VL"))

    # Telegram
    telegram_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_bot_username: str = field(default_factory=lambda: _env("TELEGRAM_BOT_USERNAME"))
    telegram_demo_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_DEMO_CHAT_ID"))

    # App behaviour
    public_url: str = field(default_factory=lambda: _env("KINSHIELD_PUBLIC_URL", "http://localhost:8000"))
    secret_key: str = field(default_factory=lambda: _env("KINSHIELD_SECRET", "dev-only-change-me"))
    demo_mode: bool = field(default_factory=lambda: _env("KINSHIELD_DEMO_MODE", "1") == "1")
    workspace_ttl_days: int = field(default_factory=lambda: int(_env("KINSHIELD_WORKSPACE_TTL_DAYS", "7")))
    model_calls_per_hour: int = field(default_factory=lambda: int(_env("KINSHIELD_MODEL_CALLS_PER_HOUR", "60")))
    worker_interval_s: int = field(default_factory=lambda: int(_env("KINSHIELD_WORKER_INTERVAL", "15")))
    allow_vision_fallback: bool = field(default_factory=lambda: _env("KINSHIELD_ALLOW_VISION", "1") == "1")

    @property
    def default_ack_window_minutes(self) -> int:
        return 2 if self.demo_mode else 30


settings = Settings()

# List prices in USD per 1M tokens (input, output). Third-party listing; confirm in the Nebius console.
PRICES: dict[str, tuple[float, float]] = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "nvidia/Nemotron-3.5-Super-VL": (0.0, 0.0),  # price not yet confirmed
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.24),
}


def price_for(model: str) -> tuple[float, float]:
    if model in PRICES:
        return PRICES[model]
    lowered = {k.lower(): v for k, v in PRICES.items()}
    return lowered.get(model.lower(), (0.0, 0.0))
