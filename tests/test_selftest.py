import pytest

from goofish_collector.selftest import run_self_test


@pytest.mark.browser
def test_packaging_self_test_flow() -> None:
    assert run_self_test() == 0

