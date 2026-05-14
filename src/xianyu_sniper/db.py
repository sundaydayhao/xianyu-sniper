from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha1
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class Listing:
    keyword_name: str
    query: str
    title: str
    price: float
    url: str = ""
    item_id: str = ""
    description: str = ""
    raw_text: str = ""
    location: str = ""
    seller_name: str = ""
    seller_rating: float | None = None
    published_text: str = ""
    published_at: datetime | None = None
    shipping_price: float | None = None
    images_count: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    scraped_at: datetime = field(default_factory=datetime.now)
    source_uid: str = ""
    model_key: str = ""
    market_price: float | None = None
    deal_ratio: float | None = None
    risk_score: float | None = None
    final_score: float | None = None

    def ensure_source_uid(self) -> str:
        if self.source_uid:
            return self.source_uid
        raw = self.item_id or self.url or f"{self.keyword_name}|{self.title}|{self.price}"
        self.source_uid = sha1(raw.encode("utf-8")).hexdigest()
        return self.source_uid


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def init(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_uid TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL DEFAULT 'goofish',
                keyword_name TEXT NOT NULL,
                query TEXT NOT NULL,
                item_id TEXT,
                url TEXT,
                title TEXT NOT NULL,
                description TEXT,
                raw_text TEXT,
                price REAL NOT NULL,
                shipping_price REAL,
                location TEXT,
                seller_name TEXT,
                seller_rating REAL,
                published_text TEXT,
                published_at TEXT,
                images_count INTEGER NOT NULL DEFAULT 0,
                model_key TEXT,
                market_price REAL,
                deal_ratio REAL,
                risk_score REAL,
                final_score REAL,
                payload_json TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                notified_at TEXT
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_listings_keyword_model_seen
            ON listings(keyword_name, model_key, last_seen_at DESC)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_listings_notify
            ON listings(final_score DESC, risk_score ASC, notified_at)
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def upsert_listing(self, listing: Listing) -> None:
        now = listing.scraped_at.isoformat(timespec="seconds")
        published_at = listing.published_at.isoformat(timespec="seconds") if listing.published_at else None
        self.conn.execute(
            """
            INSERT INTO listings (
                source_uid, keyword_name, query, item_id, url, title, description, raw_text,
                price, shipping_price, location, seller_name, seller_rating, published_text,
                published_at, images_count, model_key, market_price, deal_ratio, risk_score,
                final_score, payload_json, first_seen_at, last_seen_at
            ) VALUES (
                :source_uid, :keyword_name, :query, :item_id, :url, :title, :description, :raw_text,
                :price, :shipping_price, :location, :seller_name, :seller_rating, :published_text,
                :published_at, :images_count, :model_key, :market_price, :deal_ratio, :risk_score,
                :final_score, :payload_json, :first_seen_at, :last_seen_at
            )
            ON CONFLICT(source_uid) DO UPDATE SET
                keyword_name = excluded.keyword_name,
                query = excluded.query,
                item_id = excluded.item_id,
                url = excluded.url,
                title = excluded.title,
                description = excluded.description,
                raw_text = excluded.raw_text,
                price = excluded.price,
                shipping_price = excluded.shipping_price,
                location = excluded.location,
                seller_name = excluded.seller_name,
                seller_rating = excluded.seller_rating,
                published_text = excluded.published_text,
                published_at = excluded.published_at,
                images_count = excluded.images_count,
                model_key = excluded.model_key,
                market_price = excluded.market_price,
                deal_ratio = excluded.deal_ratio,
                risk_score = excluded.risk_score,
                final_score = excluded.final_score,
                payload_json = excluded.payload_json,
                last_seen_at = excluded.last_seen_at
            """,
            {
                "source_uid": listing.ensure_source_uid(),
                "keyword_name": listing.keyword_name,
                "query": listing.query,
                "item_id": listing.item_id,
                "url": listing.url,
                "title": listing.title,
                "description": listing.description,
                "raw_text": listing.raw_text,
                "price": listing.price,
                "shipping_price": listing.shipping_price,
                "location": listing.location,
                "seller_name": listing.seller_name,
                "seller_rating": listing.seller_rating,
                "published_text": listing.published_text,
                "published_at": published_at,
                "images_count": listing.images_count,
                "model_key": listing.model_key,
                "market_price": listing.market_price,
                "deal_ratio": listing.deal_ratio,
                "risk_score": listing.risk_score,
                "final_score": listing.final_score,
                "payload_json": json.dumps(listing.payload, ensure_ascii=False),
                "first_seen_at": now,
                "last_seen_at": now,
            },
        )
        self.conn.commit()

    def get_recent_prices(
        self,
        keyword_name: str,
        model_key: str,
        lookback_days: int,
        limit: int = 80,
    ) -> list[float]:
        cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            """
            SELECT price
            FROM listings
            WHERE keyword_name = ?
              AND model_key = ?
              AND last_seen_at >= ?
              AND price > 0
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (keyword_name, model_key, cutoff, limit),
        ).fetchall()
        return [float(row["price"]) for row in rows]

    def was_notified_recently(self, source_uid: str, cooldown_hours: int) -> bool:
        row = self.conn.execute(
            """
            SELECT notified_at
            FROM listings
            WHERE source_uid = ?
            """,
            (source_uid,),
        ).fetchone()
        if not row or not row["notified_at"]:
            return False
        notified_at = datetime.fromisoformat(row["notified_at"])
        return datetime.now() - notified_at < timedelta(hours=cooldown_hours)

    def mark_notified(self, source_uid: str) -> None:
        self.conn.execute(
            """
            UPDATE listings
            SET notified_at = ?
            WHERE source_uid = ?
            """,
            (datetime.now().isoformat(timespec="seconds"), source_uid),
        )
        self.conn.commit()

    def get_top_matches(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT *
            FROM listings
            ORDER BY final_score DESC, deal_ratio DESC, last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
