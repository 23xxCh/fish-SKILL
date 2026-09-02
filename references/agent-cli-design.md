# v0.5 Agent CLI design

## Goal

Expose the existing local, visible-browser collector through a small command-line interface that an agent can call without using private signed APIs or handling credentials.

## Commands

- `python -m goofish_collector collect`: accepts a keyword, page/item limits, supported visible-page filters, and an output directory. It reuses `GoofishBrowserSession` and the existing local browser profile.
- `python -m goofish_collector monitor-status`: reads the existing monitor SQLite database with a read-only connection and returns task and outbox counts. It does not start scanning or send notifications.

## Result contract

`collect` always emits a JSON result on standard output after configuration succeeds. It also writes a JSON summary beside the exported workbook (or to `--summary-json`). The summary contains counts, stop reason, output paths and one of `completed`, `stopped`, `error`, or `verification_required`.

Login, CAPTCHA, and risk controls remain human steps. In an interactive terminal the user is asked to complete the visible browser step and press Enter. In a non-interactive run the command reports `verification_required` and preserves the checkpoint instead of attempting a workaround.

## Verification

- Parser/configuration tests cover supported command arguments and filters.
- A fake browser session verifies structured collection output without a live Xianyu session.
- A temporary monitor database verifies that status reads do not create a missing database or expose provider secrets.
- The existing full test suite, compile check, and package self-test remain green.
