"""Scaffold-level tests for the stub `trello` CLI.

These pin the packaging contract that card 2/4 (the real client port) will
build on: the package imports, exposes a version, and the `main()` entry point
behind the `trello` console script runs without spawning a real Trello call.
"""

import subprocess
import sys

import trello_cli
from trello_cli.__main__ import main


def test_package_exposes_version():
    assert isinstance(trello_cli.__version__, str)
    assert trello_cli.__version__


def test_main_version_flag(capsys):
    code = main(["--version"])
    out = capsys.readouterr().out
    assert code == 0
    assert trello_cli.__version__ in out


def test_main_no_args_is_a_clean_stub(capsys):
    # The real subcommands land in card 2/4; for now the stub must exit
    # cleanly and say so rather than error out.
    code = main([])
    out = capsys.readouterr().out
    assert code == 0
    assert "not implemented yet" in out.lower()


def test_console_script_entry_point_resolves():
    # The `trello` console script maps to trello_cli.__main__:main — invoking
    # the module the same way the entry point does must succeed.
    result = subprocess.run(
        [sys.executable, "-m", "trello_cli", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert trello_cli.__version__ in result.stdout
