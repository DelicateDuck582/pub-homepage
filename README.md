# 个人主页

DelicateDuck582 的个人主页：纯静态站点（HTML / CSS / JS），没有构建步骤，也没有运行时依赖。

## 页面

| 文件 | 说明 |
|---|---|
| `index.html` | 主页 |
| `worklog.html` | 工作日志：把 cloud-mail 与 SPlayer 两个仓库（**所有分支**）里由我署名的提交，整理成统计卡、提交类型、热力图、分支拓扑和时间线。**自动生成，别手改。** |
| `提交类型规范.md` | 工作日志里「提交类型」的判定口径 |
| `favicon.*` `apple-touch-icon.png` `logo.png` | 图标与 logo |

## 工作日志怎么来的

1. `_collect.py`：用 git 拉两个仓库的提交图（`--filter=blob:none` 的裸库，只取提交元数据，很快），
   算出分支树、分叉/并回关系、每个提交的类型与改动目录，写出 `worklog-data.json`；
2. `_gen_worklog.py`：把数据渲染成自包含的 `worklog.html`（字体、图标、样式全部内联，无外部请求）；
3. `_cf_worker_worklog.js`：一个 Cloudflare Worker + KV，负责在云端也维护同一份数据：
   - 每天 cron 用 GitHub API 抓一次（免费版子请求受限，所以做了预算制 + 按 sha 缓存，跑不完下次接着跑）；
   - 用 Workers AI **逐条读提交信息，在既有的 13 个类型里重判提交类型**（前缀 + 关键词那套会判错，
     例如 `sync(cos-expression): …` 其实是在加新能力，应判 `feat`）；只判没判过的提交，结果缓存进 KV；
   - 把时间线快照存进 KV，并开 `GET /timeline.json?since=<ISO>` 给页面取增量。

页面打开时只做三件事：`GET /stage.json` 比一下构建时间 → 有更新的整页就整页替换 →
否则 `GET /timeline.json?since=<本页构建时间>` 把新提交补在顶部提示条里（带类型胶囊）。

## 本地跑

```powershell
# 1) 准备两个裸库（放在本目录下，脚本按这个名字找）
git clone --bare --filter=blob:none https://github.com/DelicateDuck582/cloud-mail.git cloud-mail.git
git clone --bare --filter=blob:none https://github.com/DelicateDuck582/SPlayer.git SPlayer.git

# 2) 采集 + 渲染
python _collect.py         # 写出 worklog-data.json
python _gen_worklog.py     # 写出 worklog.html（默认写到脚本所在目录）
```

`_daily_update.ps1` 把上面两步串起来，可选参数：`-Api <Worker 地址> -PublishToken <口令>`
（顺路让 Worker 判类型并取回 `worklog-kinds.json`）、`-Publish`（把新页面推给 Worker）。

## 部署 Worker（可选）

```bash
npm i -g wrangler
wrangler kv namespace create WL     # 把返回的 id 填进 wrangler.toml
wrangler secret put GH_TOKEN        # GitHub 只读 token（只存在 Cloudflare，不进代码）
wrangler secret put PUBLISH_TOKEN   # 写接口口令，自己生成一串随机字符
wrangler deploy
```

`.github/workflows/worklog.yml` 是「GitHub Actions 每天重新渲染并提交」的模板；
启用时把 `SRC_TOKEN`、`WORKLOG_API`、`WORKLOG_PUBLISH_TOKEN` 配成仓库 Secrets 即可。

> **仓库里不放任何密钥。** 口令一律走 `wrangler secret` / 环境变量 / GitHub Secrets，
> 本地口令文件（`_publish_token.txt`）与采集产物都在 `.gitignore` 里。

## 来源与说明

- 首页的版式与配色参考了「二叉树树」的主页（AcoFork，<https://www.acofork.com/>）
- **这个仓库里的所有代码（页面、采集与生成脚本、Cloudflare Worker）都由 AI 编写。**
- 拉丁字母用 Inter（SIL Open Font License 1.1，见 `inter-var.woff2`），中文回退系统字体。
