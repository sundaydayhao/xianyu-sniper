from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class KeywordConfig:
    name: str
    query: str
    min_price: float | None = None
    max_price: float | None = None
    min_score: float | None = None
    required_keywords: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AppConfig:
    timezone: str = "Asia/Shanghai"
    database_path: str = "data/xianyu.sqlite3"
    browser_profile_dir: str = "data/browser-profile"
    log_level: str = "INFO"


@dataclass(slots=True)
class ScraperConfig:
    base_url: str = "https://www.goofish.com/search"
    timeout_ms: int = 30000
    wait_after_load_ms: int = 3500
    scroll_wait_ms: int = 1800
    max_scroll_rounds: int = 5
    max_items_per_keyword: int = 40
    headless: bool = True
    block_images: bool = True


@dataclass(slots=True)
class ScoringConfig:
    alert_threshold: float = 0.30
    max_risk_score: float = 0.55
    min_market_samples: int = 4
    lookback_days: int = 30
    suspicious_price_ratio: float = 0.55
    full_freshness_hours: int = 6
    stale_after_hours: int = 72
    notification_cooldown_hours: int = 48


@dataclass(slots=True)
class WeChatWorkConfig:
    webhook: str = ""
    mentioned_mobile_list: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ServerChanConfig:
    sendkey: str = ""


@dataclass(slots=True)
class PushPlusConfig:
    token: str = ""
    topic: str = ""


@dataclass(slots=True)
class NotificationConfig:
    provider: str = "none"
    dry_run: bool = True
    wechat_work: WeChatWorkConfig = field(default_factory=WeChatWorkConfig)
    serverchan: ServerChanConfig = field(default_factory=ServerChanConfig)
    pushplus: PushPlusConfig = field(default_factory=PushPlusConfig)


@dataclass(slots=True)
class Settings:
    project_root: Path
    config_path: Path
    app: AppConfig
    scraper: ScraperConfig
    scoring: ScoringConfig
    notifications: NotificationConfig
    keywords: list[KeywordConfig]

    @property
    def database_path(self) -> Path:
        return (self.project_root / self.app.database_path).resolve()

    @property
    def browser_profile_dir(self) -> Path:
        return (self.project_root / self.app.browser_profile_dir).resolve()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _keyword_from_dict(raw: dict[str, Any]) -> KeywordConfig:
    return KeywordConfig(
        name=str(raw["name"]),
        query=str(raw["query"]),
        min_price=float(raw["min_price"]) if raw.get("min_price") is not None else None,
        max_price=float(raw["max_price"]) if raw.get("max_price") is not None else None,
        min_score=float(raw["min_score"]) if raw.get("min_score") is not None else None,
        required_keywords=[str(item).strip() for item in raw.get("required_keywords", [])],
        excluded_keywords=[str(item).strip() for item in raw.get("excluded_keywords", [])],
    )


def load_settings(config_path: str | Path) -> Settings:
    config_file = Path(config_path).resolve()
    project_root = config_file.parent
    data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}

    app = AppConfig(**_as_dict(data.get("app")))
    scraper = ScraperConfig(**_as_dict(data.get("scraper")))
    scoring = ScoringConfig(**_as_dict(data.get("scoring")))

    notifications_raw = _as_dict(data.get("notifications"))
    notifications = NotificationConfig(
        provider=str(notifications_raw.get("provider", "none")),
        dry_run=bool(notifications_raw.get("dry_run", True)),
        wechat_work=WeChatWorkConfig(**_as_dict(notifications_raw.get("wechat_work"))),
        serverchan=ServerChanConfig(**_as_dict(notifications_raw.get("serverchan"))),
        pushplus=PushPlusConfig(**_as_dict(notifications_raw.get("pushplus"))),
    )

    keywords = [_keyword_from_dict(item) for item in data.get("keywords", [])]
    if not keywords:
        raise ValueError("config 里至少要配置一个 keywords 项。")

    return Settings(
        project_root=project_root,
        config_path=config_file,
        app=app,
        scraper=scraper,
        scoring=scoring,
        notifications=notifications,
        keywords=keywords,
    )
