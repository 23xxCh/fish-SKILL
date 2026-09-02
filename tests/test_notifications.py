import json

import pytest

from goofish_collector.models import ProductRecord
from goofish_collector.monitor_models import FeishuConfig, NotificationBatch, WxPusherConfig
from goofish_collector.notifications import FeishuProvider, HttpResponse, WxPusherProvider


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict, dict]] = []

    def post_json(self, url: str, data: dict, headers: dict | None = None, timeout: float = 15) -> HttpResponse:
        self.calls.append((url, data, headers or {}))
        return self.responses.pop(0)


def _batch(provider_id: str) -> NotificationBatch:
    items = [
        ProductRecord(
            keyword="耳机",
            item_id=str(index),
            title="<全新耳机>" if index == 1 else f"耳机 {index}",
            price=99,
            region="广东",
            url=f"https://www.goofish.com/item?id={index}",
            chat_url=(
                "https://www.goofish.com/im?itemId=1&peerUserId=u1" if index == 1 else ""
            ),
        )
        for index in range(1, 11)
    ]
    return NotificationBatch(
        task_id="t1", task_name="耳机监控", provider_id=provider_id, items=items, total_count=13
    )


def test_wxpusher_builds_html_with_real_links() -> None:
    transport = FakeTransport([HttpResponse(200, '{"code":1000,"msg":"处理成功"}')])
    provider = WxPusherProvider(WxPusherConfig(spt="SPT_token"), transport=transport)

    result = provider.send_batch(_batch("wxpusher"))

    assert result.success
    payload = transport.calls[0][1]
    assert payload["spt"] == "SPT_token"
    assert payload["contentType"] == 2
    assert "直接聊天" in payload["content"]
    assert "剩余 3 件" in payload["content"]
    assert "&lt;全新耳机&gt;" in payload["content"]
    assert payload["url"] == "https://www.goofish.com/item?id=1"


def test_wxpusher_rejects_invalid_spt() -> None:
    with pytest.raises(ValueError, match="SPT_"):
        WxPusherProvider(WxPusherConfig(spt="bad")).validate_config()


def test_feishu_refreshes_token_and_sends_card() -> None:
    transport = FakeTransport(
        [
            HttpResponse(200, '{"code":0,"tenant_access_token":"token-1","expire":7200}'),
            HttpResponse(200, '{"code":0,"msg":"success"}'),
        ]
    )
    provider = FeishuProvider(
        FeishuConfig(app_id="cli_1", app_secret="secret", open_id="ou_1"),
        transport=transport,
    )

    result = provider.send_batch(_batch("feishu"))

    assert result.success
    assert "tenant_access_token/internal" in transport.calls[0][0]
    send_url, payload, headers = transport.calls[1]
    assert "receive_id_type=open_id" in send_url
    assert payload["receive_id"] == "ou_1"
    assert payload["msg_type"] == "interactive"
    card = json.loads(payload["content"])
    assert "直接聊天" in json.dumps(card, ensure_ascii=False)
    assert headers["Authorization"] == "Bearer token-1"
