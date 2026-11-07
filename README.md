# cms-publisher

潜界 AI 的独立 CMS 草稿投递器：读取三个 Notion 正式专栏中经过 READY 验证的文章，只写入 `cms.qjai.org` **草稿箱**。

本项目从 [`gamehack/qjai-text-publisher`](https://github.com/gamehack/qjai-text-publisher) 拆分 CMS 通道。微信公众号草稿仍由原项目独立负责。本项目不包含微信凭据、微信投递接口、CMS 公开发布接口或旧项目的 systemd cron。

## 数据流与时间

```text
Grok / Product Hunt / TrustMRR → Notion READY
                                   ↓
GitHub Actions cron → SSH → 硅谷 CVM → cms-publisher
                                   ↓
                              CMS 文章草稿箱
```

| Profile | Notion 父页面（仅允许直接子页面） | sourceKey | CMS 分类 | GitHub Actions（北京时间） |
|---|---|---|---|---|
| `x` | `4019e605-ccf8-8193-9c2d-dbe20e3112fa` | `x-daily:YYYY-MM-DD` | `x` | 每日 08:15 |
| `producthunt` | `4109e605-ccf8-81c4-accd-c19386b4a38d` | `producthunt:YYYY-MM-DD` | `producthunt` | 每日 19:15 |
| `trustmrr` | `4109e605-ccf8-81ed-a46c-f9ab95538f5b` | `trustmrr-weekly:YYYY-MM-DD` / `trustmrr-monthly:YYYY-MM` | `TrustMRR` | 每周一 04:15 |

- CMS 调度晚于旧 `qjai-text-publisher` 的微信投递时刻：X 07:30、Product Hunt 17:30/18:30、TrustMRR 周一 03:00。
- Product Hunt 日期以 America/Los_Angeles 上一个完整日期为准，保留原逻辑。
- TrustMRR 每月首个周一会尝试当周周报和当月月报；月报缺失/未 READY 时独立跳过。
- GitHub Actions 使用 `timezone: Asia/Shanghai`。计划任务可能延迟；需要补投时用手动 `workflow_dispatch`，历史期则由服务器管理员明确执行 `backfill --authorize`。
- GitHub-hosted runner **只触发远程任务**；内容处理和 SQLite 状态持久化都发生在硅谷 CVM，不在 Actions 上传数据库/文章/密钥。

## 安全与去重

1. Notion 父页面、日期、唯一子页面标题、READY 内容摘要都必须验证。X 专栏兼容既有 READY 块格式，另外两类验证最后一行 `QJAI_PUBLISH_READY` 和 SHA-256。
2. 所有写操作默认关闭：服务器 `.env` 的 `CMS_ENABLE_DRAFTS` 与 `CMS_<PROFILE>_ENABLED` 均需显式为 `true`；仓库变量 `CMS_PUBLISHER_ENABLED` 也需显式为 `true`，GitHub 定时任务才执行。
3. 旧项目 CMS SQLite 状态（含 `SUCCESS`、`UNKNOWN`、`INTENT`、`FAILED`、`PENDING`）必须在启用新定时前只读导入新库。新项目不会修改旧 SQLite，不导入微信状态。
4. 每个新 `sourceKey` 发送前先调用 CMS `GET /api/ingest?source=...&externalId=...`：若 CMS 已存在则标记 `SUCCESS`，**不再 POST**。
5. 仅目标 `https://cms.qjai.org/api/ingest` 可写，且只提交 CMS 兼容的 `source/externalId/categorySlug/title/summary/bodyMarkdown`。CMS 返回草稿成功或已存在才记为成功。
6. POST 开始前持久化 `INTENT`；任何不确定响应变为 `UNKNOWN`。不会自动重试，需管理员只读查证后显式 reconciliation。
7. GitHub Actions 使用已钉住主机公钥的 SSH 连接。**公开仓库不安装 self-hosted Actions runner**，避免公共 PR 利用 runner 权限。所有 secrets 保存在 Actions Secrets / CVM 本地，不提交到仓库。

## 本地安装与测试

需要 Python 3.11+。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
cp .env.example .env
chmod 600 .env
# 配置 Notion / CMS token；不要提交 .env
.venv/bin/cms-publisher --env-file .env dry-run --profile x --period 2026-11-07
```

常用命令：

```bash
cms-publisher --env-file .env dry-run --profile producthunt --period 2026-11-06
cms-publisher --env-file .env status
cms-publisher --env-file .env health
cms-publisher --env-file .env run --profile x
cms-publisher --env-file .env backfill --profile trustmrr --period weekly:2026-11-02 --authorize
```

`dry-run` 仅读 Notion，不读写状态库，也不会投递。`run` 与 `backfill` 可写 CMS 草稿，且受服务器双开关保护。

## 硅谷服务器安装与一次性切换

部署身份建议沿用现有 `ubuntu` 用户。保留旧项目 `/opt/qjai-text-publisher`，因为微信投递还依赖它。

**切换前**：先确认没有旧 CMS 投递进程在运行，并立即将旧项目本地 `.env` 中 `CMS_ENABLE_DRAFTS=false`；旧项目的 `WECHAT_*` 和 systemd 定时器保持不变。不要删除 `/var/lib/qjai-text-publisher/state.sqlite`。

```bash
# 在硅谷 CVM 上执行，先部署但不要开启写入开关
sudo install -d -m 700 -o ubuntu -g ubuntu /var/lib/cms-publisher
sudo install -d -m 755 -o ubuntu -g ubuntu /opt/cms-publisher
git -C /opt/cms-publisher init -b cms-bootstrap
git -C /opt/cms-publisher remote add origin https://github.com/gamehack/cms-publisher.git
git -C /opt/cms-publisher fetch --depth=1 origin main
git -C /opt/cms-publisher switch -c main FETCH_HEAD
cd /opt/cms-publisher
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp .env.example .env
chmod 600 .env
chmod 755 scripts/run-on-server.sh

# 手工将现有 NOTION_API_KEY / CMS_INGEST_TOKEN 安全填入新 .env，保持全部开关 false。
# 原 SQLite 文件必须存在、可读；旧服务停用 CMS 通道以后，再导入。
.venv/bin/cms-publisher --env-file .env import-from-text-state \
  --file /var/lib/qjai-text-publisher/state.sqlite --authorize
.venv/bin/cms-publisher --env-file .env health
.venv/bin/cms-publisher --env-file .env dry-run --profile x
```

**最终激活顺序（不可颠倒）：**

1. 验证旧 CMS 开关关闭，检查旧项目的微信草稿定时仍正常；停止任何正在进行中的旧 CMS 投递。
2. 将旧 SQLite 的所有 CMS 状态一次性导入新状态库，`status` 核对成功/不确定记录数；确认没有新增旧 CMS 写入（必要时重新检查差异）。
3. 新服务器 `.env` 中开启 `CMS_ENABLE_DRAFTS=true` 及三项 `CMS_*_ENABLED=true`；首先运行单个明确期间的 `run --profile ... --period ...` 进行草稿箱验收，无须重投已存在项。
4. 在新公开 GitHub 仓库的 Actions **Secrets** 设置 `SV_HOST`、`SV_USER`、`SV_SSH_PRIVATE_KEY`、`SV_SSH_KNOWN_HOSTS`（SSH known_hosts 应离线核对服务器 Ed25519 指纹，不要在工作流中动态 ssh-keyscan）。密钥应是专用于此任务的低权限密钥；`ubuntu` 账户不应将其用于其他目的。
5. 手动运行 Actions `CMS draft cron`，选择 `dry-run` 验证远程执行与日志。
6. 最后才将 GitHub **Actions Variables** 中的 `CMS_PUBLISHER_ENABLED` 设置为字符串 `true`，启动 cron。验证下一轮三专栏均正常后，才从 `qjai-text-publisher` 代码中移除 CMS 模块/开关（无需停止微信任务）。

**注意：只把代码推到 GitHub 不是服务器部署完成，也不意味着新定时运行。** 一旦激活，新任务会执行 CMS 写入；在服务端完整切换前，务必保持仓库变量未设置或 `false`。

## 旧状态的对账

```bash
# 已创建的新状态库中，UNKNOWN/INTENT/FAILED 均禁止自动重投。
cms-publisher --env-file .env health
cms-publisher --env-file .env status --profile producthunt

# CMS 中确有此 sourceKey；会通过 CMS GET 核实，标记 SUCCESS，不新增文章。
cms-publisher --env-file .env reconcile \
  --source-key producthunt:2026-11-05 --result SUCCESS --authorize

# 明确确认 CMS 中不存在且有人工核验凭据时，才允许解锁 UNKNOWN/INTENT；不直接发布。
cms-publisher --env-file .env reconcile \
  --source-key producthunt:2026-11-05 --result ABSENT \
  --authorize --confirm-absent \
  --evidence 'Operator checked CMS sourceKey lookup and admin list on 2026-11-07'
```

## 回滚与维护

- 首先将 GitHub Actions 变量 `CMS_PUBLISHER_ENABLED=false`；再将 CVM 新 `.env` 的 `CMS_ENABLE_DRAFTS=false`。不需要删除文章或 SQLite。
- 恢复旧项目 CMS 之前，应先核对新旧 SQLite 以及 CMS GET sourceKey 结果，杜绝两个写入端同时活跃。
- 保持 `cms-publisher` 代码和 SQLite 数据库分离。状态库包含文章正文，是私有数据，不应作为 Actions artifact、源码或 Notion 附件上传。
- 定期查看 GitHub Actions cron 日志、服务器 `health` 和 `status`。如果 GitHub cron 延迟或跳过，手工补跑同一 `sourceKey` 是幂等的；不自动扫描旧期。
