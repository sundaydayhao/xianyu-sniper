from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

from .config import KeywordConfig, ScraperConfig
from .db import Listing


logger = logging.getLogger(__name__)


PRICE_PATTERN = re.compile(r"(?:¥|￥)\s*([0-9]+(?:\.[0-9]+)?)")
TIME_PATTERN = re.compile(r"(刚刚|\d+\s*分钟前|\d+\s*小时(?:前)?|昨天|\d+\s*天前)")
ID_PATTERN = re.compile(r"(?:id=|itemId=|\/item\/)(\d{6,})", re.IGNORECASE)

TITLE_KEYS = ["title", "itemTitle", "subject", "name", "mainTitle"]
PRICE_KEYS = ["price", "currentPrice", "showPrice", "itemPrice", "soldPrice"]
URL_KEYS = ["itemUrl", "url", "shareUrl", "detailUrl", "targetUrl"]
ID_KEYS = ["itemId", "item_id", "id"]
DESC_KEYS = ["desc", "description", "subTitle", "content"]
SELLER_KEYS = ["sellerNick", "sellerName", "nick", "userNick", "nickname"]
LOCATION_KEYS = ["city", "location", "area", "from"]
TIME_KEYS = ["publishTimeText", "publishTime", "gmtCreate", "time", "postTime"]
RATING_KEYS = ["sellerCreditScore", "creditScore", "sellerLevel", "userRate"]


def _first_str(payload: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            candidate = value.strip()
            if candidate:
                return candidate
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _first_float(payload: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        value = payload.get(key)
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = PRICE_PATTERN.search(value)
        if match:
            return float(match.group(1))
        simple = re.search(r"\d+(?:\.\d+)?", value.replace(",", ""))
        if simple:
            return float(simple.group(0))
    return None


def _normalize_url(raw: str) -> str:
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/"):
        return "https://www.goofish.com" + raw
    return raw


def _listing_from_dict(keyword: KeywordConfig, payload: dict[str, Any]) -> Listing | None:
    title = _first_str(payload, TITLE_KEYS)
    price = _first_float(payload, PRICE_KEYS)
    if not title or price is None:
        return None
    url = _normalize_url(_first_str(payload, URL_KEYS))
    item_id = _first_str(payload, ID_KEYS)
    if not item_id and url:
        match = ID_PATTERN.search(url)
        if match:
            item_id = match.group(1)
    published_text = _first_str(payload, TIME_KEYS)
    published_at = None
    if published_text and published_text.isdigit() and len(published_text) >= 10:
        timestamp = int(published_text[:10])
        published_at = datetime.fromtimestamp(timestamp)
    return Listing(
        keyword_name=keyword.name,
        query=keyword.query,
        title=title,
        price=price,
        url=url,
        item_id=item_id,
        description=_first_str(payload, DESC_KEYS),
        raw_text=json.dumps(payload, ensure_ascii=False),
        location=_first_str(payload, LOCATION_KEYS),
        seller_name=_first_str(payload, SELLER_KEYS),
        seller_rating=_first_float(payload, RATING_KEYS),
        published_text=published_text if not published_text.isdigit() else "",
        published_at=published_at,
        images_count=len(payload.get("images", []) or payload.get("picUrls", []) or []),
        payload=payload,
    )


def _walk_json(payload: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 7:
        return []
    results: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if any(key in payload for key in TITLE_KEYS) and any(key in payload for key in PRICE_KEYS):
            results.append(payload)
        for value in payload.values():
            results.extend(_walk_json(value, depth + 1))
    elif isinstance(payload, list):
        for item in payload:
            results.extend(_walk_json(item, depth + 1))
    return results


class XianyuScraper:
    def __init__(self, config: ScraperConfig, browser_profile_dir: str) -> None:
        self.config = config
        self.browser_profile_dir = browser_profile_dir

    def login(self, headful: bool = True) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=self.browser_profile_dir,
                headless=not headful,
                viewport={"width": 1440, "height": 1200},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.new_page()
            page.goto("https://www.goofish.com/", wait_until="domcontentloaded")
            print("浏览器已打开，请完成闲鱼扫码登录。完成后回到终端按回车保存登录态。")
            input()
            context.close()

    def fetch_keyword(self, keyword: KeywordConfig) -> list[Listing]:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        results: list[Listing] = []
        seen: set[str] = set()
        captured_payloads: list[dict[str, Any]] = []
        search_url = f"{self.config.base_url}?q={quote_plus(keyword.query)}"

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=self.browser_profile_dir,
                headless=self.config.headless,
                viewport={"width": 1440, "height": 1200},
                args=["--disable-blink-features=AutomationControlled"],
            )

            if self.config.block_images:
                context.route(
                    "**/*",
                    lambda route: route.abort()
                    if route.request.resource_type in {"image", "media", "font"}
                    else route.continue_(),
                )

            page = context.new_page()
            page.on("response", lambda response: self._collect_payload(response, captured_payloads))

            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                page.wait_for_timeout(self.config.wait_after_load_ms)
                for _ in range(self.config.max_scroll_rounds):
                    page.mouse.wheel(0, 20000)
                    page.wait_for_timeout(self.config.scroll_wait_ms)
            except PlaywrightTimeoutError:
                logger.warning("搜索页超时: %s", keyword.query)

            api_results = self._parse_api_payloads(keyword, captured_payloads)
            dom_results = self._extract_dom_results(page, keyword)
            context.close()

        for listing in api_results + dom_results:
            key = listing.item_id or listing.url or f"{listing.title}|{listing.price}"
            if key in seen:
                continue
            if not self._matches_keyword_rules(listing, keyword):
                continue
            seen.add(key)
            results.append(listing)
            if len(results) >= self.config.max_items_per_keyword:
                break
        return results

    def _collect_payload(self, response: Any, captured_payloads: list[dict[str, Any]]) -> None:
        try:
            content_type = response.headers.get("content-type", "")
        except Exception:
            return
        if "json" not in content_type.lower():
            return
        url = response.url.lower()
        if not any(token in url for token in ("search", "item", "list", "mtop", "feed")):
            return
        try:
            payload = response.json()
        except Exception:
            return
        if isinstance(payload, dict):
            captured_payloads.append(payload)

    def _parse_api_payloads(
        self,
        keyword: KeywordConfig,
        captured_payloads: list[dict[str, Any]],
    ) -> list[Listing]:
        results: list[Listing] = []
        for payload in captured_payloads:
            for candidate in _walk_json(payload):
                listing = _listing_from_dict(keyword, candidate)
                if listing:
                    results.append(listing)
        return results

    def _extract_dom_results(self, page: Any, keyword: KeywordConfig) -> list[Listing]:
        payload = page.evaluate(
            """
            () => {
              const toAbs = (href) => {
                try {
                  return new URL(href, location.origin).toString();
                } catch {
                  return href || "";
                }
              };
              const priceRegex = /(¥|￥)\\s*([0-9]+(?:\\.[0-9]+)?)/;
              const timeRegex = /(刚刚|\\d+\\s*分钟前|\\d+\\s*小时(?:前)?|昨天|\\d+\\s*天前)/;
              const seen = new Map();
              const links = Array.from(document.querySelectorAll("a[href]"));

              for (const link of links) {
                const href = link.getAttribute("href") || "";
                if (!href) {
                  continue;
                }
                if (!/item|detail|goods/i.test(href)) {
                  continue;
                }
                let card = link;
                for (let i = 0; i < 4 && card.parentElement; i += 1) {
                  card = card.parentElement;
                }
                const rawText = (card.innerText || link.innerText || "").replace(/\\s+/g, " ").trim();
                if (!rawText) {
                  continue;
                }
                const priceMatch = rawText.match(priceRegex);
                if (!priceMatch) {
                  continue;
                }
                const title = (
                  link.getAttribute("title") ||
                  link.innerText ||
                  rawText.split(/[¥￥]/)[0] ||
                  ""
                ).replace(/\\s+/g, " ").trim();
                if (!title || title.length < 4) {
                  continue;
                }
                const key = toAbs(href) || `${title}|${priceMatch[2]}`;
                if (seen.has(key)) {
                  continue;
                }
                const timeMatch = rawText.match(timeRegex);
                seen.set(key, {
                  title,
                  price: Number(priceMatch[2]),
                  url: toAbs(href),
                  raw_text: rawText,
                  published_text: timeMatch ? timeMatch[0] : "",
                  images_count: card.querySelectorAll("img").length,
                });
              }
              return Array.from(seen.values());
            }
            """
        )
        results: list[Listing] = []
        for item in payload or []:
            url = _normalize_url(str(item.get("url", "")))
            item_id = ""
            match = ID_PATTERN.search(url)
            if match:
                item_id = match.group(1)
            results.append(
                Listing(
                    keyword_name=keyword.name,
                    query=keyword.query,
                    title=str(item["title"]).strip(),
                    price=float(item["price"]),
                    url=url,
                    item_id=item_id,
                    raw_text=str(item.get("raw_text", "")),
                    published_text=str(item.get("published_text", "")),
                    images_count=int(item.get("images_count", 0)),
                    payload=item,
                )
            )
        return results

    def _matches_keyword_rules(self, listing: Listing, keyword: KeywordConfig) -> bool:
        text = f"{listing.title} {listing.raw_text}".lower()
        if keyword.min_price is not None and listing.price < keyword.min_price:
            return False
        if keyword.max_price is not None and listing.price > keyword.max_price:
            return False
        if keyword.required_keywords:
            required = [token.lower() for token in keyword.required_keywords]
            if not all(token in text for token in required):
                return False
        if keyword.excluded_keywords:
            excluded = [token.lower() for token in keyword.excluded_keywords]
            if any(token in text for token in excluded):
                return False
        return True
