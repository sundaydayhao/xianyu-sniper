from __future__ import annotations

from dataclasses import dataclass

import requests

from .config import NotificationConfig
from .scoring import ScoredListing


@dataclass(slots=True)
class NotificationResult:
    delivered: bool
    message: str


class BaseNotifier:
    def __init__(self, config: NotificationConfig) -> None:
        self.config = config

    def send(self, scored: ScoredListing) -> NotificationResult:
        raise NotImplementedError

    @staticmethod
    def build_message(scored: ScoredListing) -> str:
        listing = scored.listing
        lines = [
            f"[闲鱼捡漏] {listing.keyword_name}",
            f"标题: {listing.title}",
            f"价格: ¥{listing.price:.2f}",
            f"参考价: ¥{listing.market_price:.2f}" if listing.market_price else "参考价: 样本不足",
            f"得分: {listing.final_score:.2f}  风险: {listing.risk_score:.2f}",
            f"地区: {listing.location or '未知'}",
            f"卖家: {listing.seller_name or '未知'}",
            f"发布时间: {listing.published_text or '未知'}",
            f"链接: {listing.url or '无'}",
        ]
        if scored.reasons:
            lines.append("说明: " + " | ".join(scored.reasons[:4]))
        return "\n".join(lines)


class NullNotifier(BaseNotifier):
    def send(self, scored: ScoredListing) -> NotificationResult:
        return NotificationResult(delivered=False, message=self.build_message(scored))


class WeChatWorkNotifier(BaseNotifier):
    def send(self, scored: ScoredListing) -> NotificationResult:
        webhook = self.config.wechat_work.webhook.strip()
        if not webhook:
            raise ValueError("notifications.wechat_work.webhook 未配置。")
        message = self.build_message(scored)
        if self.config.dry_run:
            return NotificationResult(delivered=False, message=message)
        response = requests.post(
            webhook,
            timeout=10,
            json={
                "msgtype": "text",
                "text": {
                    "content": message,
                    "mentioned_mobile_list": self.config.wechat_work.mentioned_mobile_list,
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errcode", 0) != 0:
            raise RuntimeError(f"企业微信机器人发送失败: {payload}")
        return NotificationResult(delivered=True, message=message)


class ServerChanNotifier(BaseNotifier):
    def send(self, scored: ScoredListing) -> NotificationResult:
        sendkey = self.config.serverchan.sendkey.strip()
        if not sendkey:
            raise ValueError("notifications.serverchan.sendkey 未配置。")
        message = self.build_message(scored)
        if self.config.dry_run:
            return NotificationResult(delivered=False, message=message)
        response = requests.post(
            f"https://sctapi.ftqq.com/{sendkey}.send",
            timeout=10,
            data={
                "title": f"闲鱼捡漏: {scored.listing.keyword_name}",
                "desp": message.replace("\n", "\n\n"),
            },
        )
        response.raise_for_status()
        return NotificationResult(delivered=True, message=message)


class PushPlusNotifier(BaseNotifier):
    def send(self, scored: ScoredListing) -> NotificationResult:
        token = self.config.pushplus.token.strip()
        if not token:
            raise ValueError("notifications.pushplus.token 未配置。")
        message = self.build_message(scored)
        if self.config.dry_run:
            return NotificationResult(delivered=False, message=message)
        response = requests.post(
            "https://www.pushplus.plus/send",
            timeout=10,
            json={
                "token": token,
                "title": f"闲鱼捡漏: {scored.listing.keyword_name}",
                "content": message.replace("\n", "<br/>"),
                "template": "html",
                "topic": self.config.pushplus.topic or None,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 200:
            raise RuntimeError(f"PushPlus 发送失败: {payload}")
        return NotificationResult(delivered=True, message=message)


def create_notifier(config: NotificationConfig) -> BaseNotifier:
    provider = config.provider.strip().lower()
    if provider in {"none", ""}:
        return NullNotifier(config)
    if provider == "wechat_work":
        return WeChatWorkNotifier(config)
    if provider == "serverchan":
        return ServerChanNotifier(config)
    if provider == "pushplus":
        return PushPlusNotifier(config)
    raise ValueError(f"未知通知方式: {config.provider}")
