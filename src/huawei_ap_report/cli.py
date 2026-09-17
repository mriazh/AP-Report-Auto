"""Command line entry point.

    ap-report-auto run              collect today, archive, rebuild the month
    ap-report-auto reports          rebuild the month from the archive only
    ap-report-auto check            verify config and templates, touch nothing
    ap-report-auto install-browser  download the Chromium build for collection

Exit codes: ``0`` success, ``1`` job failure, ``2`` configuration problem.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .errors import ApReportError, ConfigError
from .huawei import PlaywrightCollector
from .job import DailyJob, rebuild_only
from .notifier import GowaNotifier, NullNotifier
from .settings import Settings, load_settings

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

logger = logging.getLogger("huawei_ap_report")

# Import the process lock
from .lock import LockContentionError, process_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ap-report-auto", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--env-file", default="config/.env", help="path to the env file (default: config/.env)")
    parser.add_argument("--log-level", default=None, help="override LOG_LEVEL from the env file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="collect today, archive, and rebuild the month")
    run.add_argument("--date", help="use this YYYY-MM-DD as the export day instead of today")
    run.add_argument(
        "--no-notify",
        action="store_true",
        help="skip WhatsApp notices for this run (the timer never sets this)",
    )
    subparsers.add_parser("reports", help="rebuild the month from existing raw snapshots")
    subparsers.add_parser("check", help="verify configuration and templates without collecting")
    subparsers.add_parser(
        "install-browser", help="download the Chromium build used by the portal adapter"
    )
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )


def _build_notifier(settings: Settings, *, enabled: bool) -> object:
    if not enabled or not settings.notify.enabled:
        return NullNotifier()
    return GowaNotifier(
        settings.notify,
        tls_verify=settings.notify.tls_verify,
        ca_bundle=settings.notify.tls_ca_bundle,
    )


def _command_run(args, settings: Settings) -> int:
    day = None
    if args.date:
        try:
            day = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"--date must look like YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
            return EXIT_CONFIG

    settings.verify_configured()
    notifier = _build_notifier(settings, enabled=not args.no_notify)
    
    # Use process lock to prevent concurrent runs
    lock_path = settings.output_root / '.run.lock'
    try:
        with process_lock(lock_path, blocking=False):
            with PlaywrightCollector(
                settings.huawei,
                tls_ca_bundle=settings.huawei.tls_ca_bundle,
                tls_verify=settings.huawei.tls_verify,
            ) as exporter:
                result = DailyJob(settings, exporter, notifier).run(day)
    except LockContentionError:
        print("Another ap-report-auto process is currently holding the output lock", file=sys.stderr)
        return EXIT_FAILED
    except OSError:
        print("Another ap-report-auto run is currently in progress", file=sys.stderr)
        return EXIT_FAILED

    if result.ok:
        print(f"OK  {result.day.isoformat()}  {result.snapshot.directory}")
        for path in result.reports:
            print(f"    {path}")
        return EXIT_OK
    print(f"FAIL {result.day.isoformat()}  {result.error}", file=sys.stderr)
    return EXIT_FAILED


def _command_reports(settings: Settings) -> int:
    lock_path = settings.output_root / '.run.lock'
    try:
        with process_lock(lock_path, blocking=False):
            month = settings.report_month or settings.month_of()
            paths = rebuild_only(settings, month)
    except LockContentionError:
        print("Another ap-report-auto process is currently holding the output lock", file=sys.stderr)
        return EXIT_FAILED
    except OSError:
        print("Another ap-report-auto process is currently holding the output lock", file=sys.stderr)
        return EXIT_FAILED
    print(f"OK  rebuilt {month[0]:04d}-{month[1]:02d}")
    for path in paths:
        print(f"    {path}")
    return EXIT_OK


def _command_check(settings: Settings, env_file: Path) -> int:
    from .reports.publish import REPORT_KINDS
    from .workbook import resolve_template

    print(f"config       {env_file}")
    print(f"timezone     {settings.timezone_name} ({settings.now().isoformat()})")
    print(f"output root  {settings.output_root}")
    print(f"templates    {settings.template_dir}")
    problems: list[str] = []
    for kind, (keyword, _) in REPORT_KINDS.items():
        try:
            print(f"  {kind:<10} {resolve_template(settings.template_dir, keyword)}")
        except ApReportError as exc:
            problems.append(str(exc))
            print(f"  {kind:<10} MISSING: {exc}")
    try:
        settings.verify_configured()
        print("credentials  configured")
    except ConfigError as exc:
        problems.append(str(exc))
        print(f"credentials  not usable: {exc}")
    print(f"notifications {'enabled' if settings.notify.enabled else 'disabled'}")
    if problems:
        print(f"\n{len(problems)} problem(s) to fix before a scheduled run", file=sys.stderr)
        return EXIT_CONFIG
    print("\nready")
    return EXIT_OK


def _command_install_browser() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed: pip install '.[browser]'", file=sys.stderr)
        return EXIT_CONFIG
    with sync_playwright() as playwright:
        playwright.chromium.install()
    print("chromium installed")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "install-browser":
        return _command_install_browser()

    try:
        settings = load_settings(Path(args.env_file))
    except ConfigError as exc:
        configure_logging(args.log_level or "INFO")
        logger.error("%s", exc)
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    configure_logging(args.log_level or settings.log_level)

    try:
        if args.command == "run":
            return _command_run(args, settings)
        if args.command == "reports":
            return _command_reports(settings)
        if args.command == "check":
            return _command_check(settings, Path(args.env_file))
    except ApReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    parser.error(f"unknown command {args.command!r}")
    return EXIT_CONFIG


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())