# cms-publisher

潜界 AI 的独立、**无服务器** CMS 草稿投递器。三个内容生产项目将正式文章写入 Notion；本项目在 **GitHub-hosted Actions runner** 上读取已 READY 的文章，直接投递到 `cms.qjai.org` 草稿箱。

本项目从旧统一发布器拆出 CMS 通道；微信公众号草稿现由独立的 [mp-publisher](https://github.com/gamehack/mp-publisher) 在硅谷 Docker 容器中负责，二者互不调用。旧服务器代码及定时任务已于 2026-11-08 清除。本项目不需要云服务器部署、SSH、systemd、Docker、SQLite、Supabase 写入或持久化存储，也不会公开发布文章。

## 业务流程

```text
Grok / Product Hunt / TrustMRR
             ↓
     Notion READY 专栏
             ↓
   GitHub Actions cron
     (ubuntu-latest)
             ↓
       cms-publisher
             ↓
  GET CMS by sourceKey ── 已存在 ──→ 跳过，不更改文章
             ↓ 不存在
   POST CMS /api/ingest
             ↓
        CMS 草稿箱
```

| Profile | Notion 父页面（只允许直接子页面） | sourceKey | CMS 分类 | Actions 定时（北京时间） |
|---|---|---|---|---|
| `x` | `4019e605-ccf8-8193-9c2d-dbe20e3112fa` | `x-daily:YYYY-MM-DD` | `x` | 每天 08:15 |
| `producthunt` | `4109e605-ccf8-81c4-accd-c19386b4a38d` | `producthunt:YYYY-MM-DD` | `producthunt` | 每天 19:15 |
| `trustmrr` | `4109e605-ccf8-81ed-a46c-f9ab95538f5b` | `trustmrr-weekly:YYYY-MM-DD` / `trustmrr-monthly:YYYY-MM` | `TrustMRR` | 每周一 04:15 |

- CMS 时刻晚于现有微信草稿任务：X 07:30；Product Hunt 17:30/18:30；TrustMRR 周一 03:00。
- Product Hunt 日期继续采用 America/Los_Angeles 上一个完整日期。
- TrustMRR 在每月首个周一检查周报和月报，两者互相独立；月报尚未 READY 则跳过。
- GitHub Actions 使用 `timezone: Asia/Shanghai`，无需自行处理 UTC 换算。
- Actions 定时不是硬实时；发生延迟或跳过时，进入 Actions 手工触发 `workflow_dispatch`。GitHub 可能对 **60 天无仓库活动的公开仓库禁用定时工作流**，因此需要监控 workflow 状态。

## 无状态去重与安全

GitHub-hosted runner 每次启动都是独立环境，本项目**刻意不使用** SQLite 或 Actions cache/artifact 保存业务状态。

1. Notion 仅从三个固定父页面选择指定日期的唯一 READY 子页面。Product Hunt 和 TrustMRR 必须验证最后一行 `QJAI_PUBLISH_READY` 的内容 SHA-256；X 专栏兼容原有 READY 块格式。
2. 每次写入前用 Bearer Token 调用 CMS `GET /api/ingest?source=...&externalId=...`。如果已存在，不覆盖正文、分类或后台人工编辑结果。
3. CMS 端 `articles.sourceKey` 在数据库层有 `unique: true` 约束，`POST /api/ingest` 在出现并发重复时返回 `duplicate: true`。即使 Runner 上次在响应超时后消失，也不会产生两条相同 `sourceKey` 的记录。
4. `POST` 结果不确定时会再进行**一次只读** GET 核验；找不到或核验失败就报告异常，不在同一次运行内重复 POST。后续人工重跑仍需 CMS 自身的唯一约束保护。
5. 只允许目标 `https://cms.qjai.org/api/ingest`，只发送兼容字段 `source/externalId/categorySlug/title/summary/bodyMarkdown`。CMS POST 只生成草稿，不公开发布。
6. **双开关默认关闭**：仓库 Actions Variable `CMS_PUBLISHER_ENABLED` 必须为 `true` 才允许计划任务和手动 `publish`；命令行自身还要求 `CMS_ENABLE_DRAFTS=true` 与对应 `CMS_<PROFILE>_ENABLED=true`。
7. 私密的 Notion 和 CMS token 只存 GitHub Actions Secrets；工作流不输出凭据，不生成含正文的 artifact。PR CI 不能使用这两个生产凭据。

> CMS 数据库自身的 `sourceKey` 唯一约束是长期去重依据，迁移无需旧 SQLite。旧服务器代码和定时器已清理；暂保留 `/var/lib/qjai-text-publisher/state.sqlite` 仅用于核对旧 CMS 的 12 条 SUCCESS 与 1 条 PENDING，确认后可按授权删除。新微信投递状态已迁移到 `/var/lib/mp-publisher/state.sqlite`。

## GitHub Actions 配置（新仓库）

在 `gamehack/cms-publisher` 的 Settings → Secrets and variables → Actions 中设置：

| 类型 | 名称 | 内容 |
|---|---|---|
| **Secret** | `NOTION_API_KEY` | 已可读取三个正式 Notion 专栏的集成凭据 |
| **Secret** | `CMS_INGEST_TOKEN` | 与 `qjai-cms` 后端 `CONTENT_INGEST_TOKEN` 匹配的现有令牌 |
| **Variable** | `CMS_PUBLISHER_ENABLED` | 生产计划任务必须为字符串 `true`；停用时设为 `false` |

无需配置 `SV_HOST`、`SV_USER`、SSH 私钥或服务器 host key；这些都是上一版远程执行方案遗留配置，不再使用。

**现行生产与下一次验收（截至 2026-11-08）：**

1. `.github/workflows/cms-cron.yml` 已配置 `Asia/Shanghai` 定时：X 每日 08:15、Product Hunt 每日 19:15、TrustMRR 周一 04:15；三个 Profile 使用同一 GitHub-hosted 工作流。
2. 2026-11-07 的手动 `workflow_dispatch` 已验证真实 CMS `draft` 创建及重复 `sourceKey` 防重（[执行记录](https://github.com/gamehack/cms-publisher/actions/runs/42388517140)）。这不能替代首次自然 `schedule` 验收。
3. 等待首次自然运行，核对 Actions 结论、READY 选择、CMS 草稿状态与文章分类。发生跳过或失败时先查日志和 CMS `sourceKey`，不要盲目重投。
4. 旧统一发布器 systemd 与代码已清理，不得重新启用旧 CMS 通道。微信公众号投递归 `mp-publisher`，无需等待微信成功才能写 CMS 草稿。

## 本地运行

需要 Python 3.11+。以下本地 `.env` 仅供开发或受控联调，不提交仓库：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
cp .env.example .env
chmod 600 .env
```

```bash
# 仅读取 Notion；不需要打开 CMS 写入开关
.venv/bin/cms-publisher --env-file .env dry-run --profile x --period 2026-11-07

# 仅读取 CMS sourceKey；不修改文章
.venv/bin/cms-publisher --env-file .env lookup --source-key producthunt:2026-11-06

# 需要在本地 .env 显式打开双开关才能写 CMS 草稿
.venv/bin/cms-publisher --env-file .env run --profile producthunt

# 历史期需要显式授权，仍然受 CMS 双开关保护
.venv/bin/cms-publisher --env-file .env backfill \
  --profile trustmrr --period weekly:2026-11-02 --authorize
```

## 回滚和长期维护

- 紧急停止：将仓库变量 `CMS_PUBLISHER_ENABLED=false`；计划和手动发布随即被阻止，`dry-run` 仍可使用。
- CMS 已存在的文章不会被本发布器修改或删除。旧统一发布器已下线，回滚只暂停本仓库生产开关并排查，不应恢复旧 CMS 投递链路。
- 定期查看 Actions 状态、成功率、`SKIPPED` 记录与 CMS 草稿结果。GitHub Actions cron 可能延迟或因公开仓库长期无活动而被停用。
- 当前是轻量 MVP：Actions 运行时间、Notion/CMS API 调用是实际成本；没有额外常驻服务器费用和 GitHub artifact 存储需求。

## 海外创业志（M7 预接入，默认关闭）

- 新增 `overseas` Profile，固定读取 Notion「07 · 海外创业志」的直接子页面，标题格式 `01｜海外创业志｜<人物故事标题>`，来源键 `overseas-stories:01`，CMS 分类 `overseas-stories`。序号为独立的发表期次，不是日期。
- **仅支持显式 `--issue 01`（兼容 `--period 01`）**；没有海外创业志的 cron。GitHub Actions `workflow_dispatch` 已提供 `overseas` 和 `period` 输入，填写序号 `01`，仅作人工 dry-run / 经批准的草稿投递。
- `CMS_OVERSEAS_ENABLED` Action Variable 缺省为 false；即使全局 `CMS_PUBLISHER_ENABLED=true`，新专栏仍不能写草稿。启用前必须通过 M7 人工成稿验收，并核对 CMS 后台已存在该分类。
- 不能直接把 M6 样稿改成 READY；必须先根据 [M7 编辑规范](https://app.notion.com/p/4119e605ccf881f0ae22dcad97535908) 确认正文与源键 SHA-256。
