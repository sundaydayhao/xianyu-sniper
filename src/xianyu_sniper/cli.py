from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .config import Settings, load_settings
from .db import Database, Listing
from .notifier import create_notifier
from .scoring import derive_model_key, score_listing
from .scraper import XianyuScraper


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="闲鱼捡漏本地原型")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--config", default="config.yaml", help="配置文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", parents=[shared], help="初始化 SQLite 数据库")

    login = subparsers.add_parser("login", parents=[shared], help="打开浏览器并保存登录态")
    login.add_argument("--headful", action="store_true", help="显式使用有头模式")

    run_once = subparsers.add_parser("run-once", parents=[shared], help="执行一次抓取和评分")
    run_once.add_argument("--print-matches", action="store_true", help="打印高分商品")

    watch = subparsers.add_parser("watch", parents=[shared], help="循环抓取")
    watch.add_argument("--interval-seconds", type=int, default=300, help="轮询间隔")
    watch.add_argument("--print-matches", action="store_true", help="打印高分商品")
    return parser


def init_db(settings: Settings) -> Database:
    db = Database(settings.database_path)
    db.init()
    return db


def print_match(listing: Listing) -> None:
    print(
        " | ".join(
            [
                listing.keyword_name,
                f"score={listing.final_score:.2f}" if listing.final_score is not None else "score=n/a",
                f"risk={listing.risk_score:.2f}" if listing.risk_score is not None else "risk=n/a",
                f"price={listing.price:.0f}",
                listing.title[:60],
                listing.url or "no-url",
            ]
        )
    )


def run_cycle(settings: Settings, print_matches: bool = False) -> int:
    logger = logging.getLogger(__name__)
    db = init_db(settings)
    notifier = create_notifier(settings.notifications)
    scraper = XianyuScraper(settings.scraper, str(settings.browser_profile_dir))
    matches = 0

    try:
        for keyword in settings.keywords:
            logger.info("开始抓取关键词: %s", keyword.query)
            listings = scraper.fetch_keyword(keyword)
            if not listings:
                logger.warning("关键词无结果: %s", keyword.query)
                continue

            groups: dict[str, list[float]] = {}
            for listing in listings:
                listing.model_key = derive_model_key(listing.title, keyword)
                groups.setdefault(listing.model_key, []).append(listing.price)

            for listing in listings:
                scored = score_listing(
                    listing=listing,
                    keyword=keyword,
                    batch_prices=groups.get(listing.model_key, []),
                    historical_prices=db.get_recent_prices(
                        keyword_name=keyword.name,
                        model_key=listing.model_key,
                        lookback_days=settings.scoring.lookback_days,
                    ),
                    settings=settings.scoring,
                    timezone_name=settings.app.timezone,
                )
                db.upsert_listing(scored.listing)
                if scored.should_alert and not db.was_notified_recently(
                    scored.listing.ensure_source_uid(),
                    settings.scoring.notification_cooldown_hours,
                ):
                    result = notifier.send(scored)
                    if print_matches or not result.delivered:
                        print(result.message)
                        print("-" * 80)
                    db.mark_notified(scored.listing.ensure_source_uid())
                    matches += 1
                elif print_matches and scored.should_alert:
                    print_match(scored.listing)
    finally:
        db.close()

    return matches


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config).resolve()
    settings = load_settings(config_path)
    settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(settings.app.log_level)

    if args.command == "init-db":
        db = init_db(settings)
        db.close()
        print(f"数据库已初始化: {settings.database_path}")
        return 0

    if args.command == "login":
        scraper = XianyuScraper(settings.scraper, str(settings.browser_profile_dir))
        scraper.login(headful=True)
        print(f"登录态已写入: {settings.browser_profile_dir}")
        return 0

    if args.command == "run-once":
        matches = run_cycle(settings, print_matches=args.print_matches)
        print(f"本轮完成，命中 {matches} 条。")
        return 0

    if args.command == "watch":
        interval = max(int(args.interval_seconds), 60)
        while True:
            matches = run_cycle(settings, print_matches=args.print_matches)
            print(f"本轮完成，命中 {matches} 条。下次运行将在 {interval} 秒后。")
            time.sleep(interval)

    parser.error(f"未知命令: {args.command}")
    return 2
