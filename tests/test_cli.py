"""Tests for the standalone Trello CLI (``trello_cli.cli`` / the ``trello``
console script).

The HTTP layer (``cli._do_http`` / ``cli._do_download`` / ``cli._do_upload``)
is monkeypatched so these tests assert on the method/URL each command issues
and how it shapes output — without ever touching the network. The seams
themselves are covered by ``TestNetworkSeams`` via a fake ``urlopen``.
"""

import email
import io
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from email import policy

import pytest

import trello_cli
from trello_cli import cli


# --- packaging contract ---------------------------------------------------


def test_console_script_entry_point_resolves():
    result = subprocess.run(
        [sys.executable, "-m", "trello_cli", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert trello_cli.__version__ in result.stdout


# --- fixtures -------------------------------------------------------------


def _creds() -> cli.Creds:
    return cli.Creds(
        api_key="KEY",
        api_token="TOK",
        board_id="694dd9802e3ad21db9ca5da1",
        todo_list_id="694dd98f57680df4b26fe1c1",
        ready_to_try_list_id="694e7177ae98fb33dc26c3c9",
        icebox_list_id="695135da7cec68ebbcd333de",
    )


class FakeHTTP:
    """Records requests and replays canned responses keyed by (method, path).

    ``path`` is matched against the URL with the query string stripped, so
    tests don't have to spell out ``key``/``token`` query params.
    """

    def __init__(self, responses=None):
        self.calls = []  # list of (method, url, data)
        self._responses = responses or {}

    def __call__(self, method, url, data=None):
        self.calls.append((method, url, data))
        path = url.split("?", 1)[0].replace(cli.TrelloAPI.BASE, "")
        if (method, path) in self._responses:
            return self._responses[(method, path)]
        return {}

    def last_url(self):
        return self.calls[-1][1]


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP()
    monkeypatch.setattr(cli, "_do_http", fake)
    monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
    return fake


def _run(argv, capsys):
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, out


# --- credential loading ---------------------------------------------------


class TestLoadCreds:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        # Keep the host's real config/env out of these tests: blank the
        # default-path search so a real ~/.trellm/config.yaml isn't read, and
        # clear every env var the loader consults.
        monkeypatch.setattr(cli, "_DEFAULT_CONFIG_PATHS", ())
        for var in (
            "TRELLO_API_KEY",
            "TRELLO_TOKEN",
            "TRELLO_API_TOKEN",
            "TRELLO_BOARD_ID",
            "TRELLO_TODO_LIST_ID",
            "TRELLO_READY_LIST_ID",
            "TRELLO_ICEBOX_LIST_ID",
            "TRELLO_CONFIG",
            "TRELLO_API_KEY_CMD",
            "TRELLO_TOKEN_CMD",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_reads_trello_section_from_yaml(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "trello:\n"
            "  api_key: yamlkey\n"
            "  api_token: yamltok\n"
            "  board_id: brd\n"
            "  todo_list_id: todo\n"
            "  ready_to_try_list_id: ready\n"
            "  icebox_list_id: ice\n"
        )
        creds = cli.load_creds(str(cfg))
        assert creds.api_key == "yamlkey"
        assert creds.api_token == "yamltok"
        assert creds.board_id == "brd"
        assert creds.todo_list_id == "todo"
        assert creds.ready_to_try_list_id == "ready"
        assert creds.icebox_list_id == "ice"

    def test_reads_flat_yaml_without_trello_section(self, tmp_path):
        # A standalone config that is only about Trello needn't nest under a
        # `trello:` key.
        cfg = tmp_path / "config.yaml"
        cfg.write_text("api_key: flatkey\napi_token: flattok\nboard_id: brd\n")
        creds = cli.load_creds(str(cfg))
        assert creds.api_key == "flatkey"
        assert creds.api_token == "flattok"
        assert creds.board_id == "brd"

    def test_key_aliases_in_config(self, tmp_path):
        # `token` is accepted as an alias for `api_token`, and `ready_list_id`
        # for `ready_to_try_list_id`.
        cfg = tmp_path / "config.yaml"
        cfg.write_text("api_key: k\ntoken: t\nready_list_id: rdy\n")
        creds = cli.load_creds(str(cfg))
        assert creds.api_token == "t"
        assert creds.ready_to_try_list_id == "rdy"

    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("trello:\n  api_key: yamlkey\n  api_token: yamltok\n")
        monkeypatch.setenv("TRELLO_API_KEY", "envkey")
        monkeypatch.setenv("TRELLO_TOKEN", "envtok")
        creds = cli.load_creds(str(cfg))
        assert creds.api_key == "envkey"
        assert creds.api_token == "envtok"

    def test_env_only_no_config_file(self, monkeypatch):
        # Deployment-agnostic: env vars alone are enough, no file needed.
        monkeypatch.setenv("TRELLO_API_KEY", "envkey")
        monkeypatch.setenv("TRELLO_TOKEN", "envtok")
        monkeypatch.setenv("TRELLO_BOARD_ID", "envbrd")
        creds = cli.load_creds()
        assert (creds.api_key, creds.api_token, creds.board_id) == (
            "envkey",
            "envtok",
            "envbrd",
        )

    def test_legacy_api_token_env_name_still_works(self, monkeypatch):
        # trellm used TRELLO_API_TOKEN; keep honouring it.
        monkeypatch.setenv("TRELLO_API_KEY", "k")
        monkeypatch.setenv("TRELLO_API_TOKEN", "legacytok")
        creds = cli.load_creds()
        assert creds.api_token == "legacytok"

    def test_config_env_var_points_at_file(self, tmp_path, monkeypatch):
        cfg = tmp_path / "elsewhere.yaml"
        cfg.write_text("api_key: k\napi_token: t\n")
        monkeypatch.setenv("TRELLO_CONFIG", str(cfg))
        creds = cli.load_creds()
        assert creds.api_key == "k"

    def test_secret_command_sources_token(self, tmp_path, monkeypatch):
        # `pass`-style: a command produces the secret so it isn't in plaintext.
        monkeypatch.setenv("TRELLO_API_KEY", "k")
        monkeypatch.setenv("TRELLO_TOKEN_CMD", "printf sekret")
        creds = cli.load_creds()
        assert creds.api_token == "sekret"

    @pytest.mark.parametrize(
        "env, config",
        [
            ({"TRELLO_API_KEY_CMD": "printf cmdkey", "TRELLO_TOKEN": "t"}, ""),
            ({}, "api_key_cmd: printf cmdkey\napi_token: t\n"),
            ({"TRELLO_API_KEY": "cmdkey"}, "api_token_cmd: printf t\n"),
            ({"TRELLO_API_KEY": "cmdkey"}, "token_cmd: printf t\n"),
        ],
        ids=["env-key-cmd", "config-api_key_cmd", "config-api_token_cmd", "config-token_cmd"],
    )
    def test_secret_command_keys(self, tmp_path, monkeypatch, env, config):
        # Every *_CMD spelling (env and config) is honoured for its secret.
        for var, value in env.items():
            monkeypatch.setenv(var, value)
        cfg = tmp_path / "config.yaml"
        cfg.write_text(config)
        creds = cli.load_creds(str(cfg))
        assert (creds.api_key, creds.api_token) == ("cmdkey", "t")

    def test_env_beats_config_beats_secret_command(self, tmp_path, monkeypatch):
        # A losing secret command is `false`: if it were run despite a
        # higher-precedence value, load_creds would raise.
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "api_key: cfgkey\n"
            "api_key_cmd: 'false'\n"
            "token_cmd: 'false'\n"
            "api_token_cmd: 'false'\n"
        )
        monkeypatch.setenv("TRELLO_TOKEN", "envtok")
        monkeypatch.setenv("TRELLO_API_KEY_CMD", "false")
        creds = cli.load_creds(str(cfg))
        assert (creds.api_key, creds.api_token) == ("cfgkey", "envtok")

    def test_env_secret_command_beats_config_secret_command(
        self, tmp_path, monkeypatch
    ):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("api_key: k\napi_token_cmd: 'false'\ntoken_cmd: 'false'\n")
        monkeypatch.setenv("TRELLO_TOKEN_CMD", "printf envcmdtok")
        creds = cli.load_creds(str(cfg))
        assert creds.api_token == "envcmdtok"

    def test_secret_command_failure_raises(self, monkeypatch):
        monkeypatch.setenv("TRELLO_API_KEY", "k")
        monkeypatch.setenv("TRELLO_TOKEN_CMD", "false")
        with pytest.raises(cli.TrelloError):
            cli.load_creds()

    def test_missing_credentials_raises(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("trello:\n  board_id: brd\n")
        with pytest.raises(cli.TrelloError):
            cli.load_creds(str(cfg))


# --- network seams (_do_http / _do_download / _do_upload) ----------------


class FakeUrlopen:
    """Stands in for ``urllib.request.urlopen``: either raises ``error`` or
    returns a response whose ``read()`` yields ``body``. No real network."""

    def __init__(self, body=b"", error=None):
        self.body = body
        self.error = error

    def __call__(self, req, timeout=None):
        if self.error is not None:
            raise self.error
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def _http_error(code=404, msg="Not Found", body=b"card not found"):
    return urllib.error.HTTPError(
        "https://api.trello.com/1/x", code, msg, {}, io.BytesIO(body)
    )


class TestNetworkSeams:
    """The real ``urlopen``-calling seams, exercised through the CLI with a
    fake ``urlopen``: transport failures must become ``TrelloError`` (exit 1
    plus a useful stderr message), not a traceback."""

    @pytest.fixture
    def cmds(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
        f = tmp_path / "up.txt"
        f.write_text("x")
        return {
            "http": ["card", "CARD"],
            "download": ["download", "https://trello.com/x", str(tmp_path / "o")],
            "upload": ["attach", "CARD", str(f)],
        }

    @pytest.mark.parametrize(
        "seam, error, expected",
        [
            ("http", _http_error(), ["404", "card not found"]),
            ("download", _http_error(), ["404", "Not Found"]),
            ("upload", _http_error(), ["404", "card not found"]),
            ("http", urllib.error.URLError("conn refused"), ["Network error", "conn refused"]),
            ("download", urllib.error.URLError("conn refused"), ["Network error", "conn refused"]),
            ("upload", urllib.error.URLError("conn refused"), ["Network error", "conn refused"]),
        ],
        ids=[
            "http-HTTPError", "download-HTTPError", "upload-HTTPError",
            "http-URLError", "download-URLError", "upload-URLError",
        ],
    )
    def test_transport_errors_exit_1_with_message(
        self, monkeypatch, capsys, cmds, seam, error, expected
    ):
        monkeypatch.setattr(urllib.request, "urlopen", FakeUrlopen(error=error))
        code = cli.main(cmds[seam])
        err = capsys.readouterr().err
        assert code == 1
        assert err.startswith("error: ")
        for text in expected:
            assert text in err

    @pytest.mark.parametrize(
        "body, expected",
        [(b"", {}), (b"not json", "not json"), (b'{"id": "C1"}', {"id": "C1"})],
        ids=["empty", "non-json", "json"],
    )
    @pytest.mark.parametrize("seam", ["http", "upload"])
    def test_response_body_parsing(self, monkeypatch, seam, body, expected):
        monkeypatch.setattr(urllib.request, "urlopen", FakeUrlopen(body=body))
        url = f"{cli.TrelloAPI.BASE}/cards/C1"
        if seam == "http":
            result = cli._do_http("GET", url)
        else:
            result = cli._do_upload(url, {}, b"")
        assert result == expected


# --- request building -----------------------------------------------------


class TestRequest:
    def test_injects_key_and_token(self, http):
        api = cli.TrelloAPI(_creds())
        api.request("GET", "/cards/abc")
        method, url, _ = http.calls[-1]
        assert method == "GET"
        assert url.startswith(cli.TrelloAPI.BASE + "/cards/abc?")
        assert "key=KEY" in url
        assert "token=TOK" in url


# --- list resolution ------------------------------------------------------


class TestResolveList:
    def test_passes_through_24_hex_id(self, http):
        api = cli.TrelloAPI(_creds())
        assert api.resolve_list("694e7177ae98fb33dc26c3c9") == "694e7177ae98fb33dc26c3c9"
        assert http.calls == []

    def test_known_aliases(self, http):
        api = cli.TrelloAPI(_creds())
        assert api.resolve_list("todo") == _creds().todo_list_id
        assert api.resolve_list("ready") == _creds().ready_to_try_list_id
        assert api.resolve_list("READY TO TRY") == _creds().ready_to_try_list_id
        assert api.resolve_list("ice box") == _creds().icebox_list_id
        assert api.resolve_list("icebox") == _creds().icebox_list_id

    def test_resolves_name_against_board(self, monkeypatch):
        fake = FakeHTTP(
            {
                ("GET", "/boards/694dd9802e3ad21db9ca5da1/lists"): [
                    {"id": "L1", "name": "TODO"},
                    {"id": "L2", "name": "PARKED"},
                ]
            }
        )
        monkeypatch.setattr(cli, "_do_http", fake)
        api = cli.TrelloAPI(_creds())
        assert api.resolve_list("parked") == "L2"

    def test_unknown_name_raises(self, monkeypatch):
        fake = FakeHTTP(
            {("GET", "/boards/694dd9802e3ad21db9ca5da1/lists"): [{"id": "L1", "name": "TODO"}]}
        )
        monkeypatch.setattr(cli, "_do_http", fake)
        api = cli.TrelloAPI(_creds())
        with pytest.raises(cli.TrelloError):
            api.resolve_list("nonexistent")


# --- commands -------------------------------------------------------------


class TestCardCommand:
    def test_prints_card_json(self, http, capsys):
        http._responses[("GET", "/cards/6a2fa027a5237efa86b721a5")] = {
            "id": "6a2fa027a5237efa86b721a5",
            "name": "fix the thing",
            "desc": "do it",
            "idList": "694dd98f57680df4b26fe1c1",
            "url": "https://trello.com/c/QpczFJXX",
            "attachments": [],
        }
        code, out = _run(["card", "6a2fa027a5237efa86b721a5"], capsys)
        assert code == 0
        data = json.loads(out)
        assert data["name"] == "fix the thing"
        assert "attachments=true" in http.last_url()


class TestListsCommand:
    def test_board_id_flag_overrides(self, monkeypatch, capsys):
        fake = FakeHTTP({("GET", "/boards/FLAGBOARD/lists"): [{"id": "L", "name": "X"}]})
        monkeypatch.setattr(cli, "_do_http", fake)
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
        code, out = _run(["--board-id", "FLAGBOARD", "lists"], capsys)
        assert code == 0
        assert "/boards/FLAGBOARD/lists" in fake.last_url()

    def test_no_board_id_errors(self, monkeypatch, capsys):
        creds = _creds()
        creds.board_id = ""
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: creds)
        monkeypatch.setattr(cli, "_do_http", FakeHTTP())
        code = cli.main(["lists"])
        err = capsys.readouterr().err
        assert code == 1
        assert "board_id" in err


class TestCardsCommand:
    def test_cards_in_resolved_list(self, monkeypatch, capsys):
        fake = FakeHTTP(
            {
                ("GET", "/lists/694dd98f57680df4b26fe1c1/cards"): [
                    {"id": "c1", "name": "a", "url": "u1"},
                    {"id": "c2", "name": "b", "url": "u2"},
                ]
            }
        )
        monkeypatch.setattr(cli, "_do_http", fake)
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
        code, out = _run(["cards", "todo"], capsys)
        assert code == 0
        data = json.loads(out)
        assert [d["id"] for d in data] == ["c1", "c2"]


class TestCommentsCommand:
    def test_comments_oldest_first(self, http, capsys):
        http._responses[("GET", "/cards/CARD/actions")] = [
            {"date": "2026-06-02", "memberCreator": {"fullName": "Dario"}, "data": {"text": "newer"}},
            {"date": "2026-06-01", "memberCreator": {"fullName": "Claude"}, "data": {"text": "older"}},
        ]
        code, out = _run(["comments", "CARD"], capsys)
        assert code == 0
        data = json.loads(out)
        assert [c["text"] for c in data] == ["older", "newer"]
        assert "filter=commentCard" in http.last_url()


class TestCommentCommand:
    def test_posts_comment(self, http, capsys):
        code, out = _run(["comment", "CARD", "Claude: done"], capsys)
        assert code == 0
        method, url, data = http.calls[-1]
        assert method == "POST"
        assert "/cards/CARD/actions/comments" in url
        assert data == {"text": "Claude: done"}


class TestCreateCommand:
    def test_creates_card_in_resolved_list(self, monkeypatch, capsys):
        fake = FakeHTTP({("POST", "/cards"): {"id": "new", "url": "https://trello.com/c/new"}})
        monkeypatch.setattr(cli, "_do_http", fake)
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
        code, out = _run(["create", "icebox", "new thing", "--desc", "body"], capsys)
        assert code == 0
        method, url, data = fake.calls[-1]
        assert method == "POST"
        assert "/cards" in url
        assert data["idList"] == _creds().icebox_list_id
        assert data["name"] == "new thing"
        assert data["desc"] == "body"


class TestMoveCommand:
    def test_moves_card_to_resolved_list(self, http, capsys):
        code, out = _run(["move", "CARD", "ready"], capsys)
        assert code == 0
        method, url, data = http.calls[-1]
        assert method == "PUT"
        assert "/cards/CARD" in url
        assert data == {"idList": _creds().ready_to_try_list_id}


class TestArchiveCommand:
    def test_archives_card(self, http, capsys):
        code, out = _run(["archive", "CARD"], capsys)
        assert code == 0
        method, url, data = http.calls[-1]
        assert method == "PUT"
        assert "/cards/CARD" in url
        assert data == {"closed": "true"}


class TestDownloadCommand:
    """`trello download` fetches an authenticated Trello attachment (e.g. a
    voice note). Trello attachment URLs require an OAuth ``Authorization``
    header, NOT the query-string key/token the JSON endpoints use — so it has
    its own seam (``_do_download``)."""

    def _patch(self, monkeypatch, payload=b"audio-bytes"):
        calls = []

        def fake(url, headers):
            calls.append((url, headers))
            return payload

        monkeypatch.setattr(cli, "_do_download", fake)
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
        return calls

    def test_creates_parent_directory(self, monkeypatch, tmp_path, capsys):
        self._patch(monkeypatch)
        out = tmp_path / "nested" / "deep" / "clip.opus"
        code, _ = _run(["download", "https://trello.com/x", str(out)], capsys)
        assert code == 0
        assert out.exists()

    def test_network_error_returns_1(self, monkeypatch, tmp_path, capsys):
        def boom(url, headers):
            raise cli.TrelloError("HTTP 404")

        monkeypatch.setattr(cli, "_do_download", boom)
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
        out = tmp_path / "clip.opus"
        code = cli.main(["download", "https://trello.com/x", str(out)])
        assert code == 1
        assert not out.exists()


class TestDownloadAllowlist:
    """The download URL is card-supplied (untrusted), so the OAuth header is
    attached only for known Trello hosts over https, and a non-https scheme is
    rejected — preventing credential exfil / SSRF."""

    def _patch(self, monkeypatch, payload=b"bytes"):
        calls = []

        def fake(url, headers):
            calls.append((url, headers))
            return payload

        monkeypatch.setattr(cli, "_do_download", fake)
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
        return calls

    def test_credentials_attached_for_trello_hosts(self, monkeypatch, tmp_path, capsys):
        calls = self._patch(monkeypatch)
        out = tmp_path / "clip.opus"
        for host in ("trello.com", "api.trello.com", "Trello.Com"):
            url = f"https://{host}/1/cards/C/attachments/A/download/clip.opus"
            code, _ = _run(["download", url, str(out)], capsys)
            assert code == 0
            _, headers = calls[-1]
            assert headers["Authorization"].startswith("OAuth ")
            assert "KEY" in headers["Authorization"]
            assert "TOK" in headers["Authorization"]

    def test_no_credentials_for_foreign_host(self, monkeypatch, tmp_path, capsys):
        calls = self._patch(monkeypatch)
        out = tmp_path / "clip.opus"
        url = "https://evil.example.com/steal"
        code, _ = _run(["download", url, str(out)], capsys)
        assert code == 0
        _, headers = calls[-1]
        assert "Authorization" not in headers
        assert "KEY" not in str(headers) and "TOK" not in str(headers)

    def test_no_credentials_for_trello_subdomain_lookalike(
        self, monkeypatch, tmp_path, capsys
    ):
        calls = self._patch(monkeypatch)
        out = tmp_path / "clip.opus"
        url = "https://trello.com.evil.example.com/steal"
        code, _ = _run(["download", url, str(out)], capsys)
        assert code == 0
        _, headers = calls[-1]
        assert "Authorization" not in headers

    def test_rejects_non_https_scheme(self, monkeypatch, tmp_path, capsys):
        calls = self._patch(monkeypatch)
        out = tmp_path / "clip.opus"
        for url in (
            "http://trello.com/1/cards/C/attachments/A/download/clip.opus",
            "file:///etc/passwd",
            "ftp://trello.com/x",
        ):
            code = cli.main(["download", url, str(out)])
            err = capsys.readouterr().err
            assert code == 1
            assert "https" in err.lower()
        assert calls == []
        assert not out.exists()


class TestDownloadSecureTempPath:
    """The old fixed `/tmp/clip.<ext>` path is symlink-/clobber-prone on a
    shared /tmp. Omitting the output path makes the CLI write to a
    `tempfile.mkstemp` file (owner-only, O_EXCL, unpredictable name) and print
    only that path; an explicit path is hardened to refuse a pre-planted
    symlink (O_NOFOLLOW) and is created mode 0600."""

    def _patch(self, monkeypatch, payload=b"audio-bytes"):
        monkeypatch.setattr(cli, "_do_download", lambda url, headers: payload)
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())

    def test_omitted_path_writes_secure_temp_and_prints_path(self, monkeypatch, capsys):
        self._patch(monkeypatch)
        url = "https://trello.com/1/cards/C/attachments/A/download/note.opus"
        code, out = _run(["download", url], capsys)
        assert code == 0
        path = out.strip()
        assert path
        from pathlib import Path

        p = Path(path)
        try:
            assert p.read_bytes() == b"audio-bytes"
            assert p.name != "note.opus"
            assert p.suffix == ".opus"
            assert (p.stat().st_mode & 0o777) == 0o600
        finally:
            p.unlink()

    def test_omitted_path_with_unsafe_url_suffix_falls_back_to_no_suffix(
        self, monkeypatch, capsys
    ):
        self._patch(monkeypatch)
        url = "https://trello.com/x/clip.opus%00.evilextension"
        code, out = _run(["download", url], capsys)
        assert code == 0
        from pathlib import Path

        p = Path(out.strip())
        try:
            assert p.read_bytes() == b"audio-bytes"
            assert p.suffix == ""
        finally:
            p.unlink()

    def test_explicit_path_is_created_owner_only(self, monkeypatch, tmp_path, capsys):
        self._patch(monkeypatch)
        out = tmp_path / "clip.opus"
        code, _ = _run(["download", "https://trello.com/x", str(out)], capsys)
        assert code == 0
        assert out.read_bytes() == b"audio-bytes"
        assert (out.stat().st_mode & 0o777) == 0o600

    def test_explicit_path_refuses_to_follow_a_symlink(self, monkeypatch, tmp_path):
        self._patch(monkeypatch)
        victim = tmp_path / "victim.txt"
        victim.write_text("precious")
        link = tmp_path / "clip.opus"
        link.symlink_to(victim)
        code = cli.main(["download", "https://trello.com/x", str(link)])
        assert code == 1
        assert victim.read_text() == "precious"

    def test_safe_suffix_helper(self):
        f = cli._safe_suffix
        assert f("https://trello.com/x/clip.opus") == ".opus"
        assert f("https://trello.com/x/AUDIO.M4A") == ".m4a"
        assert f("https://trello.com/x/noext") == ""
        assert f("https://trello.com/x/weird.tar.gz") == ".gz"
        assert f("https://trello.com/x/bad.toolongext") == ""


# --- attach -----------------------------------------------------------


def _parse_multipart(content_type: str, body: bytes) -> dict:
    """Decode a ``multipart/form-data`` body into ``{field_name: part}``.

    Used only by tests, to assert on exactly what a real HTTP client would
    see rather than grepping for substrings in the raw bytes.
    """
    msg = email.message_from_bytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body,
        policy=policy.default,
    )
    fields = {}
    for part in msg.iter_parts():
        cd = part.get("Content-Disposition", "")
        name = re.search(r'name="([^"]*)"', cd).group(1)
        filename_match = re.search(r'filename="([^"]*)"', cd)
        fields[name] = {
            "filename": filename_match.group(1) if filename_match else None,
            "content_type": part.get_content_type(),
            "payload": part.get_payload(decode=True),
        }
    return fields


class TestAttachCommand:
    """`trello attach` uploads a local file as a native Trello attachment.

    Trello gates this write behind an OAuth ``Authorization`` header (the
    query-string key/token used elsewhere is rejected for uploads), so it has
    its own network seam (``_do_upload``), the same way ``download`` has
    ``_do_download``.
    """

    def _patch(self, monkeypatch, response=None):
        calls = []

        def fake(url, headers, body):
            calls.append((url, headers, body))
            return response or {
                "id": "ATT1",
                "name": "file.txt",
                "url": "https://trello.com/1/cards/CARD/attachments/ATT1",
                "mimeType": None,
            }

        monkeypatch.setattr(cli, "_do_upload", fake)
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
        return calls

    def test_uploads_file_with_oauth_header_not_query_string(
        self, monkeypatch, tmp_path, capsys
    ):
        calls = self._patch(monkeypatch)
        f = tmp_path / "index.html"
        f.write_text("<html></html>")
        code, out = _run(["attach", "CARD", str(f)], capsys)
        assert code == 0
        assert "attachments/ATT1" in out
        url, headers, body = calls[-1]
        assert url == f"{cli.TrelloAPI.BASE}/cards/CARD/attachments"
        assert "key=" not in url and "token=" not in url
        auth = headers.get("Authorization", "")
        assert "OAuth" in auth and "KEY" in auth and "TOK" in auth

    def test_multipart_body_is_well_formed(self, monkeypatch, tmp_path, capsys):
        calls = self._patch(monkeypatch)
        f = tmp_path / "index.html"
        f.write_text("<html>hi</html>")
        code, _ = _run(["attach", "CARD", str(f)], capsys)
        assert code == 0
        _, headers, body = calls[-1]
        fields = _parse_multipart(headers["Content-Type"], body)
        assert fields["name"]["payload"] == b"index.html"
        assert fields["file"]["filename"] == "index.html"
        assert fields["file"]["payload"] == b"<html>hi</html>"

    def test_explicit_name_overrides_basename(self, monkeypatch, tmp_path, capsys):
        calls = self._patch(monkeypatch)
        f = tmp_path / "index.html"
        f.write_text("data")
        code, _ = _run(["attach", "CARD", str(f), "--name", "renamed.html"], capsys)
        assert code == 0
        _, headers, body = calls[-1]
        fields = _parse_multipart(headers["Content-Type"], body)
        assert fields["name"]["payload"] == b"renamed.html"
        assert fields["file"]["filename"] == "renamed.html"

    def test_explicit_mime_sent_as_its_own_form_field(
        self, monkeypatch, tmp_path, capsys
    ):
        # Card note: Trello returned `mimeType: null` for a text/html upload
        # even with the file part's Content-Type header set — an explicit
        # `mimeType` form field is what actually needs to carry it.
        calls = self._patch(monkeypatch)
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01")
        code, _ = _run(["attach", "CARD", str(f), "--mime", "text/html"], capsys)
        assert code == 0
        _, headers, body = calls[-1]
        fields = _parse_multipart(headers["Content-Type"], body)
        assert fields["mimeType"]["payload"] == b"text/html"
        assert fields["file"]["content_type"] == "text/html"

    def test_no_mime_flag_omits_mime_type_field(self, monkeypatch, tmp_path, capsys):
        calls = self._patch(monkeypatch)
        f = tmp_path / "plain.txt"
        f.write_text("hi")
        code, _ = _run(["attach", "CARD", str(f)], capsys)
        assert code == 0
        _, headers, body = calls[-1]
        fields = _parse_multipart(headers["Content-Type"], body)
        assert "mimeType" not in fields

    def test_missing_file_returns_1(self, monkeypatch, tmp_path, capsys):
        self._patch(monkeypatch)
        code = cli.main(["attach", "CARD", str(tmp_path / "nope.txt")])
        err = capsys.readouterr().err
        assert code == 1
        assert "nope.txt" in err


class TestAttachSanitization:
    """``name``/``mime`` are caller-supplied and end up embedded in raw
    multipart header lines, so they are sanitised against directory
    components and CR/LF/quote header-injection."""

    def test_safe_filename_strips_directory_components(self):
        assert cli._safe_filename("../../etc/passwd") == "passwd"
        assert cli._safe_filename("/abs/path/report.pdf") == "report.pdf"

    def test_safe_filename_strips_header_injection_chars(self):
        result = cli._safe_filename('evil"\r\nX-Injected: yes')
        assert "\r" not in result
        assert "\n" not in result
        assert '"' not in result

    def test_safe_filename_falls_back_when_empty(self):
        assert cli._safe_filename("") == "file"
        assert cli._safe_filename("   ") == "file"

    def test_safe_mime_strips_crlf(self):
        result = cli._safe_mime("text/html\r\nX-Injected: yes")
        assert "\r" not in result
        assert "\n" not in result

    def test_explicit_name_with_injection_chars_is_sanitized_end_to_end(
        self, monkeypatch, tmp_path, capsys
    ):
        calls = []

        def fake(url, headers, body):
            calls.append((url, headers, body))
            return {"id": "ATT1", "url": "https://trello.com/x"}

        monkeypatch.setattr(cli, "_do_upload", fake)
        monkeypatch.setattr(cli, "load_creds", lambda *a, **k: _creds())
        f = tmp_path / "f.txt"
        f.write_text("x")
        code, _ = _run(
            ["attach", "CARD", str(f), "--name", 'evil"\r\nX-Injected: yes'], capsys
        )
        assert code == 0
        _, headers, body = calls[-1]
        # A raw CR/LF in the sanitized name must not have split the header
        # into an extra line the multipart parser would choke on.
        fields = _parse_multipart(headers["Content-Type"], body)
        assert "file" in fields and "name" in fields
