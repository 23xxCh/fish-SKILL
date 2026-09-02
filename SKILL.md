---
name: goofish-local-collector
description: "Run and verify this local Xianyu/Goofish Windows collector for item-link exports and new-item monitoring. Use after cloning this repository, not for platform bypass or seller messaging."
---

# Goofish Local Collector

Operate this repository's local desktop collector so the user gets either a verifiable Excel link export or a locally running new-item monitor. Work from the repository root and prefer the packaged `dist_v7\\XianyuLinkCollector\\XianyuLinkCollector.exe` when it exists; otherwise use the source application.

## Boundaries

- This is a Windows-local workflow. Keep login state, task data, credentials, and exports on the user's computer.
- The user handles QR-code login, password or SMS input, CAPTCHA, security checks, and notification credentials. Never ask the user to paste those secrets into chat or try to bypass a challenge.
- Do not message sellers, place orders, publish listings, scrape at an unreasonable rate, or claim a live operation succeeded without observable evidence.
- One dedicated Edge profile is shared by login, one-time collection, and monitoring. Do not start concurrent operations.

## Choose the operation

Confirm the repository root and intended launch artifact first. Run `python -m goofish_collector --self-test` before diagnosing source code or making changes.

- **One-time export:** launch the desktop application; have the user log in; then use **单次采集** for the requested keyword, page count, limit, output directory, and filters. Report the generated `.xlsx` path and verify that it opens with non-empty HTTPS item links.
- **New-item monitor:** configure either Feishu or WxPusher in the app with credentials entered locally by the user. Create and save a narrow task, use **立即扫描** for its silent baseline, then choose **启动监控**. The first scan normally sends no alert.
- **Diagnosis:** separate a local app failure from a live-platform change. If login expires, security verification appears, or parsing fails, stop the affected action and let the user resolve login/security visibly in Edge. Preserve the error and output path for a minimal fix.

## Completion standard

For exports, report the requested keyword and filters, pages attempted, output path, item/link count, and sample-link result. For monitors, report task settings, baseline time, notification-test result, monitor/tray state, and any live checks still unverified. Read [operation details](references/operation.md) when performing either operation.
