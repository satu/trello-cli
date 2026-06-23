# trello-cli

A standalone, **stdlib-only** Trello command-line client, installed as the
`trello` console script. It is being factored out of
[trellm](https://github.com/satu/trellm)'s in-repo `trello_cli.py` so that both
trellm and [humphrey](https://github.com/satu/humphrey) can depend on one shared
tool instead of each carrying its own Trello integration (humphrey today uses a
heavyweight third-party Trello MCP server).

> **Status: scaffold (chain card 1/4).** This repo currently contains packaging
> + the `trello` entry point only — the CLI is a stub. The actual client logic
> is ported from trellm in card 2/4.

## The chain

This repo is milestone 1 of a four-card chain:

1. **[1/4] scaffold** — stand up this repo, packaging, and the `trello` entry
   point; register the `trellocli` project in trellm. *(this card)*
2. **[2/4] port** — port trellm's `trello_cli.py` client logic here.
3. **[3/4] trellm switch** — point trellm at this package.
4. **[4/4] humphrey migrate** — replace humphrey's third-party Trello MCP with
   this CLI.

Background and tradeoffs:
`docs/proposals/trello-integration-cli-vs-mcp.md` in the humphrey repo.

## Install

Python 3.10+. Uses the same tooling as trellm (hatchling + a `.venv`):

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

This puts a `trello` console script on the venv's `PATH`.

## Usage

```bash
trello --version
trello                 # stub — prints a "not implemented yet" notice for now
```

The planned command surface (ported in card 2/4) mirrors trellm's CLI:

```
trello lists                       # all lists on the board
trello cards <list>                # cards in a list
trello card <card_id>              # one card incl. attachments
trello comments <card_id>          # comments, oldest-first
trello comment <card_id> "<text>"  # add a comment
trello create <list> "<name>" [--desc "<body>"]
trello move <card_id> <list>
trello archive <card_id>
trello download <url> [<path>]     # authenticated attachment download
```

It reads Trello credentials and well-known list ids from
`~/.trellm/config.yaml` (environment variables override), the same config
trellm uses.

## Development

```bash
.venv/bin/pytest
```

## License

MIT — see [LICENSE](LICENSE).
