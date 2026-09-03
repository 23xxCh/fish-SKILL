import json
from io import BytesIO

import pytest
from PIL import Image

from goofish_collector.models import ProductRecord
from goofish_collector.monitor_models import FeishuConfig, NotificationBatch, WxPusherConfig
from goofish_collector.notifications import FeishuProvider, HttpResponse, WxPusherProvider


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict, dict]] = []
        self.multipart_calls: list[tuple[str, dict, str, str, bytes, str, dict]] = []

    def post_json(self, url: str, data: dict, headers: dict | None = None, timeout: float = 15) -> HttpResponse:
        self.calls.append((url, data, headers or {}))
        return self.responses.pop(0)

    def post_multipart(
        self,
        url: str,
        fields: dict[str, str],
        *,
        file_field: str,
        filename: str,
        content: bytes,
        content_type: str,
        headers: dict | None = None,
        timeout: float = 15,
    ) -> HttpResponse:
        self.multipart_calls.append(
            (url, fields, file_field, filename, content, content_type, headers or {})
        )
        return self.responses.pop(0)


def _batch(provider_id: str, *, image_urls: list[str] | None = None) -> NotificationBatch:
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
            image_url=(image_urls[index - 1] if image_urls and index <= len(image_urls) else ""),
        )
        for index in range(1, 11)
    ]
    return NotificationBatch(
        task_id="t1", task_name="耳机监控", provider_id=provider_id, items=items, total_count=13
    )


def _image_bytes(color: str) -> tuple[bytes, str]:
    image = Image.new("RGB", (80, 60), color=color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue(), "image/png"


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


def test_feishu_uploads_product_image_before_card_text_and_buttons() -> None:
    transport = FakeTransport(
        [
            HttpResponse(200, '{"code":0,"tenant_access_token":"token-1","expire":7200}'),
            HttpResponse(200, '{"code":0,"data":{"image_key":"img_key_1"}}'),
            HttpResponse(200, '{"code":0,"msg":"success"}'),
        ]
    )
    provider = FeishuProvider(
        FeishuConfig(app_id="cli_1", app_secret="secret", open_id="ou_1"),
        transport=transport,
        image_fetcher=lambda _: _image_bytes("red"),
    )
    source = _batch("feishu", image_urls=["https://img.goofish.example/item-1.jpg"])
    batch = NotificationBatch(
        task_id=source.task_id,
        task_name=source.task_name,
        provider_id=source.provider_id,
        items=source.items[:1],
        total_count=1,
        item_label="商品",
    )

    result = provider.send_batch(batch)

    assert result.success
    assert "图片已展示 1/1" in result.message
    assert len(transport.multipart_calls) == 1
    upload = transport.multipart_calls[0]
    assert upload[1] == {"image_type": "message"}
    assert upload[2] == "image"
    assert upload[4] == _image_bytes("red")[0]
    send_payload = transport.calls[-1][1]
    card = json.loads(send_payload["content"])
    assert card["elements"][0]["tag"] == "img"
    assert card["elements"][0]["img_key"] == "img_key_1"
    assert card["elements"][1]["tag"] == "div"
    assert card["elements"][2]["tag"] == "action"
    assert card["elements"][2]["actions"][-1]["text"]["content"] == "查看商品"


def test_feishu_falls_back_to_text_card_when_product_image_is_unavailable() -> None:
    transport = FakeTransport(
        [
            HttpResponse(200, '{"code":0,"tenant_access_token":"token-1","expire":7200}'),
            HttpResponse(200, '{"code":0,"msg":"success"}'),
        ]
    )
    provider = FeishuProvider(
        FeishuConfig(app_id="cli_1", app_secret="secret", open_id="ou_1"),
        transport=transport,
        image_fetcher=lambda _: None,
    )
    source = _batch("feishu", image_urls=["https://img.goofish.example/item-1.jpg"])
    batch = NotificationBatch(
        task_id=source.task_id,
        task_name=source.task_name,
        provider_id=source.provider_id,
        items=source.items[:1],
        total_count=1,
        item_label="商品",
    )

    result = provider.send_batch(batch)

    assert result.success
    assert "图片已展示 0/1" in result.message
    assert transport.multipart_calls == []
    card = json.loads(transport.calls[-1][1]["content"])
    assert card["elements"][0]["tag"] == "div"
    assert card["elements"][1]["tag"] == "action"
    assert card["elements"][1]["actions"][-1]["url"] == "https://www.goofish.com/item?id=1"


def test_feishu_skips_a_blank_video_placeholder_image() -> None:
    transport = FakeTransport(
        [
            HttpResponse(200, '{"code":0,"tenant_access_token":"token-1","expire":7200}'),
            HttpResponse(200, '{"code":0,"msg":"success"}'),
        ]
    )
    provider = FeishuProvider(
        FeishuConfig(app_id="cli_1", app_secret="secret", open_id="ou_1"),
        transport=transport,
        image_fetcher=lambda _: _image_bytes("white"),
    )
    source = _batch("feishu", image_urls=["https://img.goofish.example/video-placeholder.jpg"])
    batch = NotificationBatch(
        task_id=source.task_id,
        task_name=source.task_name,
        provider_id=source.provider_id,
        items=source.items[:1],
        total_count=1,
        item_label="商品",
    )

    result = provider.send_batch(batch)

    assert result.success
    assert "图片已展示 0/1" in result.message
    assert transport.multipart_calls == []
    card = json.loads(transport.calls[-1][1]["content"])
    assert card["elements"][0]["tag"] == "div"
    assert card["elements"][1]["actions"][-1]["url"] == "https://www.goofish.com/item?id=1"
