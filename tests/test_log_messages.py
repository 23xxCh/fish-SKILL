from goofish_collector.crawler import brief_error


def test_browser_retry_error_is_kept_to_one_readable_line() -> None:
    error = RuntimeError(
        "Locator.click: Timeout 15000ms exceeded.\n"
        "Call log:\n"
        "  - waiting for element to be visible, enabled and stable\n"
        "  - retrying click action"
    )

    message = brief_error(error)

    assert message == "Locator.click: Timeout 15000ms exceeded."
    assert "\n" not in message
