# Casey: Slack IT Support Agent (Bolt for Python + OpenCode)

Casey is an AI IT-support agent that lives in Slack. Users describe a problem in a DM, an `@mention`, or the App Home, and Casey troubleshoots in a thread, remembering context across follow-ups.

Casey is built with [Bolt for Python](https://docs.slack.dev/tools/bolt-python/). The model side is delegated to a local [OpenCode server](https://opencode.ai/docs/server/), so Casey works with whichever model provider your OpenCode instance is configured for (Anthropic, OpenAI, Bedrock, Ollama, ...) and never handles a model API key itself.

Casey also has an optional **operator mode**: an allowlisted Slack user can turn a thread into a remote shell/coding agent for the machine Casey runs on, with per-action Approve/Deny buttons and an audit log. See [Operator mode](#operator-mode).

## What Casey does

Four entry points:

* **App Home** — Users open the Home tab and pick an issue category (Password Reset, Access Request, Software Help, Network Issues, Something Else). A modal collects details, then Casey opens a DM thread with next steps.
* **Direct Messages** — Users describe any IT issue. Casey reacts with :eyes:, replies in a thread, and keeps the thread's history for follow-ups.
* **Channel @mentions** — `@Casey` in any channel it has been invited to; Casey answers in a thread.
* **Assistant panel** — Click _Add Agent_ in Slack, select Casey, and pick a suggested prompt.

Every reply carries thumbs up/down feedback buttons.

> **Note:** The helpdesk persona has **no tools**. It gives troubleshooting guidance only and is instructed never to invent ticket numbers or system statuses. Wiring real ticketing, directory, or status systems is up to you.

### Slack MCP Server

The App Home shows whether the user has connected the [Slack MCP Server](https://docs.slack.dev/ai/slack-mcp-server) via OAuth (HTTP mode, `app_oauth.py`). This fork does **not** yet pass the user token to the model, so the badge is informational; see the Slack docs for how an agent can consume the MCP server.

## Architecture

```
Slack  <-- Socket Mode -->  Casey (Bolt, app.py)  <-- HTTP 127.0.0.1:4096 -->  opencode serve  -->  model provider
                                                                              ^
                                                              credentials file (sourced only here)
```

* `scripts/dev.sh` supervises both processes: it sources your credentials file into `opencode serve`, restarts it when the file changes, and runs `slack run` next to it.
* One OpenCode session per Slack thread. Sessions persist on disk, so threads survive restarts.

## Prerequisites

1. A Slack workspace where you can install apps. The [Slack Developer Program](https://api.slack.com/developer-program) gives you a free sandbox.
2. **Python 3.10+**.
3. **[Slack CLI](https://docs.slack.dev/tools/slack-cli/)** ([macOS & Linux](https://docs.slack.dev/tools/slack-cli/guides/installing-the-slack-cli-for-mac-and-linux/), [Windows](https://docs.slack.dev/tools/slack-cli/guides/installing-the-slack-cli-for-windows/)), then `slack login`. The CLI creates the app from `manifest.json` and injects the Slack tokens at run time, so you never copy a bot token by hand.
4. **[OpenCode](https://opencode.ai/docs/)** on the same machine:

   ```sh
   curl -fsSL https://opencode.ai/install | bash
   opencode --version
   ```

5. **Model provider credentials** for OpenCode, as environment variables. Check the provider's variable names with `opencode models` / the [OpenCode providers docs](https://opencode.ai/docs/providers/).

## Quickstart

```sh
# 1. Get the code
git clone https://github.com/EikanWang/slack-support-agent.git casey
cd casey

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate
pip install -e ".[dev]"

# 3. Create a credentials file for the model provider, OUTSIDE the repo
cat > ~/casey-credentials.sh <<'EOF'
export ANTHROPIC_API_KEY=...     # or OPENAI_API_KEY, AWS_* for Bedrock, etc.
EOF
chmod 600 ~/casey-credentials.sh

# 4. Pick a model id
source ~/casey-credentials.sh && opencode models   # e.g. anthropic/claude-sonnet-4-5

# 5. Create the Slack app from manifest.json (one time) and note its app ID
slack install                    # choose your workspace; creates .slack/apps.dev.json
slack app list                   # shows the App ID (A0...)

# 6. Configure
cp .env.sample .env
#    set OPENCODE_MODEL, CASEY_CREDENTIALS_FILE, SLACK_APP_ID

# 7. Run
scripts/dev.sh up
scripts/dev.sh status
```

Open Slack, find Casey under _Apps_, and send it a DM.

## Configuration

Everything deployment-specific lives in one gitignored file, `.env`. Nothing in the repository identifies a workspace, machine, model provider, or credential.

| Variable | Required | Purpose |
|---|---|---|
| `OPENCODE_BASE_URL` | yes | The local [OpenCode server](https://opencode.ai/docs/server/). Keep it on `127.0.0.1`. |
| `OPENCODE_MODEL` | yes | `<provider>/<model>` as printed by `opencode models` |
| `CASEY_CREDENTIALS_FILE` | yes (dev.sh) | Absolute path to a shell file that `export`s the provider credentials. Sourced only into the `opencode serve` process; keep it outside the repo. |
| `SLACK_APP_ID` | yes (dev.sh) | Your app's ID (`A0...`) from `slack app list` or App Settings |
| `OPENCODE_PORT`, `OPENCODE_BIN`, `SLACK_BIN` | no | Port and binaries used by `scripts/dev.sh` |
| `SLACK_RUN_FLAGS` | no | Extra flags for `slack run`, e.g. `--org-workspace-grant=all` on Enterprise Grid |
| `LOG_LEVEL` | no | Python log level, default `INFO` |
| `CASEY_OPERATOR_*` | no | Operator mode, see below |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | only without the CLI | Needed when running `python3 app.py` directly |
| `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`, `SLACK_REDIRECT_URI` | only for OAuth | Needed by `app_oauth.py` |

`.env.sample` documents every variable with its default.

Casey itself never sees a model API key: the OpenCode server owns the provider and its credentials.

### Switching models from Slack

`OPENCODE_MODEL` is the default, but any thread can switch to another model the OpenCode server has a provider for. Send these in the thread (or DM) instead of a question:

| Command | Effect |
|---|---|
| `model` | Show the model this thread is using |
| `model list` | List every available model |
| `model list haiku 5` | List models matching all of those words |
| `model openai/gpt-5` | Switch to an exact `provider/model` |
| `model haiku` | Switch by substring, as long as it matches exactly one model |
| `model reset` | Go back to `OPENCODE_MODEL` |

The switch applies to the **current thread only** and lasts as long as the thread's conversation history (24h by default). New threads start on the default again. The OpenCode session is not recreated, so **the thread keeps its full history across a switch** — useful for escalating a stuck conversation to a stronger model mid-thread.

An ambiguous query is rejected with the list of candidates rather than guessing. The catalogue is cached for 5 minutes.

To migrate to another machine, copy `.env`, the credentials file it points to, and `.slack/apps.dev.json` (your local Slack app link, also gitignored), then run `scripts/dev.sh up`.

### Rotating credentials

`opencode serve` reads credentials once at startup, so a refreshed token needs a server restart. `scripts/dev.sh` handles this: it watches the credentials file and restarts only the OpenCode server when the file changes, while the Slack connection stays up and in-flight requests retry until the server is back.

To rotate: edit the credentials file (or `scripts/dev.sh restart` to force a re-read). Casey's threads keep their history because OpenCode sessions persist on disk across restarts.

If a token has already expired, Casey replies with a `:warning:` message naming the credentials problem instead of a generic error.

## Operator mode

Operator mode turns a Slack thread into a remote coding/shell agent for the machine Casey runs on. It is **off by default** and, when on, is restricted to the Slack user IDs you list. Everyone else keeps talking to the helpdesk persona.

```sh
CASEY_OPERATOR_USER_IDS=U0XXXXXXXXX    # your Slack member ID: profile > "..." > Copy member ID
CASEY_OPERATOR_ROOT=/home/you          # or / for the whole filesystem
```

### What an operator can do

- Ask for anything OpenCode can do with its tools: read and search files, run shell commands, edit files.
- `cd <path>` and `pwd` are handled by Casey directly (no model call). `cd` is confined to `CASEY_OPERATOR_ROOT` (which may be `/`); threads start in `CASEY_OPERATOR_HOME`, and each `cd` starts a fresh OpenCode session rooted there.
- Every reply ends with a `Tools:` list of what ran, with a tick or a cross.

### Safety model

| Layer | Enforced by | What it does |
|---|---|---|
| Who | Python, before any model call | Only `CASEY_OPERATOR_USER_IDS` reach operator mode; only they can press Approve/Deny |
| Where | Python (`cd`) + OpenCode `external_directory` rules | Nothing outside `CASEY_OPERATOR_ROOT` |
| Protected paths | OpenCode permission rules, per tool | By default `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config`, `~/.slack`, `~/.opencode`, `~/.env`, `~/.git-credentials`, `~/.netrc`, `/etc/shadow`, `/etc/gshadow`, `/etc/sudoers*`, `/etc/ssh`, `/root`, and the credentials file are denied to `read`, `glob`, `grep`, `list`, `edit`, `write`, and to any `bash` command that mentions them. Override with `CASEY_OPERATOR_DENY`. |
| Side effects | OpenCode `ask` + Slack buttons | `bash`, `edit`, `write`, `webfetch`, `task` pause until the operator clicks **Approve**, **Always**, or **Deny** in the thread. Reads run immediately. |
| Audit | `logs/operator-audit.log` | JSON line for every request, decision, and rejected click |

**Approve** allows the single request. **Always** allows it and remembers the pattern OpenCode suggests (shown on the card, e.g. `git status*` or a file path) for the rest of that thread's session, so repetitive commands stop prompting; the memory is dropped when the thread's session ends or you `cd`. **Deny** stops the turn.

A denied action stops the turn; Casey asks how to proceed rather than retrying. A turn that waits longer than `CASEY_OPERATOR_TURN_TIMEOUT` (default 30 min) is aborted.

Things this does **not** protect against: an approved shell command can do anything your user can (the approval card shows the exact command, read it); the deny list is a glob match, so a protected file copied elsewhere is no longer protected; the audit log and the Slack thread contain the full text of every approved command, so do not paste secrets into them; and the OpenCode server itself trusts anything on localhost, so keep `OPENCODE_BASE_URL` on `127.0.0.1`.

## Running

<details open><summary><strong>Dev supervisor (recommended)</strong></summary>

```sh
scripts/dev.sh up       # start opencode serve (credential-watching) + slack run
scripts/dev.sh status
scripts/dev.sh logs
scripts/dev.sh restart  # force opencode serve to re-read credentials
scripts/dev.sh down
```

`status` reports the OpenCode server, `slack run`, and the Bolt app separately. If it ever shows more than one app copy, a previous run was orphaned and both copies are answering Slack with their own `.env`; `down` then `up` clears it.

Logs and pid files live in `.run/` (gitignored).
</details>

<details><summary><strong>Slack CLI only</strong></summary>

Start an OpenCode server with the provider credentials in its environment, then run the app with the CLI:

```sh
(source ~/casey-credentials.sh && opencode serve --port 4096 --hostname 127.0.0.1) &
slack run
```
</details>

<details><summary><strong>Plain Python (no Slack CLI)</strong></summary>

Create the app at [api.slack.com/apps/new](https://api.slack.com/apps/new) from `manifest.json`, install it, then put the two tokens in `.env`:

- `SLACK_BOT_TOKEN` — _OAuth & Permissions_ > _Bot User OAuth Token_ (`xoxb-...`)
- `SLACK_APP_TOKEN` — _Basic Information_ > _App-Level Tokens_, with the `connections:write` scope (`xapp-...`)

```sh
(source ~/casey-credentials.sh && opencode serve --port 4096 --hostname 127.0.0.1) &
python3 app.py
```
</details>

<details><summary><strong>OAuth HTTP server (with ngrok)</strong></summary>

This mode uses an HTTP server instead of Socket Mode, which is required for OAuth-based distribution and for the Slack MCP Server badge in the App Home.

1. Install [ngrok](https://ngrok.com/download) and start a tunnel: `ngrok http 3000`
2. Copy the `https://*.ngrok-free.app` URL from the ngrok output.
3. In `manifest.json`, set `socket_mode_enabled` to `false` and replace `ngrok-free.app` with your ngrok domain.
4. Create the app: `slack install -E local` (or via App Settings from the manifest).
5. In App Settings (`slack app settings`), copy **Client ID**, **Client Secret**, and **Signing Secret** into `.env`, and set `SLACK_REDIRECT_URI`:

   ```sh
   SLACK_CLIENT_ID=YOUR_CLIENT_ID
   SLACK_CLIENT_SECRET=YOUR_CLIENT_SECRET
   SLACK_SIGNING_SECRET=YOUR_SIGNING_SECRET
   SLACK_REDIRECT_URI=https://YOUR_NGROK_SUBDOMAIN.ngrok-free.app/slack/oauth_redirect
   ```

6. Start: `slack run app_oauth.py` (or `python3 app_oauth.py`).
7. Open the install URL printed in the terminal to install via OAuth.

> **Note:** Each time ngrok restarts it generates a new URL. Update `manifest.json`, `SLACK_REDIRECT_URI`, and re-install the app.
</details>

## Using the app

**App Home** — Open Casey in Slack and click the _Home_ tab. Click a category, describe your issue in the modal, and submit. Casey starts a DM thread with you.

**Direct Messages** — Open a DM with Casey. Pick a suggested prompt (_Reset Password_, _Request Access_, _Network Issues_) or describe your own issue. Follow up in the same thread and Casey keeps the context. Send `model` to see or change which model answers in that thread.

**Channel @mentions** — `/invite @Casey` to a channel, then `@Casey <your issue>`. Casey answers in a thread.

**Assistant panel** — Click _Add Agent_ in the top-right corner of Slack, select Casey, then pick a suggested prompt or type a message.

**Operator** (allowlisted users only) — DM Casey with `pwd`, `cd some/dir`, or any request such as "show me the failing test and fix it". Approve, Always-approve, or deny each side-effecting action from the buttons in the thread.

## Development

```sh
ruff check .
ruff format --check .
pytest
```

CI runs the same three commands on every PR.

## Project structure

| Path | Purpose |
|---|---|
| `manifest.json` | Slack app manifest: scopes, events, Socket Mode, App Home and agent settings |
| `app.py` | Socket Mode entry point. Thin: loads `.env`, builds the Bolt app, registers listeners |
| `app_oauth.py` | HTTP/OAuth entry point for distributed installs |
| `scripts/dev.sh` | Local supervisor for `opencode serve` + `slack run`, with credential-file watching |
| `agent/opencode_client.py` | Small HTTP client for the OpenCode server: sessions, prompts, permission replies, event stream |
| `agent/support_agent.py` | Helpdesk persona: system prompt, one OpenCode session per thread, tools disabled |
| `agent/operator.py`, `agent/operator_policy.py`, `agent/approvals.py` | Operator mode: allowlist and directory sandbox, OpenCode permission ruleset, `cd`/`pwd`, Slack approval cards wired to OpenCode permission events |
| `agent/models.py` | The `model` command: catalogue lookup, fuzzy resolution, and the Slack replies |
| `agent/router.py`, `agent/deps.py` | Intercept the `model` command, pick helpdesk vs operator per Slack user, carry the Slack client and thread context into the agent |
| `listeners/events/` | `app_home_opened.py` (Home tab + suggested prompts), `app_mentioned.py`, `message.py` |
| `listeners/actions/` | `issue_buttons.py` (category buttons), `feedback_buttons.py`, `approval_buttons.py` |
| `listeners/views/` | `issue_modal.py` (submission handler), `app_home_builder.py`, `issue_modal_builder.py`, `feedback_builder.py` (Block Kit builders) |
| `thread_context/store.py` | Thread-safe in-memory conversation history keyed by channel and thread |
| `tests/` | pytest suite (no network; OpenCode and Slack are faked) |

## Troubleshooting

**`CASEY_CREDENTIALS_FILE is unset or not readable`** — `scripts/dev.sh` needs the absolute path to your credentials file in `.env`.

**Casey replies with a `:warning:` about credentials** — the provider rejected the token. Refresh the credentials file; the supervisor restarts `opencode serve` automatically within a few seconds.

**`opencode serve did not become healthy`** — run `scripts/dev.sh logs` and check `.run/opencode-serve.log`. Usually a wrong `OPENCODE_MODEL` or missing provider variable.

**Casey does not answer DMs** — make sure `slack run` is up (`scripts/dev.sh status`) and that the app's _Messages_ tab is enabled (it is, in `manifest.json`).

**Casey answers twice, or uses a model you already changed** — `scripts/dev.sh status` will show two app copies. An earlier `slack run` died without reaping its Bolt child, so an old process is still handling events with the `.env` it started with. `scripts/dev.sh down` reaps strays; `up` sweeps before starting.

**`OpenCode has no model ... configured`** — `OPENCODE_MODEL` names a provider or model the server does not have, usually after editing `opencode.json`. The message lists the same model id under the providers that do offer it; `model list` shows everything.

**MCP Server connection error `HTTP error 400 (Bad Request)`** — the Slack MCP feature is not enabled for your app. Run `slack app settings`, open **Agents & AI Apps**, and toggle **Slack Model Context Protocol** on.

## License

MIT. Based on Slack's [bolt-python-support-agent](https://github.com/slack-samples/bolt-python-support-agent) sample; see [LICENSE](./LICENSE).
