# trello-cli

A standalone, **dependency-light** Trello command-line client, installed as the
`trello` console script. It was factored out of
[trellm](https://github.com/satu/trellm)'s in-repo `trello_cli.py` so that both
trellm and [humphrey](https://github.com/satu/humphrey) can depend on one shared
tool instead of each carrying its own Trello integration (humphrey today uses a
heavyweight third-party Trello MCP server).

The client itself is stdlib-only (`urllib` + `json`); PyYAML is the sole runtime
dependency and is imported lazily — only when a YAML config file is actually
read — so pure-environment usage needs no third-party packages.

## The chain

This repo is part of a four-card chain:

1. **[1/4] scaffold** — stand up this repo, packaging, and the `trello` entry
   point; register the `trellocli` project in trellm.
2. **[2/4] port** — port trellm's `trello_cli.py` client logic + the `/trello`
   skill here, deployment-agnostic. *(this card)*
3. **[3/4] trellm switch** — point trellm at this package.
4. **[4/4] humphrey migrate** — replace humphrey's third-party Trello MCP with
   this CLI.

## Install

Python 3.10+:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"   # or: uv tool install .
```

This puts a `trello` console script on the `PATH`.

## Configuration

Nothing about any particular board is baked in. Settings are read in precedence
order **environment > config file > secret command**:

| Setting     | Env var                                | Config key             |
| ----------- | -------------------------------------- | ---------------------- |
| API key     | `TRELLO_API_KEY`                       | `api_key`              |
| API token   | `TRELLO_TOKEN` (or `TRELLO_API_TOKEN`) | `api_token` / `token`  |
| Board id    | `TRELLO_BOARD_ID` (or `--board-id`)    | `board_id`             |
| TODO list   | `TRELLO_TODO_LIST_ID`                  | `todo_list_id`         |
| READY list  | `TRELLO_READY_LIST_ID`                 | `ready_to_try_list_id` |
| ICEBOX list | `TRELLO_ICEBOX_LIST_ID`               | `icebox_list_id`       |

A config file is located via `--config`, then `$TRELLO_CONFIG`, then the first
existing of `~/.config/trello-cli/config.yaml`, `~/.trello-cli.yaml`,
`~/.trellm/config.yaml` (the last for back-compat with trellm). It may nest the
settings under a `trello:` section or list them flat:

```yaml
api_key: <your key>
api_token_cmd: pass show trello/token   # or: api_token: <your token>
board_id: <24-hex board id>
todo_list_id: <24-hex>
ready_to_try_list_id: <24-hex>
icebox_list_id: <24-hex>
```

To keep the token out of a plaintext file, point `TRELLO_TOKEN_CMD` /
`api_token_cmd` (and `TRELLO_API_KEY_CMD` / `api_key_cmd`) at a command whose
stdout is the secret, e.g. `pass show trello/token`.

## Usage

```bash
trello --version
trello lists                       # all lists on the board
trello cards <list>                # cards in a list
trello card <card_id>              # one card incl. attachments
trello comments <card_id>          # comments, oldest-first
trello comment <card_id> "<text>"  # add a comment
trello create <list> "<name>" [--desc "<body>"]
trello move <card_id> <list>
trello archive <card_id>
trello download <url> [<path>]     # authenticated attachment download
trello attach <card_id> <path> [--name NAME] [--mime MIME]  # upload a file as an attachment
```

`<list>` is a list id, one of the aliases `todo` / `ready` / `icebox`, or any
exact list name on the board (case-insensitive). Read commands print JSON;
mutations print a one-line confirmation.

## The skill

`skills/trello/SKILL.md` documents the agent workflow (acknowledge → work →
finish a card, file a backlog item, transcribe a voice note). It ships inside
the wheel so an install lands the workflow, not just the binary.

## Development

```bash
.venv/bin/pytest
```

## License

MIT — see [LICENSE](LICENSE).
