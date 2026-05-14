from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from .config import KeywordConfig, ScoringConfig
from .db import Listing


SUSPICIOUS_WORDS = [
    "先款",
    "微信",
    "vx",
    "vx:",
    "私聊",
    "线下",
    "代卖",
    "引流",
    "转转同步",
    "急出",
    "仅支持面交",
]

FEATURE_PATTERNS = [
    re.compile(r"\b(pro max|promax|pro|plus|ultra|oled|lite|mini)\b", re.IGNORECASE),
    re.compile(r"\b(\d{2,4}g(?:b)?)\b", re.IGNORECASE),
    re.compile(r"\b(\d(?:\.\d)?t(?:b)?)\b", re.IGNORECASE),
]


@dataclass(slots=True)
class ScoredListing:
    listing: Listing
    should_alert: bool
    reasons: list[str]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_relative_time(text: str, timezone_name: str) -> datetime | None:
    if not text:
        return None
    now = datetime.now(ZoneInfo(timezone_name))
    raw = clean_text(text)
    if "刚刚" in raw:
        return now
    match = re.search(r"(\d+)\s*分钟", raw)
    if match:
        return now - timedelta(minutes=int(match.group(1)))
    match = re.search(r"(\d+)\s*小时", raw)
    if match:
        return now - timedelta(hours=int(match.group(1)))
    match = re.search(r"(\d+)\s*天", raw)
    if match:
        return now - timedelta(days=int(match.group(1)))
    if "昨天" in raw:
        return now - timedelta(days=1)
    return None


def derive_model_key(title: str, keyword: KeywordConfig) -> str:
    parts = [keyword.name]
    normalized = clean_text(title)
    for pattern in FEATURE_PATTERNS:
        match = pattern.search(normalized)
        if match:
            parts.append(match.group(1).replace(" ", ""))
    return "::".join(parts)


def trimmed_median(prices: list[float]) -> float | None:
    clean_prices = sorted(price for price in prices if price > 0)
    if not clean_prices:
        return None
    if len(clean_prices) <= 4:
        return median(clean_prices)
    trim = max(1, len(clean_prices) // 10)
    sliced = clean_prices[trim:-trim] or clean_prices
    return median(sliced)


def estimate_market_price(
    listing: Listing,
    batch_prices: list[float],
    historical_prices: list[float],
    min_market_samples: int,
) -> float | None:
    candidates = [price for price in historical_prices if price > 0]
    if len(candidates) < min_market_samples:
        candidates.extend(price for price in batch_prices if price > 0 and price != listing.price)
    if len(candidates) < max(3, min_market_samples):
        return None
    return trimmed_median(candidates)


def freshness_score(listing: Listing, settings: ScoringConfig, timezone_name: str) -> float:
    published_at = listing.published_at or parse_relative_time(listing.published_text, timezone_name)
    if not published_at:
        return 0.45
    now = datetime.now(ZoneInfo(timezone_name))
    age_hours = max((now - published_at).total_seconds() / 3600.0, 0.0)
    if age_hours <= settings.full_freshness_hours:
        return 1.0
    if age_hours >= settings.stale_after_hours:
        return 0.0
    span = settings.stale_after_hours - settings.full_freshness_hours
    return max(0.0, 1.0 - (age_hours - settings.full_freshness_hours) / span)


def seller_score(listing: Listing) -> float:
    if listing.seller_rating is None:
        return 0.45 if listing.seller_name else 0.35
    rating = max(min(listing.seller_rating, 5.0), 0.0)
    return 0.3 + 0.7 * (rating / 5.0)


def info_quality_score(listing: Listing) -> float:
    score = 0.25
    if len(listing.title) >= 12:
        score += 0.20
    if len(listing.raw_text) >= 40:
        score += 0.20
    if listing.images_count >= 3:
        score += 0.20
    if listing.description:
        score += 0.15
    if listing.location:
        score += 0.10
    return min(score, 1.0)


def risk_score(listing: Listing, market_price: float | None, settings: ScoringConfig) -> tuple[float, list[str]]:
    text = clean_text(" ".join([listing.title, listing.description, listing.raw_text]))
    risk = 0.0
    reasons: list[str] = []

    for word in SUSPICIOUS_WORDS:
        if word in text:
            risk += 0.12
            reasons.append(f"命中风险词: {word}")

    if len(listing.title.strip()) < 8:
        risk += 0.10
        reasons.append("标题信息偏少")

    if listing.images_count == 0:
        risk += 0.10
        reasons.append("无图片计数")

    if market_price and listing.price < market_price * settings.suspicious_price_ratio:
        risk += 0.25
        reasons.append("价格显著低于参考价")

    return min(risk, 1.0), reasons


def score_listing(
    listing: Listing,
    keyword: KeywordConfig,
    batch_prices: list[float],
    historical_prices: list[float],
    settings: ScoringConfig,
    timezone_name: str,
) -> ScoredListing:
    listing.model_key = derive_model_key(listing.title, keyword)
    market_price = estimate_market_price(
        listing=listing,
        batch_prices=batch_prices,
        historical_prices=historical_prices,
        min_market_samples=settings.min_market_samples,
    )
    listing.market_price = market_price

    deal_ratio = 0.0
    reasons: list[str] = []
    if market_price and market_price > 0:
        deal_ratio = max(min((market_price - listing.price) / market_price, 1.0), -1.0)
        reasons.append(f"参考价 {market_price:.0f}，当前 {listing.price:.0f}，折价 {deal_ratio:.1%}")
    else:
        reasons.append("历史样本不足，暂未估出参考价")

    fresh = freshness_score(listing, settings, timezone_name)
    seller = seller_score(listing)
    info = info_quality_score(listing)
    risk, risk_reasons = risk_score(listing, market_price, settings)

    listing.deal_ratio = deal_ratio
    listing.risk_score = risk
    listing.final_score = max(
        0.0,
        min(
            1.0,
            0.55 * max(deal_ratio, 0.0)
            + 0.15 * fresh
            + 0.15 * seller
            + 0.15 * info
            - risk,
        ),
    )

    reasons.extend(risk_reasons)
    threshold = keyword.min_score if keyword.min_score is not None else settings.alert_threshold
    should_alert = listing.final_score >= threshold and risk <= settings.max_risk_score
    return ScoredListing(listing=listing, should_alert=should_alert, reasons=reasons)
