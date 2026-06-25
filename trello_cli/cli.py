"""A small, self-contained command-line interface to Trello.

This is the standalone port of trellm's in-repo ``trello_cli.py`` (chain card
2/4). The goals of the port:

- **deployment-agnostic** — nothing about any particular Trello board is baked
  in. Credentials (``TRELLO_API_KEY`` / ``TRELLO_TOKEN``), the board id, and the
  well-known list ids are read from the environment, an optional config file, or
  flags, in that precedence. A secret can also be sourced from a command (e.g.
  ``pass``) so the token never has to sit in a plaintext file.
- **dependency-light** — the client itself is stdlib only (``urllib`` + ``json``);
  PyYAML is imported lazily and only when an actual YAML config file is read, so
  pure-env usage needs no third-party packages at all.
- it resolves list references by id *or* by name (``todo``, ``ready``,
  ``icebox``, or any list name on the board), so callers don't memorise ids.

Installed as the ``trello`` console script (see ``pyproject.toml``); the bundled
``skills/trello`` SKILL.md documents the agent workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from trello_cli import __version__

_TRELLO_ID_RE = re.compile(r"^[0-9a-fA-F]{24}$")

# Where to look for a config file when neither ``--config`` nor ``TRELLO_CONFIG``
# is given. First existing path wins. ``~/.trellm/config.yaml`` is kept last for
# back-compat with trellm (the project this CLI was factored out of); it is a
# config *location*, not a hardcoded board.
_CONFIG_ENV = "TRELLO_CONFIG"
_DEFAULT_CONFIG_PATHS = (
    "~/.config/trello-cli/config.yaml",
    "~/.trello-cli.yaml",
    "~/.trellm/config.yaml",
)

# A safe, lowercase file-extension token (e.g. ``.opus``): a downloaded
# attachment's suffix is derived from a card-supplied URL, so it is allowlisted
# before being used to name a temp file.
_SAFE_SUFFIX_RE = re.compile(r"^\.[a-z0-9]{1,5}$")

# Only these hosts (over https) get the Trello OAuth header attached. The
# download command runs on a card-supplied attachment URL, so a malicious
# link-attachment to an attacker host must NOT receive our creds (credential
# exfil).
_TRELLO_DOWNLOAD_HOSTS = {"api.trello.com", "trello.com"}


class TrelloError(Exception):
    """Any unrecoverable error talking to Trello or loading config."""


@dataclass
class Creds:
    api_key: str
    api_token: str
    board_id: str = ""
    todo_list_id: str = ""
    ready_to_try_list_id: Optional[str] = None
    icebox_list_id: Optional[str] = None


def _first(*values: Optional[str]) -> str:
    """Return the first truthy value, or ``""`` if none."""
    for value in values:
        if value:
            return value
    return ""


def _resolve_secret_cmd(cmd: Optional[str]) -> str:
    """Run a secret-producing command (e.g. ``pass show trello/token``).

    Lets the api key/token live in a password manager rather than a plaintext
    config: the command's stdout (stripped) is the secret. Bounded by a timeout
    so a hung helper can't wedge the CLI.
    """
    if not cmd:
        return ""
    try:
        proc = subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TrelloError(f"secret command {cmd!r} failed: {exc}") from exc
    if proc.returncode != 0:
        raise TrelloError(
            f"secret command {cmd!r} exited {proc.returncode}: "
            f"{proc.stderr.strip()}"
        )
    # Take the first non-empty line, in case `pass` prints extra metadata.
    for line in proc.stdout.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _config_path(config_path: Optional[str]) -> Optional[Path]:
    """Resolve which config file to read (or ``None`` for env-only use)."""
    if config_path:
        return Path(config_path).expanduser()
    env = os.environ.get(_CONFIG_ENV)
    if env:
        return Path(env).expanduser()
    for candidate in _DEFAULT_CONFIG_PATHS:
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    return None


def _load_config_section(path: Optional[Path]) -> dict:
    """Read the Trello settings dict from ``path`` (YAML), or ``{}``.

    Accepts either a top-level ``trello:`` section (trellm's layout) or a flat
    mapping (a standalone config that is only about Trello). PyYAML is imported
    lazily so env-only usage needs no third-party dependency.
    """
    if not path or not path.exists():
        return {}
    try:
        import yaml  # lazy: only needed when an actual config file is read
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on install
        raise TrelloError(
            f"reading config {path} needs PyYAML; install it "
            "(`pip install pyyaml`) or supply creds via environment variables."
        ) from exc
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        return {}
    section = loaded.get("trello")
    if isinstance(section, dict):
        return section
    return loaded


def load_creds(config_path: Optional[str] = None) -> Creds:
    """Load Trello creds + well-known list ids from env / config / secret cmd.

    Precedence is environment variable > config file value > secret command.
    Raises :class:`TrelloError` if the key/token can't be found anywhere.
    """
    data = _load_config_section(_config_path(config_path))

    api_key = _first(os.environ.get("TRELLO_API_KEY"), data.get("api_key"))
    api_token = _first(
        os.environ.get("TRELLO_TOKEN"),
        os.environ.get("TRELLO_API_TOKEN"),  # back-compat with trellm's name
        data.get("api_token"),
        data.get("token"),
    )
    if not api_key:
        api_key = _resolve_secret_cmd(
            _first(os.environ.get("TRELLO_API_KEY_CMD"), data.get("api_key_cmd"))
        )
    if not api_token:
        api_token = _resolve_secret_cmd(
            _first(
                os.environ.get("TRELLO_TOKEN_CMD"),
                data.get("api_token_cmd"),
                data.get("token_cmd"),
            )
        )

    creds = Creds(
        api_key=api_key,
        api_token=api_token,
        board_id=_first(os.environ.get("TRELLO_BOARD_ID"), data.get("board_id")),
        todo_list_id=_first(
            os.environ.get("TRELLO_TODO_LIST_ID"), data.get("todo_list_id")
        ),
        ready_to_try_list_id=_first(
            os.environ.get("TRELLO_READY_LIST_ID"),
            data.get("ready_to_try_list_id"),
            data.get("ready_list_id"),
        )
        or None,
        icebox_list_id=_first(
            os.environ.get("TRELLO_ICEBOX_LIST_ID"), data.get("icebox_list_id")
        )
        or None,
    )
    if not creds.api_key or not creds.api_token:
        raise TrelloError(
            "Trello api_key/api_token not found. Set TRELLO_API_KEY and "
            "TRELLO_TOKEN in the environment, put them in a config file "
            "(--config / TRELLO_CONFIG), or point *_CMD at a secret command "
            "such as `pass show trello/token`."
        )
    return creds


def _do_http(method: str, url: str, data: Optional[dict] = None) -> Any:
    """Perform one HTTP request and return parsed JSON (or ``{}``).

    Isolated so tests can monkeypatch the network away. ``data`` is sent
    form-urlencoded (Trello accepts form bodies for POST/PUT).
    """
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise TrelloError(f"Trello API {exc.code} on {method} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise TrelloError(f"Network error on {method} {url}: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _do_download(url: str, headers: dict) -> bytes:
    """Fetch ``url`` with ``headers`` and return the raw bytes.

    Isolated (like :func:`_do_http`) so tests can monkeypatch the network away.
    Trello attachment downloads need an OAuth ``Authorization`` header rather
    than the query-string key/token the JSON API uses.
    """
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise TrelloError(f"Trello download {exc.code} on {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise TrelloError(f"Network error downloading {url}: {exc.reason}") from exc


def _safe_suffix(url: str) -> str:
    """Derive a safe file-extension suffix (e.g. ``.opus``) from ``url``'s path.

    The suffix names a temp file, so it is allowlisted to a simple lowercase
    token (``_SAFE_SUFFIX_RE``); anything else (a missing, weird, or injection-y
    extension) yields ``""`` rather than being trusted.
    """
    name = os.path.basename(urllib.parse.urlparse(url).path)
    ext = os.path.splitext(name)[1].lower()
    return ext if _SAFE_SUFFIX_RE.fullmatch(ext) else ""


class TrelloAPI:
    BASE = "https://api.trello.com/1"

    def __init__(self, creds: Creds):
        self.creds = creds

    def request(
        self, method: str, path: str, params: Optional[dict] = None, data: Optional[dict] = None
    ) -> Any:
        query = dict(params or {})
        query["key"] = self.creds.api_key
        query["token"] = self.creds.api_token
        url = f"{self.BASE}{path}?{urllib.parse.urlencode(query)}"
        return _do_http(method, url, data)

    # --- list resolution --------------------------------------------------

    def resolve_list(self, ref: str) -> str:
        """Resolve a list reference (id, alias, or name) to a list id."""
        if _TRELLO_ID_RE.match(ref):
            return ref
        norm = ref.strip().lower()
        aliases = {
            "todo": self.creds.todo_list_id,
            "to do": self.creds.todo_list_id,
            "ready": self.creds.ready_to_try_list_id,
            "ready to try": self.creds.ready_to_try_list_id,
            "icebox": self.creds.icebox_list_id,
            "ice box": self.creds.icebox_list_id,
        }
        if norm in aliases and aliases[norm]:
            return aliases[norm]
        # Fall back to matching a list name on the board.
        for lst in self.get_lists():
            if lst.get("name", "").strip().lower() == norm:
                return lst["id"]
        raise TrelloError(
            f"Could not resolve list '{ref}'. Use a 24-char list id, one of "
            "todo/ready/icebox, or an exact list name (see `trello lists`)."
        )

    # --- reads ------------------------------------------------------------

    def get_lists(self) -> list:
        if not self.creds.board_id:
            raise TrelloError(
                "No board_id configured; cannot list board lists. Set "
                "TRELLO_BOARD_ID, board_id in the config, or pass --board-id."
            )
        return self.request("GET", f"/boards/{self.creds.board_id}/lists")

    def get_card(self, card_id: str) -> dict:
        return self.request(
            "GET",
            f"/cards/{card_id}",
            params={
                "fields": "name,desc,idList,url,closed,dateLastActivity",
                "attachments": "true",
                "attachment_fields": "name,url,mimeType,bytes",
            },
        )

    def get_cards(self, list_id: str) -> list:
        return self.request(
            "GET",
            f"/lists/{list_id}/cards",
            params={"fields": "name,desc,idList,url,dateLastActivity"},
        )

    def get_comments(self, card_id: str) -> list:
        actions = self.request(
            "GET",
            f"/cards/{card_id}/actions",
            params={"filter": "commentCard", "limit": "100"},
        )
        # Trello returns newest-first; flip to oldest-first and flatten.
        out = []
        for a in reversed(actions):
            out.append(
                {
                    "id": a.get("id"),
                    "date": a.get("date"),
                    "member": (a.get("memberCreator") or {}).get("fullName"),
                    "text": (a.get("data") or {}).get("text", ""),
                }
            )
        return out

    # --- mutations --------------------------------------------------------

    def add_comment(self, card_id: str, text: str) -> dict:
        return self.request(
            "POST", f"/cards/{card_id}/actions/comments", data={"text": text}
        )

    def create_card(self, list_id: str, name: str, desc: str = "") -> dict:
        return self.request(
            "POST", "/cards", data={"idList": list_id, "name": name, "desc": desc}
        )

    def move_card(self, card_id: str, list_id: str) -> dict:
        return self.request("PUT", f"/cards/{card_id}", data={"idList": list_id})

    def archive_card(self, card_id: str, closed: bool = True) -> dict:
        return self.request(
            "PUT", f"/cards/{card_id}", data={"closed": str(closed).lower()}
        )

    def download(self, url: str, output_path: Optional[str] = None) -> tuple[str, int]:
        """Download a Trello attachment and return ``(path_written, n_bytes)``.

        Trello gates attachment downloads behind an OAuth ``Authorization``
        header (the query-string key/token used elsewhere returns the HTML page,
        not the file).

        Credentialed-SSRF / credential-exfil hardening: the URL comes from a
        card attachment, so it is untrusted. The scheme is allowlisted to
        ``https`` (rejected otherwise), and the OAuth header is attached **only**
        when the host is a known Trello host. A non-Trello host (e.g. a
        pre-signed S3 attachment, or a malicious link) is fetched
        **uncredentialed** so our Trello creds never leave to a foreign host.

        Predictable-temp-path hardening:

        * ``output_path=None`` (recommended): the file is created with
          :func:`tempfile.mkstemp` — owner-only (``0600``), ``O_EXCL`` and an
          **unpredictable** name — so there is no path to pre-plant. The suffix
          is derived from the URL but allowlisted (``_SAFE_SUFFIX_RE``).
        * an explicit ``output_path`` is opened ``O_NOFOLLOW`` (a pre-planted
          symlink at the final component is refused) with mode ``0600``.
        """
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() != "https":
            raise TrelloError(
                f"refusing to download from a non-https URL ({parsed.scheme or 'no-scheme'}): "
                "only https attachment URLs are allowed."
            )
        headers = {"User-Agent": "trello-cli"}
        if (parsed.hostname or "").lower() in _TRELLO_DOWNLOAD_HOSTS:
            headers["Authorization"] = (
                f'OAuth oauth_consumer_key="{self.creds.api_key}", '
                f'oauth_token="{self.creds.api_token}"'
            )
        data = _do_download(url, headers)
        if output_path is None:
            fd, path = tempfile.mkstemp(prefix="trello-dl-", suffix=_safe_suffix(url))
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
            except BaseException:
                os.unlink(path)
                raise
            return path, len(data)
        directory = os.path.dirname(output_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        try:
            fd = os.open(
                output_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o600,
            )
        except OSError as exc:
            # ELOOP ⇒ the final component is a (pre-planted) symlink; O_NOFOLLOW
            # refuses to follow it rather than clobbering its destination.
            raise TrelloError(
                f"refusing to write {output_path!r}: {exc.strerror or exc} "
                "(is it a symlink?)"
            ) from exc
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return output_path, len(data)


# --- CLI ------------------------------------------------------------------


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="trello",
        description="Self-contained, dependency-light Trello command-line "
        "client. Reads credentials and ids from the environment, a config "
        "file, or flags.",
    )
    p.add_argument(
        "--config",
        help="Path to a YAML config file (default: $TRELLO_CONFIG, then the "
        "first existing of ~/.config/trello-cli/config.yaml, "
        "~/.trello-cli.yaml, ~/.trellm/config.yaml).",
    )
    p.add_argument(
        "--board-id",
        help="Board id (overrides TRELLO_BOARD_ID / the config's board_id).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("lists", help="List the lists on the configured board.")

    sp = sub.add_parser("cards", help="List cards in a list (id, alias, or name).")
    sp.add_argument("list", help="List id, alias (todo/ready/icebox), or name.")

    sp = sub.add_parser("card", help="Show one card (incl. attachments).")
    sp.add_argument("card_id")

    sp = sub.add_parser("comments", help="Show a card's comments, oldest-first.")
    sp.add_argument("card_id")

    sp = sub.add_parser("comment", help="Add a comment to a card.")
    sp.add_argument("card_id")
    sp.add_argument("text")

    sp = sub.add_parser("create", help="Create a card in a list.")
    sp.add_argument("list", help="List id, alias, or name.")
    sp.add_argument("name")
    sp.add_argument("--desc", default="", help="Card description.")

    sp = sub.add_parser("move", help="Move a card to another list.")
    sp.add_argument("card_id")
    sp.add_argument("list", help="Destination list id, alias, or name.")

    sp = sub.add_parser("archive", help="Archive (close) a card.")
    sp.add_argument("card_id")

    sp = sub.add_parser(
        "download",
        help="Download an authenticated Trello attachment (e.g. a voice note).",
    )
    sp.add_argument("url", help="Attachment URL (from `trello card`'s attachments).")
    sp.add_argument(
        "output_path",
        nargs="?",
        help="Local path to write the file to. Omit to write to a secure "
        "temp file (owner-only, unpredictable name) and print its path.",
    )

    return p


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv or "-V" in argv:
        print(f"trello {__version__}")
        return 0

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        creds = load_creds(args.config)
        if args.board_id:
            creds.board_id = args.board_id
        api = TrelloAPI(creds)
        if args.command == "lists":
            _print_json(api.get_lists())
        elif args.command == "cards":
            _print_json(api.get_cards(api.resolve_list(args.list)))
        elif args.command == "card":
            _print_json(api.get_card(args.card_id))
        elif args.command == "comments":
            _print_json(api.get_comments(args.card_id))
        elif args.command == "comment":
            api.add_comment(args.card_id, args.text)
            print("Comment added.")
        elif args.command == "create":
            card = api.create_card(api.resolve_list(args.list), args.name, args.desc)
            print(f"Created: {card.get('url') or card.get('id')}")
        elif args.command == "move":
            api.move_card(args.card_id, api.resolve_list(args.list))
            print("Card moved.")
        elif args.command == "archive":
            api.archive_card(args.card_id)
            print("Card archived.")
        elif args.command == "download":
            path, n = api.download(args.url, args.output_path)
            if args.output_path is None:
                # Print just the path so it is capturable: clip="$(trello
                # download <url>)"; transcribe "$clip". The byte count goes to
                # stderr to keep stdout machine-clean.
                print(f"Saved {n} bytes", file=sys.stderr)
                print(path)
            else:
                print(f"Saved {n} bytes to {path}")
        else:  # pragma: no cover - argparse enforces a valid command
            parser.error(f"unknown command {args.command!r}")
    except TrelloError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
