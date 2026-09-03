from __future__ import annotations

import html
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .models import ProductRecord
from .monitor_models import (
    DeliveryResult,
    FeishuConfig,
    NotificationBatch,
    WxPusherConfig,
)


FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MESSAGE_URL = (
    "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
)
WXPUSHER_SIMPLE_URL = "https://wxpusher.zjiecode.com/api/send/message/simple-push"


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    text: str

    def json(self) -> dict:
        return json.loads(self.text or "{}")


class JsonTransport(Protocol):
    def post_json(
        self,
        url: str,
        data: dict,
        headers: dict | None = None,
        timeout: float = 15,
    ) -> HttpResponse: ...


class UrllibTransport:
    def post_json(
        self,
        url: str,
        data: dict,
        headers: dict | None = None,
        timeout: float = 15,
    ) -> HttpResponse:
        request = urllib.request.Request(
            url,
            data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(response.status, response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            return HttpResponse(exc.code, exc.read().decode("utf-8", "replace"))


class NotificationProvider(Protocol):
    provider_id: str
    capabilities: frozenset[str]

    def validate_config(self) -> None: ...

    def send_test(self) -> DeliveryResult: ...

    def send_batch(self, batch: NotificationBatch) -> DeliveryResult: ...


def _price_text(record: ProductRecord) -> str:
    if record.price is None:
        return "价格未显示"
    return f"¥{record.price:,.2f}".rstrip("0").rstrip(".")


def _remaining_text(batch: NotificationBatch) -> str:
    remaining = max(0, batch.total_count - len(batch.items))
    return f"另有剩余 {remaining} 件{batch.item_label}已保存到本机。" if remaining else ""


class WxPusherProvider:
    provider_id = "wxpusher"
    capabilities = frozenset(("html", "multi_link"))

    def __init__(self, config: WxPusherConfig, *, transport: JsonTransport | None = None) -> None:
        self.config = config
        self.transport = transport or UrllibTransport()

    def validate_config(self) -> None:
        if not self.config.spt.strip().startswith("SPT_"):
            raise ValueError("WxPusher 极简推送令牌必须以 SPT_ 开头")

    def _payload(self, batch: NotificationBatch) -> dict:
        rows = [
            f"<h3>{html.escape(batch.task_name)}：{batch.total_count} 件"
            f"{html.escape(batch.item_label)}</h3>"
        ]
        for index, item in enumerate(batch.items, 1):
            details = " · ".join(part for part in (_price_text(item), item.region) if part)
            rows.append(
                f"<p><b>{index}. {html.escape(item.title or '未命名商品')}</b><br>"
                f"{html.escape(details)}<br>"
                f'<a href="{html.escape(item.chat_url or item.url, quote=True)}">直接聊天</a>　'
                f'<a href="{html.escape(item.url, quote=True)}">查看商品</a></p>'
            )
        remaining = _remaining_text(batch)
        if remaining:
            rows.append(f"<p>{html.escape(remaining)}</p>")
        first_url = batch.items[0].url if batch.items else "https://www.goofish.com/"
        return {
            "spt": self.config.spt.strip(),
            "summary": f"{batch.task_name}：{batch.total_count} 件{batch.item_label}",
            "content": "".join(rows),
            "contentType": 2,
            "url": first_url,
        }

    def send_batch(self, batch: NotificationBatch) -> DeliveryResult:
        try:
            self.validate_config()
            response = self.transport.post_json(WXPUSHER_SIMPLE_URL, self._payload(batch))
            body = response.json()
            success = response.status_code == 200 and body.get("code") in (0, 1000)
            message = str(body.get("msg") or body.get("message") or response.text)
            return DeliveryResult(
                self.provider_id,
                success,
                message,
                response.status_code,
                retryable=response.status_code >= 500 or response.status_code in (408, 429),
            )
        except Exception as exc:
            return DeliveryResult(self.provider_id, False, str(exc), retryable=True)

    def send_test(self) -> DeliveryResult:
        item = ProductRecord(
            keyword="测试",
            item_id="test",
            title="通知通道连接成功",
            url="https://www.goofish.com/",
        )
        return self.send_batch(
            NotificationBatch(
                task_id="test",
                task_name="闲鱼新品监控测试",
                provider_id=self.provider_id,
                items=[item],
                total_count=1,
            )
        )


class FeishuProvider:
    provider_id = "feishu"
    capabilities = frozenset(("button_card", "multi_link"))

    def __init__(self, config: FeishuConfig, *, transport: JsonTransport | None = None) -> None:
        self.config = config
        self.transport = transport or UrllibTransport()
        self._token = ""
        self._token_expires_at = 0.0

    def validate_config(self) -> None:
        if not self.config.app_id.strip() or not self.config.app_secret.strip():
            raise ValueError("请填写飞书 App ID 和 App Secret")
        if not self.config.open_id.strip():
            raise ValueError("尚未绑定飞书接收用户，请先完成“绑定”")

    def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        response = self.transport.post_json(
            FEISHU_TOKEN_URL,
            {"app_id": self.config.app_id.strip(), "app_secret": self.config.app_secret.strip()},
        )
        body = response.json()
        if response.status_code != 200 or body.get("code") != 0:
            raise RuntimeError(str(body.get("msg") or "获取飞书 tenant_access_token 失败"))
        token = str(body.get("tenant_access_token") or "")
        if not token:
            raise RuntimeError("飞书响应中没有 tenant_access_token")
        expires = max(60, int(body.get("expire", 7200)) - 120)
        self._token = token
        self._token_expires_at = time.monotonic() + expires
        return token

    @staticmethod
    def build_card(batch: NotificationBatch) -> dict:
        elements: list[dict] = []
        for item in batch.items:
            details = " · ".join(part for part in (_price_text(item), item.region) if part)
            elements.append(
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{item.title or '未命名商品'}**\n{details}",
                    },
                }
            )
            actions = []
            if item.chat_url:
                actions.append(
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "直接聊天"},
                        "url": item.chat_url,
                        "type": "primary",
                    }
                )
            actions.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看商品"},
                    "url": item.url,
                }
            )
            elements.append({"tag": "action", "actions": actions})
            elements.append({"tag": "hr"})
        remaining = _remaining_text(batch)
        if remaining:
            elements.append(
                {"tag": "note", "elements": [{"tag": "plain_text", "content": remaining}]}
            )
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "yellow",
                "title": {
                    "tag": "plain_text",
                    "content": f"{batch.task_name} · {batch.total_count} 件{batch.item_label}",
                },
            },
            "elements": elements,
        }

    def send_batch(self, batch: NotificationBatch) -> DeliveryResult:
        try:
            self.validate_config()
            token = self._access_token()
            response = self.transport.post_json(
                FEISHU_MESSAGE_URL,
                {
                    "receive_id": self.config.open_id.strip(),
                    "msg_type": "interactive",
                    "content": json.dumps(self.build_card(batch), ensure_ascii=False),
                },
                {"Authorization": f"Bearer {token}"},
            )
            body = response.json()
            success = response.status_code == 200 and body.get("code") == 0
            message = str(body.get("msg") or body.get("message") or response.text)
            return DeliveryResult(
                self.provider_id,
                success,
                message,
                response.status_code,
                retryable=response.status_code >= 500 or response.status_code in (408, 429),
            )
        except Exception as exc:
            return DeliveryResult(self.provider_id, False, str(exc), retryable=True)

    def send_text(self, open_id: str, text: str) -> DeliveryResult:
        try:
            if not self.config.app_id.strip() or not self.config.app_secret.strip():
                raise ValueError("请填写飞书 App ID 和 App Secret")
            token = self._access_token()
            response = self.transport.post_json(
                FEISHU_MESSAGE_URL,
                {
                    "receive_id": open_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
                {"Authorization": f"Bearer {token}"},
            )
            body = response.json()
            return DeliveryResult(
                self.provider_id,
                response.status_code == 200 and body.get("code") == 0,
                str(body.get("msg") or body.get("message") or response.text),
                response.status_code,
            )
        except Exception as exc:
            return DeliveryResult(self.provider_id, False, str(exc), retryable=True)

    def send_test(self) -> DeliveryResult:
        item = ProductRecord(
            keyword="测试",
            item_id="test",
            title="通知通道连接成功",
            url="https://www.goofish.com/",
        )
        return self.send_batch(
            NotificationBatch(
                task_id="test",
                task_name="闲鱼新品监控测试",
                provider_id=self.provider_id,
                items=[item],
                total_count=1,
            )
        )
