"""闲鱼商品采集、定时采集与飞书推送。"""

__version__ = "0.8.0"

from .cli import run_cli

__all__ = ["__version__", "run_cli"]
