from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .browser import GoofishBrowserSession
from .checkpoint import CheckpointStore
from .crawler import CrawlEngine, CrawlResult, RunControl, brief_error
from .exporter import export_workbook
from .models import PUBLISH_WINDOWS, SORT_MODES, CrawlConfig, SearchFilters


class UserVerificationRequired(RuntimeError):
    """The visible browser needs an operator to complete a platform check."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m goofish_collector",
        description="本机闲鱼采集。登录和安全验证须由用户在可见浏览器中完成。",
    )
    commands = parser.add_subparsers(dest="command")

    collect = commands.add_parser("collect", help="通过可见浏览器执行一次采集")
    collect.add_argument("--keyword", required=True, help="搜索关键词")
    collect.add_argument("--pages", type=int, default=50, help="最多采集页数（1-200）")
    collect.add_argument("--max-items", type=int, default=0, help="最多保留商品数，0 表示不限")
    collect.add_argument("--output-dir", type=Path, default=Path("采集结果"), help="Excel 与摘要输出目录")
    collect.add_argument("--summary-json", type=Path, help="JSON 摘要文件路径")
    collect.add_argument("--resume", action="store_true", help="从同关键词的本地检查点继续")
    collect.add_argument("--min-price", type=float, help="最低价格")
    collect.add_argument("--max-price", type=float, help="最高价格")
    collect.add_argument("--region", default="", help="地区筛选文字，例如 广东")
    collect.add_argument(
        "--published-within",
        choices=PUBLISH_WINDOWS[1:],
        default="",
        help="发布时间筛选",
    )
    collect.add_argument("--personal-only", action="store_true", help="只看个人闲置")
    collect.add_argument("--inspection-only", action="store_true", help="只看验货宝")
    collect.add_argument("--free-shipping", action="store_true", help="只看包邮")
    collect.add_argument("--brand-new", action="store_true", help="只看全新")
    collect.add_argument("--sort-mode", choices=SORT_MODES, default="综合", help="页面排序方式")

    return parser


def parse_collect_config(args: argparse.Namespace) -> CrawlConfig:
    filters = SearchFilters(
        min_price=args.min_price,
        max_price=args.max_price,
        region=args.region,
        published_within=args.published_within,
        personal_only=args.personal_only,
        inspection_only=args.inspection_only,
        free_shipping=args.free_shipping,
        brand_new=args.brand_new,
        sort_mode=args.sort_mode,
    )
    return CrawlConfig(
        keyword=args.keyword,
        max_pages=args.pages,
        max_items=args.max_items,
        output_dir=args.output_dir,
        filters=filters,
    )


def _summary_path(config: CrawlConfig, finished_at: datetime, requested: Path | None) -> Path:
    if requested is not None:
        return requested.expanduser().resolve()
    stamp = finished_at.strftime("%Y%m%d_%H%M%S_%f")
    return config.output_dir / f"闲鱼采集摘要_{stamp}.json"


def _write_summary(path: Path, summary: dict[str, Any]) -> Path:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def _result_status(stop_reason: str, verification_required: bool) -> str:
    if verification_required:
        return "verification_required"
    if stop_reason.startswith("运行错误"):
        return "error"
    if stop_reason.startswith("用户停止"):
        return "stopped"
    return "completed"


def run_collect(
    config: CrawlConfig,
    *,
    resume: bool = False,
    summary_json: Path | None = None,
    session_factory: Callable[..., Any] = GoofishBrowserSession,
    interactive: bool | None = None,
) -> dict[str, Any]:
    """Run the existing browser collector and return a machine-readable outcome."""
    control = RunControl()
    checkpoint_store = CheckpointStore.for_config(config)
    verification_required = False
    use_interactive_prompt = sys.stdin.isatty() if interactive is None else interactive

    def log(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    def request_verification(message: str) -> None:
        nonlocal verification_required
        if not use_interactive_prompt:
            verification_required = True
            raise UserVerificationRequired(
                "需要用户登录或安全验证；请在可交互终端或桌面程序中完成后重试"
            )
        print(f"需要人工处理：{message}", file=sys.stderr, flush=True)
        try:
            input("请在可见浏览器完成操作后按 Enter 继续：")
        except EOFError as exc:
            verification_required = True
            raise UserVerificationRequired("交互终端不可用，无法完成登录或安全验证") from exc
        control.resume()

    started_at = datetime.now()
    result: CrawlResult | None = None
    workbook_path: Path | None = None
    try:
        engine = CrawlEngine(
            checkpoint_store=checkpoint_store,
            control=control,
            on_log=log,
            on_verification=request_verification,
        )
        checkpoint = checkpoint_store.load() if resume else None
        with session_factory(on_log=log) as session:
            result = engine.run(config, session, resume=checkpoint)
        workbook_path = export_workbook(
            config=config,
            records=result.records,
            raw_records=result.raw_records,
            searched_pages=result.searched_pages,
            stop_reason=result.stop_reason,
            started_at=result.started_at,
            finished_at=result.finished_at,
        )
    except Exception as exc:
        finished_at = datetime.now()
        result = CrawlResult(
            records=[],
            raw_records=0,
            searched_pages=0,
            stop_reason=f"运行错误：{brief_error(exc)}",
            started_at=started_at,
            finished_at=finished_at,
        )

    status = _result_status(result.stop_reason, verification_required)
    if status == "completed":
        checkpoint_store.delete()
    destination = _summary_path(config, result.finished_at, summary_json)
    summary = {
        "schema_version": 1,
        "command": "collect",
        "status": status,
        "keyword": config.keyword,
        "filters": config.filters.to_dict(),
        "requested_pages": config.max_pages,
        "searched_pages": result.searched_pages,
        "raw_records": result.raw_records,
        "unique_records": len(result.records),
        "stop_reason": result.stop_reason,
        "started_at": result.started_at.isoformat(timespec="seconds"),
        "finished_at": result.finished_at.isoformat(timespec="seconds"),
        "output_xlsx": str(workbook_path) if workbook_path is not None else "",
        "summary_json": str(destination),
    }
    _write_summary(destination, summary)
    return summary


def _emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def run_cli(argv: list[str] | None = None) -> int:
    """Run the agent-facing command-line interface."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--self-test"]:
        from .selftest import run_self_test

        return run_self_test()
    if not arguments:
        from .app import main

        return main()

    parser = build_parser()
    args = parser.parse_args(arguments)
    if args.command == "collect":
        summary = run_collect(
            parse_collect_config(args),
            resume=args.resume,
            summary_json=args.summary_json,
        )
        _emit_json(summary)
        return {"completed": 0, "stopped": 130, "verification_required": 2, "error": 1}[summary["status"]]
    parser.print_help()
    return 2
