# Local operation reference

## Launch and validation

Run these commands from the repository root:

```powershell
python -m goofish_collector --self-test
python -m pytest -q
```

For a visible source application, run `python -m goofish_collector`. If the packaged v8 app exists, `dist_v8\XianyuLinkCollector\XianyuLinkCollector.exe` is the preferred launch artifact.

## Agent CLI

Use source mode for the Agent CLI. The v8 desktop executable is for the visible GUI workflow.

```powershell
python -m goofish_collector collect --keyword "相机" --pages 2 --output-dir ".\采集结果" --min-price 100 --max-price 800 --personal-only
python -m goofish_collector monitor-status
```

`collect` uses the same visible dedicated Edge profile as the desktop app. Its standard output is one JSON result, and it writes the same structured payload to `summary_json`. Treat `verification_required` as a request for the user to finish login or a safety page visibly; never substitute a hidden browser, a direct signed request, or copied cookies. `monitor-status` opens the database read-only and must not be used to start a scan or retry a notification.

## Desktop Feishu onboarding

In the desktop app’s **新品监控** page, complete the visible progress in order: save App ID/App Secret, click **开始绑定（5分钟）** and privately send `绑定` to the bot, then click **测试飞书** and verify the notification arrives on the phone. The final verification remains a human observation; a successful send response alone is not proof of phone delivery.

## One-time collection

1. Select **登录 / 切换账号**. The user completes QR-code login and any security page in the dedicated Edge window.
2. Close the Edge window after login so the app can reuse the saved local profile.
3. In **单次采集**, enter the requested keyword, page count, item limit, output directory, and only the requested filters.
4. Start collection. It deduplicates by item ID and produces a WPS-compatible Excel workbook.
5. Open the workbook and verify a small sample of HTTPS item links in a normal browser. A login page, security check, empty list, or parse failure is evidence to report, not a successful collection.

## New-item monitoring

1. Choose Feishu or WxPusher in the app. The user enters credentials directly in the app; do not echo or store them in chat.
2. Send a provider test notification and wait for visible receipt before calling the channel verified.
3. In **新品监控**, create and save a named task. It supports 1–3 pages per scan and 5/10/15/30 minute intervals.
4. Click **立即扫描**. It creates a silent baseline and intentionally has no product alert.
5. Click **启动监控**. Closing the main window should leave it in the system tray. The computer must remain online and awake.

## Evidence limits

- Automated tests and self-tests do not prove that current Xianyu selectors, a real login session, notification delivery, Android deep links, WPS display, or sleep recovery work live.
- Notification delivery retries after 5, 30, and 120 seconds. A final failure remains in the local outbox; report that status honestly.
- A chat URL exists only when the page supplied a real `peerUserId`. Otherwise an HTTPS item link is the correct result.
