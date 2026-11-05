# Casey: IT Support Agent (Bolt for Python and OpenAI Agents SDK)

Meet Casey (it/this/that) — an AI-powered IT support agent that lives in Slack. Casey can troubleshoot common issues, search knowledge base articles, reset passwords, check system status, and create support tickets, all without leaving the conversation.

Built with [Bolt for Python](https://docs.slack.dev/tools/bolt-python/) and the [OpenCode server API](https://opencode.ai/docs/server/), using whichever model provider your OpenCode instance is configured for.

## App Overview

Casey gives your team instant IT support through four entry points:

* **App Home** — Users open Casey's Home tab and choose from common issue categories (Password Reset, Access Request, Software Help, Network Issues, Something Else). A modal collects details, then Casey starts a DM thread with a resolution.
* **Direct Messages** — Users message Casey directly to describe any IT issue. Casey responds in-thread, maintaining context across follow-ups.
* **Channel @mentions** — Users mention `@Casey` in any channel to get help without leaving the conversation.
* **Assistant Panel** — Users click _Add Agent_ in Slack, select Casey, and pick from suggested prompts or describe an issue.

Casey uses five simulated tools to assist users:

* **Knowledge Base Search** — Finds relevant articles for common topics like VPN, email, Wi-Fi, printers, and more.
* **Support Ticket Creation** — Creates a tracked ticket when issues need human follow-up.
* **Password Reset** — Triggers a password reset and confirms the action.
* **System Status Check** — Reports the operational status of company systems.
* **User Permissions Lookup** — Shows access levels and group memberships.

> **Note:** All tools return simulated data for demonstration purposes. In a production app, these would connect to your actual IT systems.

### Slack MCP Server

Casey also works with the [Slack MCP Server](https://docs.slack.dev/ai/slack-mcp-server), giving it the ability to search messages and files, read channel history and threads, send messages, schedule messages, and create or update Slack canvases. When deployed with OAuth (HTTP mode), Casey automatically connects to the Slack MCP Server using the user's token, unlocking these capabilities on top of the built-in IT tools.

## Setup

Before getting started, make sure you have a development workspace where you have permissions to install apps.

### Developer Program

Join the [Slack Developer Program](https://api.slack.com/developer-program) for exclusive access to sandbox environments for building and testing your apps, tooling, and resources created to help you build and grow.

### Claude Code, Codex, Cursor, and Other Coding Agents

Install the [Slack MCP and Skills Plugin](https://docs.slack.dev/ai/slack-skills-plugin/) so your coding agent can create, run, and deploy Slack apps, look up Web API methods and scopes, and build Block Kit without guessing.

#### Claude Code

Run this in a Claude Code session:

```
/plugin install slack@claude-plugins-official
```

#### Codex

Run this in your terminal:

```sh
codex plugin marketplace add slackapi/slack-skills-plugin
codex plugin add slack@slack
```

#### Cursor

Run this in a Cursor Agent chat:

```
/add-plugin slack
```

#### Other agents

Install the skills with `npx skills`, using your [agent's identifier](https://github.com/vercel-labs/skills#supported-agents):

```sh
npx skills add slackapi/slack-skills-plugin -y -a <agent>

# For example, OpenCode
npx skills add slackapi/slack-skills-plugin -y -a opencode
```

### Create the Slack app

<details><summary><strong>Using Slack CLI</strong></summary>

Install the latest version of the Slack CLI for your operating system:

- [Slack CLI for macOS & Linux](https://docs.slack.dev/tools/slack-cli/guides/installing-the-slack-cli-for-mac-and-linux/)
- [Slack CLI for Windows](https://docs.slack.dev/tools/slack-cli/guides/installing-the-slack-cli-for-windows/)

You'll also need to log in if this is your first time using the Slack CLI.

```sh
slack login
```

#### Initializing the project

```sh
slack create my-casey-agent --template slack-samples/bolt-python-support-agent --subdir openai-agents-sdk
cd my-casey-agent
```

</details>

<details><summary><strong>Using App Settings</strong></summary>

#### Create Your Slack App

1. Open [https://api.slack.com/apps/new](https://api.slack.com/apps/new) and choose "From an app manifest"
2. Choose the workspace you want to install the application to
3. Copy the contents of [manifest.json](./manifest.json) into the text box that says `*Paste your manifest code here*` (within the JSON tab) and click _Next_
4. Review the configuration and click _Create_
5. Click _Install to Workspace_ and _Allow_ on the screen that follows. You'll then be redirected to the App Configuration dashboard.

#### Environment Variables

Before you can run the app, you'll need to store some environment variables.

1. Rename `.env.sample` to `.env`.
2. Open your apps setting page from [this list](https://api.slack.com/apps), click _OAuth & Permissions_ in the left hand menu, then copy the _Bot User OAuth Token_ into your `.env` file under `SLACK_BOT_TOKEN`.

```sh
SLACK_BOT_TOKEN=YOUR_SLACK_BOT_TOKEN
```

3. Click _Basic Information_ from the left hand menu and follow the steps in the _App-Level Tokens_ section to create an app-level token with the `connections:write` scope. Copy that token into your `.env` as `SLACK_APP_TOKEN`.

```sh
SLACK_APP_TOKEN=YOUR_SLACK_APP_TOKEN
```

#### Initializing the project

```sh
git clone https://github.com/slack-samples/bolt-python-support-agent.git my-casey-agent
cd my-casey-agent
```

</details>

### Setup your python virtual environment

```sh
python3 -m venv .venv
source .venv/bin/activate  # for Windows OS, .\.venv\Scripts\Activate instead should work
```

#### Install dependencies

```sh
pip install -e ".[dev]"
```

## Configuration

Everything deployment-specific lives in one gitignored file, `.env`. Nothing in the repository identifies a workspace, machine, model provider, or credential.

```sh
cp .env.sample .env
```

| Variable | Purpose |
|---|---|
| `OPENCODE_BASE_URL` | The local [OpenCode server](https://opencode.ai/docs/server/) Casey talks to |
| `OPENCODE_MODEL` | `<provider>/<model>` from `opencode models` |
| `CASEY_CREDENTIALS_FILE` | Shell file that `export`s the provider credentials; sourced only into the OpenCode server process, kept outside the repo |
| `SLACK_APP_ID` | Your app's ID from App Settings |

Casey itself never sees a model API key: the OpenCode server owns the provider and its credentials.

To migrate to another machine, copy `.env`, the credentials file it points to, and `.slack/apps.dev.json` (your local Slack app link, also gitignored), then run `scripts/dev.sh up`.

### Rotating credentials

`opencode serve` reads credentials once at startup, so a refreshed token needs a server restart. `scripts/dev.sh` handles this: it watches the credentials file and restarts only the OpenCode server when the file changes, while the Slack connection stays up and in-flight requests retry until the server is back.

To rotate: edit the credentials file (or `scripts/dev.sh restart` to force a re-read). Casey's threads keep their history because OpenCode sessions persist on disk across restarts.

If a token has already expired, Casey replies with a `:warning:` message naming the credentials problem instead of a generic error.


## Operator mode

Operator mode turns a Slack thread into a remote coding/shell agent for the machine Casey runs on. It is **off by default** and, when on, is restricted to the Slack user IDs you list. Everyone else keeps talking to the helpdesk persona.

```sh
CASEY_OPERATOR_USER_IDS=U0XXXXXXXXX
CASEY_OPERATOR_ROOT=/home/you
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
| Protected paths | OpenCode permission rules, per tool | `~/.ssh`, `~/.aws`, `~/.config`, the credentials file, `/etc/shadow`, `/etc/ssh`, `/root`, etc. are denied to `read`, `glob`, `grep`, `list`, `edit`, `write`, and to any `bash` command that mentions them. Overridable with `CASEY_OPERATOR_DENY`. |
| Side effects | OpenCode `ask` + Slack buttons | `bash`, `edit`, `write`, `webfetch`, `task` pause until the operator clicks **Approve** or **Deny** in the thread. Reads run immediately. |
| Audit | `logs/operator-audit.log` | JSON line for every request, decision, and rejected click |

A denied action stops the turn; Casey asks how to proceed rather than retrying. A turn that waits longer than `CASEY_OPERATOR_TURN_TIMEOUT` (default 30 min) is aborted.

Things this does **not** protect against: an approved shell command can do anything your user can (the approval card shows the exact command, read it); the deny list is a glob match, so a protected file copied elsewhere is no longer protected; and the OpenCode server itself trusts anything on localhost, so keep `OPENCODE_BASE_URL` on `127.0.0.1`.

## Development

### Starting the app

<details open><summary><strong>Using the dev supervisor (recommended)</strong></summary>

#### Supervisor

```sh
scripts/dev.sh up       # start opencode serve (credential-watching) + slack run
scripts/dev.sh status
scripts/dev.sh logs
scripts/dev.sh restart  # force opencode serve to re-read credentials
scripts/dev.sh down
```

Logs and pid files live in `.run/` (gitignored).
</details>

<details><summary><strong>Using the Slack CLI</strong></summary>

#### Slack CLI

Start an OpenCode server with the provider credentials in its environment first, then:

```sh
slack run
```
</details>

<details><summary><strong>Using the Terminal</strong></summary>

#### Terminal

```sh
python3 app.py
```

</details>

<details><summary><strong>Using OAuth HTTP Server (with ngrok)</strong></summary>

#### OAuth HTTP Server

This mode uses an HTTP server instead of Socket Mode, which is required for OAuth-based distribution.

1. Install [ngrok](https://ngrok.com/download) and start a tunnel:

```sh
ngrok http 3000
```

2. Copy the `https://*.ngrok-free.app` URL from the ngrok output.

<details><summary><strong>Using Slack CLI</strong></summary>

#### Slack CLI

3. Update `manifest.json` for HTTP mode:
   - Set `socket_mode_enabled` to `false`
   - Replace `ngrok-free.app` with your ngrok domain (e.g. `YOUR_NGROK_SUBDOMAIN.ngrok-free.app`)

4. Create a new local dev app:

```sh
slack install -E local
```

5. _(Slack CLI < v4.1.0 only)_ Enable MCP for your app:
   - Run `slack app settings` to open your app's settings
   - Navigate to **Agents & AI Apps** in the left-side navigation
   - Toggle **Model Context Protocol** on

6. Update your `.env` OAuth environment variables:
   - Run `slack app settings` to open App Settings
   - Copy **Client ID**, **Client Secret**, and **Signing Secret**
   - Update `SLACK_REDIRECT_URI` in `.env` with your ngrok domain

```sh
SLACK_CLIENT_ID=YOUR_CLIENT_ID
SLACK_CLIENT_SECRET=YOUR_CLIENT_SECRET
SLACK_SIGNING_SECRET=YOUR_SIGNING_SECRET
SLACK_REDIRECT_URI=https://YOUR_NGROK_SUBDOMAIN.ngrok-free.app/slack/oauth_redirect
```

7. Start the app:

```sh
slack run app_oauth.py
```

8. Click the install URL printed in the terminal to install the app to your workspace via OAuth.

</details>

<details><summary><strong>Using the Terminal</strong></summary>

#### Terminal

3. Create your Slack app at [api.slack.com/apps/new](https://api.slack.com/apps/new) using [`manifest.json`](./manifest.json). Before pasting the manifest, set `socket_mode_enabled` to `false` and replace `ngrok-free.app` with your ngrok domain.

4. Install the app to your workspace and copy the following values into your `.env`:
   - **Signing Secret** — from _Basic Information_
   - **Bot User OAuth Token** — from _OAuth & Permissions_
   - **Client ID** and **Client Secret** — from _Basic Information_

```sh
SLACK_BOT_TOKEN=xoxb-YOUR_BOT_TOKEN
SLACK_CLIENT_ID=YOUR_CLIENT_ID
SLACK_CLIENT_SECRET=YOUR_CLIENT_SECRET
SLACK_SIGNING_SECRET=YOUR_SIGNING_SECRET
SLACK_REDIRECT_URI=https://YOUR_NGROK_SUBDOMAIN.ngrok-free.app/slack/oauth_redirect
```

Replace `your-subdomain` in `SLACK_REDIRECT_URI` with your ngrok subdomain.

5. Start the app:

```sh
python3 app_oauth.py
```

6. Click the install URL printed in the terminal to install the app to your workspace via OAuth.

</details>

> **Note:** Each time ngrok restarts, it generates a new URL. You'll need to update the ngrok domain in `manifest.json`, `SLACK_REDIRECT_URI` in your `.env`, and re-install the app.

</details>

### Using the App

Once Casey is running, there are several ways to interact:

**App Home** — Open Casey in Slack and click the _Home_ tab. You'll see five category buttons. Click one to open a modal, describe your issue, and submit. Casey will start a DM thread with you containing a diagnosis and next steps.

**Direct Messages** — Open a DM with Casey. You'll see suggested prompts like _Reset Password_, _Request Access_, and _Network Issues_ — pick one or describe your own issue. Casey will react with :eyes: while processing, then reply in a thread. Send follow-up messages in the same thread and Casey will maintain the full conversation context.

**Channel @mentions** — Invite Casey to a channel by typing `/invite @Casey` in the message box, then type `@Casey` followed by your issue. Casey responds in a thread so the channel stays clean.

**Assistant Panel** — Click _Add Agent_ in the top-right corner of Slack, select Casey from the list, then pick a suggested prompt or type a message.

Casey will add a :white_check_mark: reaction when it believes an issue has been resolved, and occasionally adds a contextual emoji reaction to keep things friendly.

### Linting

```sh
# Run ruff check from root directory for linting
ruff check

# Run ruff format from root directory for code formatting
ruff format
```

## Project Structure

### `manifest.json`

`manifest.json` is a configuration for Slack apps. With a manifest, you can create an app with a pre-defined configuration, or adjust the configuration of an existing app.

### `app.py`

`app.py` is the entry point for the application and is the file you'll run to start the server. This project aims to keep this file as thin as possible, primarily using it as a way to route inbound requests.

### `app_oauth.py`

`app_oauth.py` is an alternative entry point that runs the app in HTTP mode instead of Socket Mode. This is intended for deployments that use OAuth for app distribution. See the HTTP Mode section under Development for setup instructions.

### `/listeners`

Every incoming request is routed to a "listener". This directory groups each listener based on the Slack Platform feature used.

**`/listeners/events`** — Handles incoming events:

- `app_home_opened.py` — Publishes the App Home view with category buttons.
- `app_mentioned.py` — Responds to `@Casey` mentions in channels.
- `message.py` — Responds to direct messages from users.

**`/listeners/actions`** — Handles interactive components:

- `category_buttons.py` — Opens the issue submission modal when a category button is clicked.
- `feedback.py` — Handles thumbs up/down feedback on Casey's responses.

**`/listeners/views`** — Handles view submissions and builds Block Kit views:

- `issue_modal.py` — Processes modal submissions, starts a DM thread, and runs the agent.
- `app_home_builder.py` — Constructs the App Home Block Kit view.
- `modal_builder.py` — Constructs the issue submission modal.
- `feedback_block.py` — Creates the feedback button block attached to responses.

### `/agent`

`opencode_client.py` is a small HTTP client for the OpenCode server: sessions, prompts, permission replies, and the server-sent event stream.

`support_agent.py` is the helpdesk persona (system prompt, one OpenCode session per thread, tools disabled).

`operator_policy.py`, `operator.py`, and `approvals.py` implement operator mode: the allowlist and directory sandbox, the OpenCode permission ruleset, `cd`/`pwd`, and the Slack approval cards wired to OpenCode's permission events.

`router.py` picks a mode per message based on the Slack user. `deps.py` carries the Slack client and thread context into either agent.

### `/thread_context`

The `store.py` file implements a thread-safe in-memory conversation history store, keyed by channel and thread. This enables multi-turn conversations where Casey remembers previous context within a thread.

## Troubleshooting

### MCP Server connection error: `HTTP error 400 (Bad Request)`

If you see an error like:

```
Failed to connect to MCP server 'streamable_http: https://mcp.slack.com/mcp': HTTP error 400 (Bad Request)
```

This means the Slack MCP feature has not been enabled for your app. There is no manifest property for this yet, so it must be toggled on manually:

1. Run `slack app settings` to open your app's settings page (or visit [api.slack.com/apps](https://api.slack.com/apps) and select your app)
2. Navigate to **Agents & AI Apps** in the left-side navigation
3. Toggle **Slack Model Context Protocol** on
