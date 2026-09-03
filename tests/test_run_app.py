from pathlib import Path


def test_entry_point_enables_multiprocessing_before_importing_the_app() -> None:
    source = (Path(__file__).parent.parent / "run_app.py").read_text(encoding="utf-8")

    assert source.index("multiprocessing.freeze_support()") < source.index(
        "from goofish_collector.app import main"
    )
