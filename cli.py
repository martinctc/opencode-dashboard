"""Console entry point for the ``opencode-dashboard`` command."""

import sys
from typing import Optional

from dashboard import main as render_main
from extract_usage import main as export_main

USAGE = "usage: opencode-dashboard {export,render} [options]"


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        print()
        print("  export   read OpenCode's local database and write a CSV export")
        print("  render   build a self-contained HTML dashboard from one or more exports")
        return 0
    command, rest = args[0], args[1:]
    if command == "export":
        return export_main(rest)
    if command == "render":
        return render_main(rest)
    print(f"unknown command: {command}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())