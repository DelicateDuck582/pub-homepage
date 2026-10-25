# 个人主页

DelicateDuck582 的个人主页：纯静态站点（HTML / CSS / JS），没有构建步骤，也没有运行时依赖。

## 页面

| 文件 | 说明 |
|---|---|
| `index.html` | 主页：命令面板，工作日志以全屏浮层（iframe）直接载入 `worklog.html`，另有 Ciallo 彩蛋 |
| `worklog.html` | 工作日志：把 cloud-mail、SPlayer 与 ciyuan-translate 三个仓库（**所有分支**）里由我署名的提交，整理成统计卡、提交类型、热力图、分支拓扑和时间线。**自动生成，别手改。** 统计、热力图、拓扑与分支明细都是全历史；时间线默认「云端自动加载」——静态页里一条提交都不内嵌（体积与提交条数无关），只按年留一行「年份行」，页面打开后自动从 Cloudflare Worker 的 KV 按年（新 → 旧）拉回来现渲染，失败点一下重试。显式设置生成变量 `WORKLOG_BUILD_YEAR` 时回到「分档」模式（那一年内嵌、其余年份点行加载），这是「2027 起自动分档」提前铺好的开关。 |
| `pipeline/提交类型规范.md` | 工作日志里「提交类型」的判定口径 |
| `favicon.*` `apple-touch-icon.png` `logo.png` `ciallo.avif` | 图标、logo 与彩蛋图 |

## 目录结构

```
index.html / worklog.html        页面（worklog.html 是构建产物，写在仓库根）
inter-var.woff2                  字体（index.html 按相对路径引用）
favicon.* / logo.png / ciallo.avif
pipeline/
  _collect.py                    采集：三个仓库的提交 → worklog-data.json
  _gen_worklog.py                渲染：worklog-data.json → 仓库根的 worklog.html
  _worklog_shell.html            工作日志页面外壳模板
  _cf_worker_worklog.js          Cloudflare Worker（云端采集 / AI 判类型 / 发布接口）
  _daily_update.ps1              本地每日更新（采集 + 渲染 + 可选发布）
  wrangler.example.toml          Worker 配置示例（复制到仓库根成 wrangler.toml）
  提交类型规范.md
.github/workflows/worklog.yml    云端每天重渲染
```

采集产物 `worklog-data.json`、`worklog-kinds.json`、`worklog-ports.json` 都落在 `pipeline/`（与 `_collect.py` / `_gen_worklog.py` 同目录），且都在 `.gitignore` 里；三个裸库按 `pipeline/*.git` 的位置放置（本地与 CI 都在跑之前现拉）。

## 工作日志怎么来的

1. `pipeline/_collect.py`：用 git 拉三个仓库的提交图（`--filter=blob:none` 的裸库放在 `pipeline/` 下，只取提交元数据，很快），
   算出分支树、分叉/并回关系、每个提交的类型与改动目录，写出 `pipeline/worklog-data.json`；顺路取一份 Worker 的
   `/ports.json`（Workers AI 判定的移植来源，落到 `pipeline/worklog-ports.json`，取不到就忽略）——提交信息自述的
   正则优先，AI 判定补位，两边的分支名都要能对回仓库真实分支，对不上不认；
2. `pipeline/_gen_worklog.py`：把数据渲染成自包含的 `worklog.html`（字体、图标、样式全部内联，无外部请求）；
   默认输出到**仓库根**（脚本目录 `pipeline/` 的父目录），外壳模板与采集数据从 `pipeline/` 读，字体 `inter-var.woff2`
   从仓库根读。时间线默认按年「云端自动加载」：所有年份的提交一条 DOM 都不生成，只在时间线里按年留一行「年份行」
   （页面里带 `data-arch-year`），页面打开后自动去 Worker 的 `/data.json` + `/classify.json` 按年
   （新 → 旧）拉回来现渲染——全量数据 + 类型判定每次打开只拉一遍，多年份共享同一份 Promise；失败停在
   那一行、点击重试。显式设置 `WORKLOG_BUILD_YEAR` 时改走分档模式：那一年的提交整块内嵌（点击折叠照旧），
   其余年份仍留年份行、点行才拉。热力图点任意小方格都能跳到时间线：目标那天还没渲染就先加载那一年再跳；
3. `pipeline/_cf_worker_worklog.js`：一个 Cloudflare Worker + KV，负责在云端也维护同一份数据：
   - 每天 cron 用 GitHub API 抓一次（免费版子请求受限，所以做了预算制 + 按 sha 缓存，跑不完下次接着跑）；
   - 用 Workers AI **逐条读提交信息，在既有的 13 个类型里重判提交类型**（前缀 + 关键词那套会判错，
     例如 `sync(cos-expression): …` 其实是在加新能力，应判 `feat`）；只判没判过的提交，结果缓存进 KV；
   - 把时间线快照存进 KV，并开 `GET /timeline.json?since=<ISO>` 给页面取增量。

4. `.github/workflows/worklog.yml`：每天 21:40（北京）在 **GitHub Actions** 里把整页重渲染一遍 ——
   把三个仓库的提交图裸克隆到 `pipeline/`、跑 `python pipeline/_collect.py` 与 `python pipeline/_gen_worklog.py`、
   把仓库根新的 `worklog.html` 提交回仓库，并推给 Worker。
   所以**提交节奏、分支拓扑、分支明细、提交时间线四处图表每天都会自己更新，跟你的电脑开不开机无关**；
   Worker 一发现数据有变化，还会立刻发一次 `repository_dispatch`，让这一步当天就近实时跑完。

页面打开时只做三件事：`GET /stage.json` 比一下构建时间 → 有更新的整页就整页替换 →
否则 `GET /timeline.json?since=<本页构建时间>` 把新提交补在顶部提示条里（带类型胶囊、类型计数，
以及一个「现在就去重建整页」的链接，指向的就是上面那个工作流）。

## 本地跑

```powershell
# 1) 准备三个裸库（放在 pipeline/ 下，脚本按这个名字找）
git clone --bare --filter=blob:none https://github.com/DelicateDuck582/cloud-mail.git pipeline/cloud-mail.git
git clone --bare --filter=blob:none https://github.com/DelicateDuck582/SPlayer.git pipeline/SPlayer.git
git clone --bare --filter=blob:none https://github.com/DelicateDuck582/ciyuan-translate.git pipeline/ciyuan-translate.git

# 2) 采集 + 渲染
python pipeline/_collect.py       # 写出 pipeline/worklog-data.json
python pipeline/_gen_worklog.py   # 写出仓库根的 worklog.html
```

`pipeline/_daily_update.ps1` 把上面两步串起来（默认站点目录同样是仓库根），可选参数：`-Api <Worker 地址> -PublishToken <口令>`
（顺路让 Worker 判类型并取回 `pipeline/worklog-kinds.json`）、`-Publish`（把新页面推给 Worker）。

## 部署 Worker（可选）

```bash
npm i -g wrangler
cp pipeline/wrangler.example.toml wrangler.toml   # main 已指向 pipeline/_cf_worker_worklog.js
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

- 首页走「极简科技黑」：近黑底 + 细描边卡片 + 单一青色点缀（Linear / Vercel 那一类）；默认深色，命令面板里可以切到纸白浅色主题
- **这个仓库里的所有代码（页面、采集与生成脚本、Cloudflare Worker）都由 AI 编写。**
- 拉丁字母用 Inter（SIL Open Font License 1.1，见 `inter-var.woff2`），中文回退系统字体。
  index.html 按相对路径引用它；worklog.html 会被单独投放（Worker 的 `/page.html`），所以内嵌成 base64 自包含。
