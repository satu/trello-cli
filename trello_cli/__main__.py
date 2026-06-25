"""Entry point for the `trello` console script.

The real implementation lives in :mod:`trello_cli.cli`; this module just
re-exports :func:`main` so both the ``trello`` console script
(``trello_cli.__main__:main``) and ``python -m trello_cli`` resolve to it.
"""

from trello_cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
