---
name: trello
description: Read and write a Trello board (lists, cards, comments, create/move/archive, attachment downloads) through the self-contained `trello` CLI. Use for anything Trello — reading a card and its comments before working, acknowledging/finishing a card, filing a backlog item, or downloading a voice-note attachment. Credentials and ids come from the environment, a config file, or flags, so it works against any board.
---

# trello

A small, **dependency-light** command-line client for Trello, installed as the
`trello` console script. It talks straight to `https://api.trello.com/1` over
stdlib `urllib` (PyYAML is used only to read a YAML config file), so it never
breaks because an external MCP server or repo went missing. **Do not** reach for
an `mcp__trello__*` tool or hand-roll raw `curl` calls — use this CLI.

If `trello` isn't on PATH, run it as `python -m trello_cli …` from the install
environment.

## Configuration (deployment-agnostic)

Nothing about a particular board is baked in. The client reads, in precedence
order **environment > config file > secret command**:

| Setting        | Env var                              | Config key             |
| -------------- | ------------------------------------ | ---------------------- |
| API key        | `TRELLO_API_KEY`                     | `api_key`              |
| API token      | `TRELLO_TOKEN` (or `TRELLO_API_TOKEN`) | `api_token` / `token` |
| Board id       | `TRELLO_BOARD_ID` (or `--board-id`)  | `board_id`             |
| TODO list      | `TRELLO_TODO_LIST_ID`                | `todo_list_id`         |
| READY list     | `TRELLO_READY_LIST_ID`               | `ready_to_try_list_id` |
| ICEBOX list    | `TRELLO_ICEBOX_LIST_ID`              | `icebox_list_id`       |

A config file is found via `--config`, then `$TRELLO_CONFIG`, then the first
existing of `~/.config/trello-cli/config.yaml`, `~/.trello-cli.yaml`,
`~/.trellm/config.yaml`. It may nest the settings under a `trello:` section
(trellm's layout) or list them flat. To keep the token out of plaintext, set
`TRELLO_TOKEN_CMD` / `api_token_cmd` to a command whose stdout is the secret,
e.g. `pass show trello/token`.

Example `~/.config/trello-cli/config.yaml`:

```yaml
api_key: <your key>
api_token_cmd: pass show trello/token   # or: api_token: <your token>
board_id: <24-hex board id>
todo_list_id: <24-hex>
ready_to_try_list_id: <24-hex>
icebox_list_id: <24-hex>
```

## Commands

```bash
trello lists                       # all lists on the board (id + name)
trello cards <list>                # cards in a list (JSON: id, name, desc, url, ...)
trello card <card_id>              # one card incl. attachments (voice notes)
trello comments <card_id>          # comments, OLDEST-FIRST (id, date, member, text)
trello comment <card_id> "<text>"  # add a comment
trello create <list> "<name>" [--desc "<body>"]   # create a card
trello move <card_id> <list>       # move a card to another list
trello archive <card_id>           # archive (close) a card
trello download <url> [<path>]     # download an authenticated attachment; omit <path> for a secure temp file (prints its path)
```

`<list>` is a **list reference**: a 24-char list id, one of the aliases
`todo` / `ready` / `icebox` (resolved from the config/env list ids), or any
exact list name on the board (case-insensitive, e.g. `"READY TO TRY"`,
`parked`). Run `trello lists` if unsure.

All read commands print JSON to stdout — pipe to `python -c …` / `jq` to
extract fields. Mutations print a one-line confirmation.

## Common flows

Acknowledge + work + finish a card (the standard harness loop):

```bash
trello comment <card_id> "Claude: Starting work on this."
# ... do the work, commit, push ...
trello comment <card_id> "Claude: Done — <summary>. <commit link>"
trello move <card_id> ready          # READY TO TRY
```

File a backlog item to ICE BOX:

```bash
trello create icebox "<concise title>" --desc "<structured body>"
```

Read a card and all its feedback before starting:

```bash
trello card <card_id>
trello comments <card_id>     # look for non-"Claude:" comments after your last one
```

Download a voice-note attachment, then transcribe it:

```bash
trello card <card_id>                          # read the attachment URL(s)
clip="$(trello download "<attachment_url>")"   # secure temp file; prints its path
transcribe "$clip"                             # transcribe; prints the transcript
```

Omit the output path (as above) so the CLI writes the attachment to a **secure
temp file** — owner-only, with an unpredictable name — and prints that path on
stdout for you to capture. This avoids a predictable `/tmp/clip.<ext>` another
local user could pre-plant as a symlink. You may still pass an explicit path if
you need one.

## Security notes

- `trello download` requires an OAuth `Authorization` header (the query-string
  key/token used by the JSON endpoints returns the HTML page, not the file); the
  CLI handles that for you.
- The download URL is treated as **untrusted** (it comes from a card
  attachment): only `https` is allowed, and the OAuth credentials are attached
  **only** when the host is a known Trello host (`trello.com` / `api.trello.com`).
  A foreign host (e.g. a pre-signed S3 link, or a malicious attachment) is
  fetched uncredentialed, so the token can never leak to it.
- Card/list/board ids are 24 hex chars; the CLI passes those through untouched,
  and anything else is treated as an alias or list name.
