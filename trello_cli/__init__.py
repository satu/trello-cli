"""trello-cli — a standalone, dependency-light Trello command-line client.

The client (:mod:`trello_cli.cli`) is stdlib-only at runtime; PyYAML is imported
lazily and only when a YAML config file is actually read. Credentials, the board
id, and well-known list ids come from the environment, a config file, or flags,
so nothing about any particular board is baked in.
"""

__version__ = "0.1.0"
