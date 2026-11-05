# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

## What This Is

**Casey**, an IT support agent for Slack built with Bolt for Python. Model calls go to a local **OpenCode server** (`agent/opencode_client.py`); the helpdesk persona has no tools. An optional **operator mode** (`agent/operator*.py`, `agent/approvals.py`) lets allowlisted Slack users drive OpenCode's shell/file tools on the host with Slack approval buttons. All deployment-specific values live in `.env` (see `.env.sample`); nothing in the repo may identify a workspace, machine, or credential.

## Slack Plugin

To give your coding agent Slack skills, install the [Slack MCP and Skills Plugin](https://docs.slack.dev/ai/slack-skills-plugin/) (for OpenCode: `npx skills add slackapi/slack-skills-plugin -y -a opencode`).

## Before you finish

Run `ruff check .`, `ruff format --check .`, and `pytest`. CI runs the same on every PR.

## Code Style

- Snake-case filenames
- No code comments. Make the code explain itself with clear names, tests, or types. Put the reason for a change in the PR description.
