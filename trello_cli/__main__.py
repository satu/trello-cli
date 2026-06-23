"""Entry point for the `trello` console script.

This is a stub. Packaging + the `trello` entry point are stood up here (chain
card 1/4); the real subcommands (lists/cards/card/comments/comment/create/
move/archive/download) are ported from trellm's `trello_cli.py` in card 2/4.
"""

import sys
from typing import List, Optional

from trello_cli import __version__


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv or "-V" in argv:
        print(f"trello {__version__}")
        return 0

    print(
        "trello-cli is scaffolded but not implemented yet — the Trello client "
        "is ported in chain card 2/4.\n"
        "See the README for the planned command surface."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
