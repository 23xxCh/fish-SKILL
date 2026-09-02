# 闲鱼商品采集与新品监控设计

## 产品边界

应用只在用户的 Windows 电脑上运行。浏览器交互使用 Playwright 和本机 Edge，用户负责扫码登录、验证码和风险控制；程序只读取公开可见的搜索结果及“准备通知的新品”详情页，不下载图片，也不自动向卖家发送任何消息。

## 两条工作流

### 单次采集

`PySide6 -> CrawlWorker -> 共用登录目录的 Edge -> 搜索卡片 -> ProductRecord -> 检查点 -> openpyxl -> WPS Excel`

保留原有暂停、继续、停止、异常导出和检查点恢复能力。

### 新品监控

`MonitorTaskConfig -> MonitorSchedulerWorker -> 一个持久 Edge 会话串行扫描 -> 任务内去重/基线 -> 真实聊天地址补充 -> SQLite outbox -> Feishu/WxPusher`

- 每个任务按 `(task_id, generation, item_key)` 保存已见商品。
- `item_key` 优先使用商品 ID，没有 ID 时使用规范化商品链接。
- 第一轮设置 `baseline_ready`，返回零个新品。
- 搜索规则指纹变化时增加 generation，保留旧商品并重建基线。
- 休眠导致的多个过期间隔不会逐个重放；恢复后只运行一次，再从当前时间计算下一次。
- 调度线程内所有任务串行执行，并在有任务要跑时才启动一个最小化的专用 Edge 会话。

## 核心对象

- `SearchFilters`：价格、地区、发布时间、四个商品条件和排序。
- `MonitorTaskConfig`：任务名、关键词、筛选、1–3 页、间隔和免打扰。
- `MonitorTaskState`：代次、基线、运行状态、上次/下次扫描和错误。
- `ProductRecord`：卡片字段，以及卖家 ID、真实聊天链接、首次发现和通知时间。
- `NotificationBatch`：任务、锁定的 provider、最多 10 件展示商品、总数和可发送时间。
- `DeliveryResult`：成功、HTTP 状态、错误和是否适合重试。
- `NotificationProvider`：`validate_config()`、`send_test()`、`send_batch()`、能力集合。

## SQLite

数据库位置为 `%LOCALAPPDATA%\GoofishLinkCollector\monitor.db`：

- `tasks`：完整任务配置、规则指纹、代次和调度状态；
- `products`：每个任务/代次的已见商品和通知时间；
- `scans`：扫描时间、结果数和新品数；
- `settings`：当前通道及加密后的通道配置；
- `outbox`：待发送、重试、失败和已发送批次。

任务规则变更不删除历史。删除任务会同时删除该任务的商品、扫描记录和通知队列。切换全局通道不会改写旧 outbox 的 `provider_id`。

## 通知可靠性

扫描产生新品后先写 outbox，再发网络请求。发送成功才填写 `notified_at`。失败的初始请求之后按 5、30、120 秒重试，第四次仍失败时保留为 `failed`。用户可选择用当前通道重新排队失败批次。

免打扰内的同一任务、同一通道和同一结束时间使用 `merge_key` 合并；展示列表始终截断到 10 件，`total_count` 保留真实新品总数。

## 飞书绑定

飞书官方 `lark-oapi` 长连接在独立子进程中运行，父线程提供 5 分钟绑定窗口。只有文本严格等于“绑定”的私聊事件会被接受；首次收到的 `open_id` 使用 DPAPI 保存并回复“绑定成功”。已有绑定时禁止被其他用户覆盖。

## 安全

- Edge 用户目录独立，不存账号密码。
- Windows DPAPI 加密 App Secret、SPT 和 open_id。
- `tenant_access_token` 只存在于 `FeishuProvider` 内存缓存。
- 日志和 Excel 不包含任何通知凭证。
- 只有从详情页真实发现 `peerUserId` 时才构造 `https://www.goofish.com/im?...`；否则回退商品 HTTPS URL。
- 安卓 `fleamarket://` 唤端入口尚未通过飞书和 WxPusher 双通道真机验收，因此正式界面不展示。

## 当前验收层级

自动测试覆盖模型校验、基线/跨轮去重、规则换代、数据库重启持久性、DPAPI 密文、通知内容、通道锁定、免打扰合并、重试、聊天链接回退、Excel 超链接和现有单次采集回归。

仍需用户在真实账号上完成：飞书后台配置和绑定、WxPusher 到达、两轮真实闲鱼扫描、登录失效恢复、安卓按钮跳转、电脑休眠恢复，以及 WPS 手工打开监控导出文件。
