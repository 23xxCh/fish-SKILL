import json

from goofish_collector.feishu_binding import parse_binding_event


def test_parse_feishu_binding_event() -> None:
    event = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_user"}},
            "message": {"content": json.dumps({"text": " 绑定 "}, ensure_ascii=False)},
        }
    }

    assert parse_binding_event(event) == ("ou_user", "绑定")
