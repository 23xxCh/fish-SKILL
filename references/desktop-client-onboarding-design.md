# v0.6 desktop client onboarding

## Goal

Let a non-technical Windows user complete Feishu mobile notification setup from the desktop client without guessing the required order.

## Interface

The Feishu notification area exposes one read-only progress label:

1. Save App ID and App Secret locally.
2. Start the five-minute binding window and send `绑定` to the visible Feishu bot.
3. Send a real test notification before enabling a monitor task.

The label is derived from the locally encrypted Feishu configuration and current binding state. A successful test is not persisted, so a restarted application does not claim a phone received a message without a fresh real test.

## Data flow

```text
Desktop fields -> DPAPI-encrypted config -> binding worker -> saved open_id -> test button -> phone
```

No credential leaves the Windows machine through the UI. The existing Feishu long connection, outbox, retry behavior, and monitor scheduler are unchanged.

## Release

Build a fresh portable Windows folder with `build.ps1 -DistPath dist_v8` after the full regression suite passes. The release is an executable folder, not an installer; live Feishu delivery still requires the user to complete the real test.
