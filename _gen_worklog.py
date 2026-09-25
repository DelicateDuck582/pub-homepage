# -*- coding: utf-8 -*-
r"""用 worklog-data.json 生成「工作日志」页（worklog.html）。

风格完全复用 index.html：抽出它同一份 <style> 共用，再追加本页专用样式（图表 / 分支拓扑 / 时间线）。
输出自包含静态页，无任何外部依赖。
"""
import base64
import collections
import datetime
import html
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))   # 脚本所在目录（本地 = _worklog-dev，CI 里 = 仓库目录）
SITE = os.environ.get("WORKLOG_SITE") or HERE       # 默认写到脚本所在目录（克隆下来直接能跑）
# 页面打开时会先去这个地址拉一次最新数据（/stage.json、/timeline.json）；留空则完全不联网（纯静态）
# 环境变量优先，其次用下面这个默认值（部署 Worker 后填这里，见 _cf_deploy.md）
API_BASE = os.environ.get("WORKLOG_API") or "https://worklog-collector.delicateduck582.workers.dev"
# 站点仓库（owner/repo）：提示条里的「现在就去重建整页」指向它的 Actions；留空就不显示这个链接
REPO_SLUG = os.environ.get("WORKLOG_REPO") or "DelicateDuck582/pub-homepage"
BUILD_AT = datetime.datetime.now().astimezone().isoformat(timespec="seconds")   # 本页构建时间，用于和 Worker 的采集时间比对
DATA = os.path.join(HERE, "worklog-data.json")
FONT_FILE = os.path.join(HERE, "inter-var.woff2")
INDEX = os.path.join(SITE, "index.html")
OUT = os.environ.get("WORKLOG_OUT") or os.path.join(SITE, "worklog.html")

# 与本人工作无关的自动分支（Cloudflare Pages / Workers 编译产物分支等），不放进页面
BRANCH_HIDE = re.compile(r"^(cloudflare/|cf-|workers-|dependabot/|renovate/)", re.I)

data = json.load(open(DATA, encoding="utf-8"))
repos = data["repos"]


def clean_repos(items):
    """剔除自动生成的分支，并同步修正分支数 / 分支提交总量。"""
    for r in items:
        r["branches"] = [b for b in r["branches"] if not BRANCH_HIDE.search(b["name"])]
        keep = set(b["name"] for b in r["branches"])
        for c in r["commits"]:
            c["branches"] = [b for b in c["branches"] if b in keep]
        r["stats"]["total_branch_commits"] = sum(b["commits_total"] for b in r["branches"])
    return items


repos = clean_repos(repos)

# 类型键 → 中文名（规则判的和 AI 判的都只用这 13 个键；顺序也定了页面里的展示次序）
KIND_LABEL = {"feat": "新功能", "fix": "修复", "refactor": "重构", "merge": "合并", "docs": "文档",
              "chore": "杂项", "style": "样式", "perf": "性能", "test": "测试", "build": "构建",
              "ci": "CI", "revert": "回滚", "other": "其它"}
KIND_ORDER = ("feat", "fix", "refactor", "merge", "docs", "chore", "style", "perf",
              "test", "build", "ci", "revert", "other")

KINDS_FILE = os.path.join(HERE, "worklog-kinds.json")   # 可选：Worker 的 /classify.json（AI 逐条判定的提交类型）


def load_kind_overrides():
    """AI 逐条判定的提交类型（可选）：sha → feat / fix / refactor / ...（只认已知的类型键）。

    没有这个文件、解析失败、或一条都不认 → 当没有：仍然按前缀 + 关键词那套规则归类，
    页面与「完全不接 AI」时逐字节一致（有专门的探针盯着这一点）。"""
    if not os.path.exists(KINDS_FILE):
        return {}
    try:
        raw = json.load(open(KINDS_FILE, encoding="utf-8"))
    except (ValueError, OSError) as err:
        print("AI 类型判定读取失败（忽略）：%s" % err)
        return {}
    got = raw.get("kinds") if isinstance(raw, dict) else None
    if not isinstance(got, dict):
        return {}
    return {sha: k for sha, k in got.items() if k in KIND_LABEL}


KIND_OVERRIDES = load_kind_overrides()


def apply_kind_overrides(items):
    """把 AI 判定的类型盖到提交上，并重算每个仓库的类型计数（图表与筛选都吃这一份）。

    盖过的那条留一个 kind_rule（规则原本判成什么），页面里可以如实说明是谁改的。"""
    fixed = 0
    for r in items:
        for c in r["commits"]:
            want = KIND_OVERRIDES.get(c["sha"])
            if want and want != c["kind"]:
                c["kind_rule"] = c["kind"]
                c["kind"] = want
                fixed += 1
        r["stats"]["kinds"] = dict(collections.Counter(c["kind"] for c in r["commits"]).most_common())
    return fixed


KIND_FIXED = apply_kind_overrides(repos)

# 提交类型那张图的说明：接了 AI 就如实说复核过，没接就还是那句「按前缀 + 关键词」
KIND_SUB = ("先按前缀 + 关键词归类，再由 Cloudflare Workers AI 逐条复核（改了 %d 条）" % KIND_FIXED
            if KIND_FIXED else "按提交信息的前缀与关键词自动归类")


def font_css():
    """内嵌 Inter 变量字体（拉丁子集）：英文与数字换掉 Segoe UI，中文自动回退系统字体。"""
    if not os.path.exists(FONT_FILE):
        return ""
    b64 = base64.b64encode(open(FONT_FILE, "rb").read()).decode("ascii")
    return ("  /* 内嵌 Inter 变量字体（拉丁子集，中文字符回退系统字体） */\n"
            "  @font-face { font-family: \"Inter var\"; font-style: normal; font-weight: 100 900;\n"
            "    font-display: swap; src: url(data:font/woff2;base64,%s) format(\"woff2\"); }\n" % b64)

ICONS = {
    "git": '<path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
    "file": '<path d="M14 3.5H7.5A1.5 1.5 0 0 0 6 5v14a1.5 1.5 0 0 0 1.5 1.5h9A1.5 1.5 0 0 0 18 19V7.5L14 3.5Z"/><path d="M14 3.5v4h4"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M8 3v4M16 3v4M3.5 10h17"/>',
    "commit": '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v5.8M12 15.2V21"/>',
    "clock": '<path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7.5V12l3.5 2"/>',
    "repo": '<path d="M5 3.5h9.5a2 2 0 0 1 2 2V21H7a2 2 0 0 1-2-2V3.5Z"/><path d="M5 17.5h11.5"/><path d="M18.5 21V8.5"/>',
    "back": '<path d="M20 12H4.5"/><path d="m10.5 6-5.5 6 5.5 6"/>',
    "up": '<path d="M12 19.5v-14"/><path d="m6.5 11 5.5-5.5L17.5 11"/>',
    "spark": '<path d="m12 3.5 1.8 4.9 4.9 1.8-4.9 1.8L12 16.9l-1.8-4.9-4.9-1.8 4.9-1.8L12 3.5Z"/>',
    "merge": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M6 9v6"/><path d="M18 12a6 6 0 0 1-6 6"/>',
    "tag": '<path d="M3.5 11.4V5A1.5 1.5 0 0 1 5 3.5h6.4a1.5 1.5 0 0 1 1.1.4l7.6 7.6a1.5 1.5 0 0 1 0 2.1l-6 6a1.5 1.5 0 0 1-2.1 0l-7.6-7.6a1.5 1.5 0 0 1-.4-1.1Z"/><circle cx="7.8" cy="7.8" r="1.3"/>',
    "chev": '<path d="m9.5 6 6 6-6 6"/>',
"snake": '<path d="M4 16.5h8.6a3.2 3.2 0 0 0 0-6.4H9.3a3.1 3.1 0 0 1 0-6.2h6.5"/>'
         '<circle cx="18.7" cy="17.3" r="2.9"/><circle cx="19.5" cy="16.5" r="0.7"/>',
}


def esc(s):
    return html.escape(str(s), quote=True)


def icon(name, cls="icon"):
    return '<svg class="%s" viewBox="0 0 24 24" aria-hidden="true">%s</svg>' % (cls, ICONS[name])


def ym_label(ym):
    return "%s 年 %d 月" % (ym[:4], int(ym[5:7]))


def short_date(day):
    return "%d/%d" % (int(day[5:7]), int(day[8:10]))


CSS_A = """
  /* 首屏：「我的 GitHub」按钮下面那行小字 */
  .hero-note { max-width: 74ch; font-size: 12.5px; line-height: 1.65; color: var(--muted); }
  .hero-note strong { color: var(--fg); font-weight: 650; }

  /* ============ 工作日志页：概览 ============ */
  .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); gap: 12px; margin-top: 18px; }
  .stat { padding: 15px 17px; border-radius: 14px; background: var(--card); box-shadow: var(--shadow-card); }
  .stat-num { font-size: 27px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.1; font-variant-numeric: tabular-nums; }
  .stat-num .unit { margin-left: 3px; font-size: 13px; font-weight: 500; color: var(--muted); }
  .stat-label { margin-top: 5px; font-size: 12.5px; color: var(--muted); }
  .stat.is-accent .stat-num { color: var(--accent); }

  /* 仓库占比 */
  .split { margin-top: 18px; }
  .split-bar { display: flex; height: 11px; border-radius: 999px; overflow: hidden; background: color-mix(in srgb, var(--fg) 8%, transparent); }
  .split-bar > i { display: block; height: 100%; }
  .split-bar > i:nth-child(1) { background: var(--accent); }
  .split-bar > i:nth-child(2) { background: color-mix(in srgb, var(--accent) 42%, var(--bg)); }
  .split-legend { display: flex; flex-wrap: wrap; gap: 6px 20px; margin-top: 10px; font-size: 13px; color: var(--muted); }
  .split-legend b { color: var(--fg); font-weight: 650; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 3px; margin-right: 6px; vertical-align: 1px; }
  .dot-1 { background: var(--accent); }
  .dot-2 { background: color-mix(in srgb, var(--accent) 42%, var(--bg)); }

  /* 图表卡 */
  .chart-card { margin-top: 16px; padding: 17px 18px 19px; border-radius: 14px; background: var(--card); box-shadow: var(--shadow-card); }
  .chart-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px 14px; flex-wrap: wrap; }
  .chart-title { font-size: 14.5px; font-weight: 650; }
  .chart-sub { font-size: 12.5px; color: var(--muted); }

  /* 分支拓扑（流程式） */
  .graph-wrap { margin-top: 14px; overflow-x: auto; scrollbar-width: thin; }
  .graph-wrap svg { display: block; min-width: 620px; width: 100%; height: auto; }
  .g-axis { stroke: var(--border); stroke-width: 1; }
  .g-axis-label { fill: var(--muted); font-size: 10px; }
  .g-lane { stroke: var(--border-strong); stroke-width: 1.8; fill: none; stroke-linecap: round; }
  .g-lane-main { stroke: var(--accent); stroke-width: 2.8; }   /* 主分支（图的根） */
  /* 上游（原作者）的部分单独走一个色系：主线里「我接手之前」的那一截、以及从上游提交分出的分叉线。
     这两条必须排在 .g-lane-main / .g-fork 后面，才能盖掉主色 */
  :root, html[data-theme="dark"] { --up: #82a3cb; }
  html[data-theme="light"] { --up: #4a6d97; }
  .g-lane-up { stroke: var(--up); stroke-width: 2.4; }
  /* 整张拓扑图只有三种符号：泳道线、提交圆点、HEAD 空心圆（外加窗口外的折叠点） */
  .g-fork { stroke: var(--accent); stroke-width: 1.4; stroke-dasharray: 4 3; opacity: 0.85; fill: none; }
  .g-fork-up { stroke: var(--up); }                            /* 分叉点落在上游的提交上 */
  .g-node { fill: var(--accent); }
  .g-node-past { fill: none; stroke: var(--muted); stroke-width: 1.4; stroke-dasharray: 2 1.6; opacity: 0.9; }   /* 窗口外的提交折叠点 */
  .g-node-head { fill: var(--bg); stroke: var(--accent); stroke-width: 2.2; }
  .g-label { fill: var(--fg); font-size: 11.5px; font-weight: 600; font-family: ui-monospace, Consolas, monospace; }
  .g-meta { fill: var(--muted); font-size: 10px; font-family: ui-monospace, Consolas, monospace; }
  .g-legend { display: flex; flex-wrap: wrap; gap: 6px 18px; margin-top: 12px; font-size: 12.5px; color: var(--muted); }

  /* 月度柱状 */
  .bars { display: flex; align-items: flex-end; gap: 7px; height: 140px; margin-top: 16px; overflow-x: auto; padding-bottom: 2px; }
  .bar-col { flex: 1 0 38px; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; gap: 7px; }
  .bar-val { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .bar { width: 100%; border-radius: 7px 7px 3px 3px; background: linear-gradient(180deg, var(--accent), color-mix(in srgb, var(--accent) 45%, transparent)); }
  .bar-label { font-size: 10.5px; color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
"""

CSS_B = """
  /* 活跃热力图 */
  .heat-wrap { margin-top: 16px; overflow-x: auto; scrollbar-width: thin; padding-bottom: 4px; --wl-cell: 11px; }
  .heat-inner { display: inline-block; min-width: 100%; }
  /* 方格边长由 JS 按卡片宽度算出来写进 --wl-cell（正方形，行高跟列宽同值）；
     这样列距恒等于「边长 + 缝」，蛇按列走才不会越走越偏 */
  .heat { display: grid; grid-auto-flow: column; grid-template-rows: repeat(7, var(--wl-cell, 11px)); grid-auto-columns: var(--wl-cell, 11px); gap: 3px; position: relative; }
  .heat i { width: auto; height: auto; border-radius: 27%; background: color-mix(in srgb, var(--fg) 7%, transparent); }
  .heat i { transition: background-color 0.34s linear, transform 0.16s var(--ease-out); }   /* 被蛇吃掉时淡出 */
  .heat i[data-day] { cursor: pointer; transition: transform 0.14s var(--ease-out), box-shadow 0.14s ease; }
  .heat i[data-day]:hover { transform: scale(1.45); box-shadow: 0 0 0 2px var(--bg), 0 0 0 3px color-mix(in srgb, var(--accent) 65%, transparent); }
  .heat i[data-day]:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .heat i[data-l="1"] { background: color-mix(in srgb, var(--accent) 30%, transparent); }
  .heat i[data-l="2"] { background: color-mix(in srgb, var(--accent) 55%, transparent); }
  .heat i[data-l="3"] { background: color-mix(in srgb, var(--accent) 78%, transparent); }
  .heat i[data-l="4"] { background: var(--accent); }
  .heat-axis { display: grid; grid-auto-flow: column; grid-auto-columns: var(--wl-cell, 11px); gap: 3px; margin-top: 7px; font-size: 10.5px; color: var(--muted); }
  .heat-axis span { width: auto; white-space: nowrap; }
  /* 热力图贪吃蛇 */
  .snake-btn { display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 10px; margin-left: 9px; border-radius: 999px; font-size: 12px; color: var(--muted); background: var(--card); box-shadow: inset 0 0 0 1px var(--border); vertical-align: 2px; transition: color 0.16s ease, box-shadow 0.16s ease; }
  .snake-btn:hover { color: var(--accent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent); }
  .snake-btn[disabled] { opacity: 0.45; cursor: default; }
  .snake-btn svg { width: 13px; height: 13px; }
  .snake-btn .snake-ico { display: inline-flex; }
  /* 蛇身按 snk 的做法：每段朝尾巴方向多探半个间隙、把方格之间的缝盖上，串成连续的一条；
     位置靠 transition 滑过去（线性），不再一格一格跳，速度也跟 snk 同一个量级 */
  .snake-layer { position: absolute; left: 0; top: 0; width: 0; height: 0; pointer-events: none; z-index: 3; --snk: 90ms; }
  .snake-head, .snake-seg { position: absolute; width: 11px; height: 11px; border-radius: 32%; background: var(--accent); box-sizing: border-box;
                             transition: left var(--snk) linear, top var(--snk) linear, width var(--snk) linear, height var(--snk) linear; }
  .snake-seg { z-index: 1; transition: left var(--snk) linear, top var(--snk) linear, width var(--snk) linear, height var(--snk) linear, opacity 0.18s linear; }
  .snake-head { z-index: 2; border-radius: 32%; box-shadow: 0 0 0 1.5px color-mix(in srgb, var(--accent) 42%, transparent);
                transition: left var(--snk) linear, top var(--snk) linear, width var(--snk) linear, height var(--snk) linear, opacity 0.18s linear; }
  .snake-head i { position: absolute; width: 21%; height: 21%; border-radius: 50%; background: var(--bg); transition: all 0.12s linear; }
  /* 眼睛跟着行进方向摆（参照 snk 的蛇头：比小方格略大、圆角更圆一点）；尺寸用百分比，方格变大变小都对得上 */
  .snake-head[data-dir="right"] i { right: 25%; }
  .snake-head[data-dir="right"] i:first-child { top: 24%; }
  .snake-head[data-dir="right"] i:last-child { bottom: 24%; }
  .snake-head[data-dir="left"] i { left: 25%; }
  .snake-head[data-dir="left"] i:first-child { top: 24%; }
  .snake-head[data-dir="left"] i:last-child { bottom: 24%; }
  .snake-head[data-dir="down"] i { bottom: 25%; }
  .snake-head[data-dir="down"] i:first-child { left: 24%; }
  .snake-head[data-dir="down"] i:last-child { right: 24%; }
  .snake-head[data-dir="up"] i { top: 25%; }
  .snake-head[data-dir="up"] i:first-child { left: 24%; }
  .snake-head[data-dir="up"] i:last-child { right: 24%; }
  .heat i.is-eaten { background: color-mix(in srgb, var(--fg) 7%, transparent); cursor: default; transform: none; box-shadow: none; }
  /* 蛇头消失后「裂开」出来的小方块：从边界飞回各自原来的位置 */
  .burst-layer { position: absolute; left: 0; top: 0; width: 0; height: 0; pointer-events: none; z-index: 3; }
  .burst-dot { position: absolute; border-radius: 27%; background: var(--accent); opacity: 0;
               transition: left 0.34s var(--ease-out), top 0.34s var(--ease-out),
                           width 0.34s var(--ease-out), height 0.34s var(--ease-out), opacity 0.22s linear; }
  .burst-dot[data-l="1"] { background: color-mix(in srgb, var(--accent) 30%, transparent); }
  .burst-dot[data-l="2"] { background: color-mix(in srgb, var(--accent) 55%, transparent); }
  .burst-dot[data-l="3"] { background: color-mix(in srgb, var(--accent) 78%, transparent); }

  /* 类型分布 */
  .kind-row { display: grid; grid-template-columns: 92px 1fr 54px; align-items: center; gap: 10px; margin-top: 11px; }
  .kind-name { display: flex; align-items: center; gap: 7px; font-size: 13px; }
  .kind-track { height: 9px; border-radius: 999px; background: color-mix(in srgb, var(--fg) 8%, transparent); overflow: hidden; }
  .kind-track > i { display: block; height: 100%; border-radius: 999px; }
  .kind-val { font-size: 12.5px; color: var(--muted); text-align: right; font-variant-numeric: tabular-nums; }
  .kd { display: inline-block; border-radius: 50%; }
  .kind-name .kd { width: 7px; height: 7px; }
  .kd-feat { background: #cc785c; }
  .kd-fix { background: #6f9f7d; }
  .kd-refactor { background: #7d8fbf; }
  .kd-merge { background: #a98bc0; }
  .kd-docs { background: #c2a25a; }
  .kd-chore { background: #8f8e86; }
  .kd-style { background: #b98fa8; }
  .kd-perf { background: #5f9fa8; }
  .kd-test { background: #7fa87f; }
  .kd-build { background: #a08f6f; }
  .kd-ci { background: #8fa0b8; }
  .kd-revert { background: #b07d78; }
  .kd-other { background: #8b8a83; }

  /* 筛选 */
  .filters { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
  .filter-btn { display: inline-flex; align-items: center; height: 32px; padding: 0 13px; border-radius: 999px; font-size: 13px; background: var(--card); box-shadow: inset 0 0 0 1px var(--border); color: var(--muted); }
  .filter-btn.is-on { background: var(--accent-soft); color: var(--accent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent); font-weight: 600; }
  .filter-btn .kd { width: 7px; height: 7px; margin-right: 6px; }
  .filter-btn .bd { width: 7px; height: 7px; margin-right: 6px; border-radius: 2px; background: var(--accent); opacity: 0.5; }
  /* 分支筛选组：默认收着，选了仓库才出现，按钮一个个弹进来 */
  .filters-branch { display: none; }
  .filters-branch.is-on { display: flex; }
  .filters-branch.is-on .filter-btn { animation: wl-btn-in 0.28s var(--ease-out) backwards; animation-delay: calc(var(--i, 0) * 26ms); }
  @keyframes wl-btn-in { from { opacity: 0; transform: translateY(-6px) scale(0.94); } }

  /* 时间线 */
  .tl { position: relative; margin-top: 20px; padding-left: 24px; }
  .tl::before { content: ""; position: absolute; left: 5px; top: 8px; bottom: 8px; width: 1px; background: var(--border); }
  .tl-day { position: relative; margin-bottom: 20px; }
  .tl-day::before { content: ""; position: absolute; left: -23px; top: 6px; width: 9px; height: 9px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent); }
  .tl-date { display: flex; align-items: baseline; gap: 8px; font-size: 13px; font-weight: 650; font-variant-numeric: tabular-nums; }
  .tl-date em { font-size: 12px; font-weight: 500; font-style: normal; color: var(--muted); }
  .tl-items { display: flex; flex-direction: column; gap: 7px; margin-top: 9px; }
  .tl-item { display: flex; align-items: flex-start; gap: 9px; padding: 10px 13px; border-radius: 12px; background: var(--card); box-shadow: var(--shadow-card); }
  .tl-item > .icon { width: 14px; height: 14px; flex: none; margin-top: 3px; color: var(--accent); }
  .tl-body { min-width: 0; }
  .tl-msg { font-size: 13.5px; word-break: break-word; }
  .tl-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 5px 8px; margin-top: 5px; font-size: 11.5px; color: var(--muted); }
  .tl-meta code { font-family: ui-monospace, Consolas, monospace; }
  .tag { display: inline-flex; align-items: center; height: 19px; padding: 0 7px; border-radius: 6px; font-size: 11px; background: color-mix(in srgb, var(--fg) 9%, transparent); color: var(--muted); }
  .tag-repo { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
  .tag-branch { font-family: ui-monospace, Consolas, monospace; }
  .tag .kd { width: 6px; height: 6px; margin-right: 5px; }
  .tl-more { margin-top: 14px; }
  .empty { margin-top: 16px; padding: 22px; border-radius: 14px; background: var(--card); box-shadow: var(--shadow-card); font-size: 13.5px; color: var(--muted); text-align: center; }

  /* 分支明细表 */
  .btable { width: 100%; margin-top: 16px; border-collapse: collapse; font-size: 13px; }
  .btable th, .btable td { padding: 9px 10px; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
  .btable th { font-size: 12px; font-weight: 600; color: var(--muted); }
  .btable td code { font-family: ui-monospace, Consolas, monospace; font-size: 12.5px; }
  .btable .num { text-align: right; font-variant-numeric: tabular-nums; }
  .bwrap { overflow-x: auto; scrollbar-width: thin; }

  @media (max-width: 640px) {
    .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .stat { padding: 13px 14px; }
    .stat-num { font-size: 22px; }
    .chart-card { padding: 15px 14px 16px; border-radius: 13px; }
    .kind-row { grid-template-columns: 80px 1fr 44px; gap: 8px; }
    .tl { padding-left: 20px; }
    .tl-day::before { left: -20px; }
    .tl-item { padding: 9px 11px; }
    .filter-btn { height: 36px; }
    .bars { height: 126px; }
  }
  @media (hover: hover) and (pointer: fine) {
    .stat:hover, .tl-item:hover { background: var(--card-hover); }
    .filter-btn:hover { background: var(--card-hover); color: var(--fg); }
  }
  @media (hover: none), (pointer: coarse) {
    .filter-btn:active { background: var(--card-hover); color: var(--fg); }
    .tl-item:active { background: var(--card-hover); }
  }
"""


def dnum(day):
    """日期 -> 序数，用于时间轴比例。"""
    return datetime.date(*[int(x) for x in day[:10].split("-")]).toordinal()


def from_ord(n):
    return datetime.date.fromordinal(n)


def week_start(day):
    d = datetime.date(*[int(x) for x in day[:10].split("-")])
    return d - datetime.timedelta(days=d.weekday())


def lane_label(name, limit=18):
    """泳道标签：去掉路径前缀，过长截断（完整名走悬浮框）。"""
    short = name.rsplit("/", 1)[-1]
    return short if len(short) <= limit else short[:limit - 1] + "…"


def topo_lanes(branches, root):
    """按真实父子关系排泳道：根在最上，子分支紧挨它的父分支（深度优先）。"""
    by_name = {b["name"]: b for b in branches}
    kids = collections.defaultdict(list)
    for b in branches:
        kids[b.get("parent") or ""].append(b["name"])
    order, seen = [], set()

    def walk(n):
        if n in seen or n not in by_name:
            return
        seen.add(n)
        order.append(n)
        for c in sorted(kids.get(n, [])):
            walk(c)

    walk(root)
    for n in sorted(by_name):
        walk(n)
    return [by_name[n] for n in order]


WINDOW_DAYS = 62        # 拓扑图只画最近这么多天（约两个月），更早的提交折叠成泳道最左边的小圆点


def graph_window(repo):
    """拓扑图横轴窗口：只画最近 WINDOW_DAYS 天，更早的历史折叠到最左边（「更早已经折叠 ↦」）。
    返回 dict(x0=起点序数, win0=起点日期, x1=终点序数, x0_full=真实最早的一天)，没数据时返回 None。"""
    days = [b[k][:10] for b in repo["branches"] for k in ("fork_date", "head_date", "first", "last") if b.get(k)]
    if not days:
        return None
    x0_full, x1_full = dnum(min(days)), dnum(max(days))
    if x1_full <= x0_full:
        x1_full = x0_full + 7
    x0 = x0_full if (x1_full - x0_full) <= WINDOW_DAYS else x1_full - WINDOW_DAYS
    x1 = int(round(x1_full + max(3, (x1_full - x0) * 0.09)))    # 右侧留白：曲线要落到子泳道、末尾圆点也要有地方站
    return dict(x0=x0, win0=from_ord(int(round(x0))).isoformat(), x1=x1, x0_full=x0_full)


def my_shas(repo):
    """我提交过的那些 sha（7 位短号），用来判断某个提交是不是上游作者的。"""
    return {c["short"] for c in repo["commits"]}


def fork_owner(repo, b):
    """这条分支的分叉点是谁的提交：'mine'（我写的）/ 'up'（上游作者的）/ ''（没有分叉点）。"""
    if not b.get("fork_point"):
        return ""
    return "mine" if b["fork_point"] in my_shas(repo) else "up"


def fork_stats(repo):
    """分叉线按「分叉点属于谁」计数，图例只显示真的画出来的那一类。"""
    out = collections.Counter()
    for b in repo["branches"]:
        o = fork_owner(repo, b)
        if o:
            out[o] += 1
    return out


def upstream_split(repo):
    """主线（图的根）上「我接手之前」的那一截 —— 也就是上游作者的 main。

    数据里每条泳道的 first/last 是「我的提交」的日期范围，所以根的 first 之前都不算我的，
    而拓扑图窗口只有最近两个月、左端常常就是那份日期，于是主线左侧会出现一段空白。
    返回 dict(lane, day, mine, total)；day 为空表示我在主线上一次都没提交（整条线都是上游的）；
    返回 None 表示我早就接手了（窗口左端已经属于我，没有可补的上游段）。"""
    win = graph_window(repo)
    root = next((b for b in repo["branches"] if b["name"] == repo["main"]), None)
    if not win or root is None:
        return None
    if not root.get("first"):
        return dict(lane=root["name"], day="", mine=0, total=root["commits_total"])
    if root["first"] <= win["win0"]:
        return None
    return dict(lane=root["name"], day=root["first"],
                mine=root["commits_mine"], total=root["commits_total"])


def svg_branch_graph(repo, width=920):
    """分支拓扑：x 轴是时间、y 轴是分支泳道。父子关系取自 git merge-base。
    整张图只有三种符号：圆点 = 我当天在该分支的提交（同一天只画一颗，越大说明当天越多）、
    空心圆 = 分支 HEAD、虚线曲线 = 这条分支从父分支的哪个提交分出来。
    颜色分两层：主色 = 我的部分；另一个色系（--up）= 上游作者的部分（主线里我接手前的那一截、
    以及分叉点落在上游提交上的曲线）。"""
    lanes = topo_lanes(repo["branches"], repo["main"])
    if not lanes:
        return ""
    win = graph_window(repo)
    if not win:
        return ""
    names = [b["name"] for b in lanes]
    idx = {n: i for i, n in enumerate(names)}
    x0, x1, win0, x0_full = win["x0"], win["x1"], win["win0"], win["x0_full"]
    up = upstream_split(repo)                      # 主线上的上游段（我接手之前的那一截）
    # 左侧标签区按最长标签动态留宽（monospace 11.5px ≈ 7.8px/字符），右侧只放「我/共」计数
    label_chars = max([len(lane_label(b["name"])) for b in lanes] or [10])
    pad_l = int(16 + label_chars * 7.8 + 14)
    pad_r, pad_t, lane_h = 62, 30, 34
    height = pad_t + lane_h * len(lanes) + 32
    plot_w = width - pad_l - pad_r
    x_right = pad_l + plot_w

    def px(day):
        if not day:
            return pad_l
        return max(pad_l, pad_l + (dnum(day) - x0) / (x1 - x0) * plot_w)   # 窗口外的日期一律贴到最左边

    def y_of(i):
        return pad_t + lane_h * i

    base_y = pad_t + lane_h * len(lanes) - lane_h // 2 + 10
    out = ['<svg viewBox="0 0 %d %d" role="img" aria-label="%s 分支拓扑图">'
           % (width, height, esc(repo["key"]))]

    # 纵向刻度（时间）+ 底部基线
    out.append('<line class="g-axis" x1="%d" y1="%d" x2="%.0f" y2="%d"/>'
               % (pad_l, base_y, x_right, base_y))
    # 时间轴刻度：跨度小时显示「月/日」，跨度大时显示「年-月」，避免一串相同文字
    def axis_label(d):
        return "%d/%d" % (d.month, d.day) if (x1 - x0) <= 90 else "%d-%02d" % (d.year, d.month)

    for frac in (0, 0.25, 0.5, 0.75, 1):
        x = pad_l + plot_w * frac
        d = from_ord(int(round(x0 + (x1 - x0) * frac)))
        anchor = "start" if frac == 0 else ("end" if frac == 1 else "middle")
        out.append('<line class="g-axis" x1="%.0f" y1="%d" x2="%.0f" y2="%d" opacity="0.5"/>'
                   % (x, pad_t - 14, x, base_y))
        out.append('<text class="g-axis-label" x="%.0f" y="%d" text-anchor="%s">%s</text>'
                   % (x, base_y + 20, anchor, axis_label(d)))
    if x0 > x0_full:                               # 有被折叠的历史，左侧标一下
        out.append('<text class="g-axis-label" x="%d" y="%d" text-anchor="start">更早已经折叠 ↦</text>'
                   % (pad_l, pad_t - 16))

    parent_of = {b["name"]: (b.get("parent") or "") for b in lanes}
    fork_of = {b["name"]: (b.get("fork_date") or "")[:10] for b in lanes}

    def own_branch(c):
        """同一提交会出现在多条包含它的分支里，归给拓扑上最深、真正提交它的那条。"""
        names = [n for n in c["branches"] if n in idx]
        cands = [n for n in names if parent_of.get(n) not in names]
        if not cands:
            return names[0] if names else ""
        cands.sort(key=lambda n: fork_of.get(n, ""), reverse=True)
        return cands[0]

    # 我的提交按「天」聚合到它真正所在的分支（同一天多条只画一颗，半径随数量增大）
    per_day = collections.defaultdict(collections.Counter)
    for c in repo["commits"]:
        ob = own_branch(c)
        if ob:
            per_day[ob][c["day"]] += 1

    nmax = max([max(c.values()) for c in per_day.values()] + [1])
    DOT_GAP = 1.8                                  # 同一泳道上圆点之间至少留出的缝
    TAGS_MINE = ("dot",)                           # 「我的提交点」：球径会按当天条数缩放
    TAG_ORDER = {"dot": 0, "past": 0, "head": 1}   # 同 x 时的绘制次序：提交点 / 折叠点 → HEAD

    def tag_rank(tag):
        return TAG_ORDER.get(tag, 1)

    def radius(n):
        """提交点半径：按本仓库单日最高条数归一（3.0 ~ 7.4px），8 条和 13 条能看出高下。"""
        return 3.0 + 4.4 * math.sqrt(max(1, n) / nmax)

    # 不再把挨太近的几天并成一颗「合并点」：圆点始终是一天一颗，位置不够时先由 lane_scale
    # 整条泳道缩球径、再由 layout 往后推几像素 —— 少一种符号，图也更好认。

    def lane_scale(items):
        """同一条泳道里点挨得太近时，整条泳道同比缩小球径（最低 0.55 倍）：
        球心仍落在真实日期上，比硬把点推开更好看、也更好分辨。"""
        its = sorted(items, key=lambda t: (t[0], tag_rank(t[2])))
        s = 1.0
        for a, b in zip(its, its[1:]):
            room, total = b[0] - a[0] - DOT_GAP, a[1] + b[1]
            if total > 0 and room < total:
                s = min(s, room / total)
        return max(0.55, min(1.0, s))

    def layout(items):
        """一条泳道内从左往右摆点：间距不足 r1+r2+gap 就往后推，整条不越过右侧界线。
        items = [(x, r, tag, tip)]，返回排布后的新列表。"""
        placed, prev = [], None
        for x, r, tag, tip in sorted(items, key=lambda t: (t[0], tag_rank(t[2]))):
            if prev is not None and x < prev[0] + prev[1] + r + DOT_GAP:
                x = prev[0] + prev[1] + r + DOT_GAP
            placed.append((x, r, tag, tip))
            prev = (x, r)
        over = placed[-1][0] - x_right
        if over > 0:                               # 越界就整条左移，最左只贴到 pad_l
            shift = min(over, placed[0][0] - pad_l)
            placed = [(x - shift, r, t, p) for x, r, t, p in placed]
        return placed

    # ---- 第一遍：每条泳道把「我的提交点 / 折叠点 / HEAD」摆到一条线上，互不贴住 ----
    laid = {}
    for b in lanes:
        name = b["name"]
        mine_days = sorted(per_day.get(name, {}).items())
        items = []
        past = [(d, n) for d, n in mine_days if d < win0]
        if past:                                   # 窗口之外的提交：最左边一颗小虚线圆，明细进悬浮框
            items.append((pad_l, 3.2, "past",
                          "%s ~ %s 的 %d 次提交已折叠（拓扑图只画最近两个月）"
                          % (past[0][0], past[-1][0], sum(n for _d, n in past))))
        for d, n in mine_days:
            if d >= win0:                          # 一天一颗，不再把挨太近的几天并成一颗
                items.append((px(d), radius(n), "dot", "%s：%d 次提交" % (d, n)))
        items.append((px((b.get("head_date") or "")[:10]), 4.6, "head",
                      "%s ｜ HEAD %s @ %s ｜ 本分支我提交 %d 条"
                      % (name, b["head"], (b.get("head_date") or "")[:10], b["commits_mine"])))
        s = lane_scale([it for it in items if it[2] in TAGS_MINE])
        # 只有提交点会缩放（折叠点 / HEAD 大小固定），间距不足时再由 layout 往后推
        laid[name] = layout([(x, max(1.2, r * s), t, p) if t in TAGS_MINE else (x, r, t, p)
                             for x, r, t, p in items])

    # ---- 第二遍：画泳道线 + 分叉曲线 + 两种圆点 ----
    nodes, heads, pasts = [], [], []
    for i, b in enumerate(lanes):
        name, y = b["name"], y_of(i)
        root = not parent_of[name]
        placed = laid[name]
        xs = [x for x, _r, _t, _p in placed]
        lane_x1, lane_x2 = min(xs), max(xs)
        if up and name == up["lane"]:               # 主线：左边那一截（我接手之前）是上游作者的
            era_x1 = lane_x2 if not up["day"] else min(lane_x1, max(pad_l, px(up["day"])))
            if era_x1 > pad_l + 0.5:                # 上游段：从窗口左界一直画到我的第一颗圆点
                out.append('<line class="g-lane g-lane-main g-lane-up" x1="%d" y1="%d" x2="%.0f" y2="%d"/>'
                           % (pad_l, y, era_x1, y))
            if era_x1 < lane_x2 - 0.5:              # 我的那一截：接手之后到分支末尾
                out.append('<line class="g-lane g-lane-main" x1="%.0f" y1="%d" x2="%.0f" y2="%d"/>'
                           % (era_x1, y, lane_x2, y))
        else:
            out.append('<line class="%s" x1="%.0f" y1="%d" x2="%.0f" y2="%d"/>'
                       % ("g-lane g-lane-main" if root else "g-lane", lane_x1, y, lane_x2, y))

        # 分叉：从父泳道上「分叉那个提交」的位置弯到本泳道 —— 落到本泳道第一颗不早于该提交的圆点上
        if not root and parent_of[name] in idx:
            fx, py = px(fork_of[name]), y_of(idx[parent_of[name]])
            land = next((x for x in sorted(xs) if x >= fx - 0.5), min(xs))
            span = land - fx
            c = max(13.0, abs(span) * 0.5) * (1.0 if span >= 0 else -1.0)   # 太短就拐个小弯，别画成直线
            out.append('<path class="g-fork%s" d="M %.0f %d C %.0f %d, %.0f %d, %.0f %d"/>'
                       % (" g-fork-up" if fork_owner(repo, b) == "up" else "",
                          fx, py, fx + c, py, land - c, y, land, y))

        for x, r, tag, tip in placed:
            if tag == "head":                      # 分支 HEAD：空心圆
                heads.append('<circle class="g-node-head" cx="%.1f" cy="%d" r="%.1f" data-tip="%s"/>'
                             % (x, y, r, esc(tip)))
            elif tag == "past":                    # 窗口之外的提交：小虚线圆
                pasts.append('<circle class="g-node-past" cx="%.1f" cy="%d" r="%.1f" data-tip="%s"/>'
                             % (x, y, r, esc(tip)))
            else:                                  # 我当天的提交（越大当天越多）
                nodes.append('<circle class="g-node" cx="%.1f" cy="%d" r="%.1f" data-tip="%s"/>'
                             % (x, y, r, esc(tip)))

        mine = per_day.get(name, {})
        tip = "%s ｜ 我 %d 条 / 分支共 %d 条" % (name, b["commits_mine"], b["commits_total"])
        if mine:
            tip += " ｜ 图中圆点 %s ~ %s" % (min(mine), max(mine))
        if root:
            tip += " ｜ 主分支（图的根）"
            if up and up["day"]:                   # 主线左端那截蓝线 = 上游作者的 main
                tip += " ｜ 左边蓝色那一截是上游作者的：%s 之前这条线上还没有我的提交" % up["day"]
            elif up:
                tip += " ｜ 这条线上还没有我的提交，整条都是上游作者的"
        elif parent_of[name]:
            tip += " ｜ 从 %s 的 %s（%s）分出%s" % (
                parent_of[name], b.get("fork_point"), fork_of[name],
                "，那个提交是上游作者的" if fork_owner(repo, b) == "up" else "")
        if b.get("merged_into"):
            tip += " ｜ 已并回 %s" % b["merged_into"]
        out.append('<text class="g-label" x="16" y="%d" data-tip="%s">%s</text>'
                   % (y + 4, esc(tip), esc(lane_label(name))))
        out.append('<text class="g-meta" x="%d" y="%d" text-anchor="end">%d / %d</text>'
                   % (width - 8, y + 4, b["commits_mine"], b["commits_total"]))

    # 后画的压在上面：折叠点 → 提交圆点 → HEAD 空心圆
    out.extend(pasts)
    out.extend(nodes)
    out.extend(heads)
    out.append("</svg>")
    return "".join(out)


def html_stats(repo):
    s = repo["stats"]
    cards = [
        ("is-accent", s["mine"], "次", "我的提交"),
        ("", len(repo["branches"]), "个", "涉及分支"),
        ("", s["active_days"], "天", "有提交的日子"),
        ("", s["merges"], "次", "分支合并"),
        ("", sum(1 for c in repo["commits"] if c["files"]), "条", "含文件改动的提交"),
        ("", s["total_branch_commits"], "条", "仓库分支提交总量"),
    ]
    return "".join(
        '<div class="stat %s"><div class="stat-num">%s<span class="unit">%s</span></div>'
        '<div class="stat-label">%s</div></div>' % (cls, num, unit, label)
        for cls, num, unit, label in cards)


def html_month_bars(repos):
    """每月提交量柱状图（纯 CSS 柱）。"""
    months = collections.Counter()
    for r in repos:
        for m, n in r["stats"]["months"].items():
            months[m] += n
    if not months:
        return '<div class="empty">暂无数据</div>'
    top = max(months.values())
    cols = []
    for m in sorted(months):
        h = max(4, round(months[m] / top * 100))
        cols.append('<div class="bar-col" data-tip="%s：%d 次提交">'
                    '<span class="bar-val">%d</span>'
                    '<span class="bar" style="height:%d%%"></span>'
                    '<span class="bar-label">%s</span></div>'
                    % (ym_label(m), months[m], months[m], h, m[2:]))
    return '<div class="bars">%s</div>' % "".join(cols)


HEAT_WEEKS = 52         # 热力图的显示范围：最近这么多周（右端＝本周）。更早的提交落在范围之外


def heat_counter(repos):
    """所有仓库按天汇总的提交数。"""
    counter = collections.Counter()
    for r in repos:
        for c in r["commits"]:
            counter[c["day"]] += 1
    return counter


def heat_days(counter):
    """热力图显示范围里的每天（Monday → 今天）。
    右端＝本周，左端＝最近 HEAT_WEEKS 周、但不早于第一次提交所在周：
    这样页面放久了「近一年没提交」也能如实显示成一片空白，而不是把老提交一直摆在右边。"""
    today = datetime.date.today()
    start = week_start(today.isoformat()) - datetime.timedelta(days=7 * (HEAT_WEEKS - 1))
    if counter:
        start = max(start, week_start(min(counter)))
    days, d = [], start
    while d <= today:
        days.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return days, start


def snake_button(repos):
    """热力图贪吃蛇按钮：显示范围里一颗亮点都没有（近期没提交）就直接禁用掉。"""
    counter = heat_counter(repos)
    days, _start = heat_days(counter)
    ico = '<i class="snake-ico">%s</i>' % icon("snake")
    if not any(counter.get(d) for d in days):
        return ('<button class="snake-btn" type="button" id="snakeBtn" disabled'
                ' title="热力图的显示范围里没有提交，暂时没得吃">%s<span>暂无可吃</span></button>' % ico)
    return ('<button class="snake-btn" type="button" id="snakeBtn">%s'
            '<span>玩一下贪吃蛇</span></button>' % ico)


def html_heatmap(repos):
    """按周 × 星期的活跃热力图（CSS grid，列=周，行=周一~周日）；只画最近的显示范围。"""
    counter = heat_counter(repos)
    days, _start = heat_days(counter)
    if not days:
        return '<div class="empty">暂无数据</div>'

    cells, axis, seen_month, week = [], [], set(), None
    for d in days:
        wd = week_start(d)
        if wd != week:                             # 换周了：补一格月份标签（同月就留空，免得一串重复）
            week = wd
            m = d[:7]
            axis.append('<span>%d月</span>' % int(m[5:7]) if m not in seen_month else '<span></span>')
            seen_month.add(m)
        n = counter.get(d, 0)
        lv = 0 if n == 0 else (1 if n == 1 else 2 if n <= 3 else 3 if n <= 6 else 4)
        if n:                                      # 有提交的格子可点：直接跳到时间线那一天
            cells.append('<i data-l="%d" data-day="%s" role="button" tabindex="0"'
                         ' data-tip="%s：%d 次提交（点一下跳到当天）"></i>' % (lv, d, d, n))
        else:
            cells.append('<i data-l="0" data-tip="%s：无提交"></i>' % d)

    return ('<div class="heat-wrap"><div class="heat-inner">'
            '<div class="heat">%s</div><div class="heat-axis">%s</div>'
            '</div></div>') % ("".join(cells), "".join(axis))


def html_kind_bars(repos):
    kinds = collections.Counter()
    for r in repos:
        kinds.update(r["stats"]["kinds"])
    if not kinds:
        return '<div class="empty">暂无数据</div>'
    top = max(kinds.values())
    rows = []
    for k in KIND_ORDER:
        if not kinds.get(k):
            continue
        rows.append('<div class="kind-row" data-tip="%s：%d 次">'
                    '<span class="kind-name"><i class="kd kd-%s"></i>%s</span>'
                    '<span class="kind-track"><i class="kd-%s" style="width:%d%%"></i></span>'
                    '<span class="kind-val">%d</span></div>'
                    % (KIND_LABEL[k], kinds[k], k, KIND_LABEL[k], k,
                       max(3, round(kinds[k] / top * 100)), kinds[k]))
    return "".join(rows)


def html_ext_bars(repos):
    """涉及文件类型（按被改动文件的后缀统计）。"""
    exts = collections.Counter()
    for r in repos:
        exts.update(r["stats"]["exts"])
    if not exts:
        return '<div class="empty">暂无数据</div>'
    top = max(exts.values())
    rows = []
    for e, n in exts.most_common(8):
        rows.append('<div class="kind-row" data-tip="%s：%d 次文件改动">'
                    '<span class="kind-name"><code>%s</code></span>'
                    '<span class="kind-track"><i class="kd-other" style="width:%d%%"></i></span>'
                    '<span class="kind-val">%d</span></div>'
                    % (esc(e), n, esc(e), max(3, round(n / top * 100)), n))
    return "".join(rows)


WEEK_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def html_timeline(repos):
    """按天分组的时间线（倒序）。返回 (html, 提交总数)。"""
    entries = []
    for r in repos:
        for c in r["commits"]:
            entries.append((c["day"], c["date"], r["key"], c))
    entries.sort(key=lambda t: t[1], reverse=True)

    by_day = collections.OrderedDict()
    for day, _date, key, c in entries:
        by_day.setdefault(day, []).append((key, c))

    blocks, total = [], 0
    for day, lst in by_day.items():
        total += len(lst)
        wd = WEEK_CN[datetime.date(*[int(x) for x in day.split("-")]).weekday()]
        rows = []
        for key, c in lst:
            branches = "".join('<span class="tag tag-branch">%s</span>' % esc(b) for b in c["branches"])
            kd = KIND_LABEL.get(c["kind"], c["kind"])
            # AI 判过的那些：如实标一下「AI 判定成 X，原按前缀 / 关键词是 Y」
            tip = (' data-tip="AI 判定的类型：%s（原按前缀 / 关键词是「%s」）"'
                   % (esc(kd), esc(KIND_LABEL.get(c.get("kind_rule"), c.get("kind_rule"))))
                   ) if c.get("kind_rule") else ""
            rows.append(
                '<div class="tl-item" data-repo="%s" data-kind="%s" data-branches="%s">%s'
                '<div class="tl-body">'
                '<div class="tl-msg">%s</div>'
                '<div class="tl-meta"><span class="tag tag-repo" data-repo="%s">%s</span>%s'
                '<span class="tag"%s><i class="kd kd-%s"></i>%s</span>'
                '<a class="tag tag-commit" href="%s/commit/%s" target="_blank" rel="noopener"'
                ' title="在 GitHub 查看这次提交">%s ↗</a><span>%d 个文件</span></div>'
                '</div></div>'
                % (esc(key), esc(c["kind"]),
                   esc(" ".join("%s|%s" % (key, b) for b in c["branches"])), icon("commit"),
                   esc(c["subject"]), esc(key), esc(key), branches,
                   tip, c["kind"], kd,
                   next(r["url"] for r in repos if r["key"] == key), c["short"], c["short"], c["files"]))
        repos_of_day = "".join('<span class="tag tag-repo" data-repo="%s">%s</span>' % (esc(k), esc(k))
                               for k in dict.fromkeys(k for k, _ in lst))
        pid = "tl-%s" % day
        blocks.append('<div class="tl-day" data-day="%s">'
                      '<button class="tl-sum" type="button" aria-expanded="false" aria-controls="%s">'
                      '%s<span class="tl-date">%s</span><em>%s · %d 次提交</em>%s</button>'
                      '<div class="tl-panel" id="%s"><div class="tl-clip"><div class="tl-items">%s</div></div></div></div>'
                      % (day, pid, icon("chev", "tri"), day, wd, len(lst), repos_of_day,
                         pid, "".join(rows)))
    return "".join(blocks), total


def html_branch_table(repo):
    rows = []
    for b in repo["branches"]:
        if b["name"] == repo["main"]:
            fork = "图根（主分支）"
        elif not b.get("parent"):
            fork = "独立分支（无共同祖先）"
        else:
            fork = "从 %s 分出（%s @ %s）" % (b["parent"], b["fork_point"], (b["fork_date"] or "")[:10])
            if b.get("merged_into"):
                fork += "，已并回 %s" % b["merged_into"]
        span = "—" if not b["first"] else (b["first"] if b["first"] == b["last"]
                                           else "%s ~ %s" % (b["first"], b["last"]))
        rows.append('<tr><td><code>%s</code></td><td class="num">%d</td><td class="num">%d</td>'
                    '<td>%s</td><td>%s</td><td><code>%s</code></td></tr>'
                    % (esc(b["name"]), b["commits_mine"], b["commits_total"],
                       esc(fork), span, esc(b["head"])))
    return ('<div class="bwrap"><table class="btable"><thead><tr>'
            '<th>分支</th><th class="num">我的提交</th><th class="num">分支提交</th>'
            '<th>分叉自</th><th>我的提交时间</th><th>HEAD</th>'
            '</tr></thead><tbody>%s</tbody></table></div>') % "".join(rows)


# ==================== 组装页面 ====================
total_mine = sum(r["stats"]["mine"] for r in repos)
all_days = sorted({c["day"] for r in repos for c in r["commits"]})
file_touches = sum(c["files"] for r in repos for c in r["commits"])
total_branches = sum(len(r["branches"]) for r in repos)
repo_commits = sum(r["stats"]["total_branch_commits"] for r in repos)
span_first, span_last = (all_days[0], all_days[-1]) if all_days else ("", "")
span_days = (dnum(span_last) - dnum(span_first) + 1) if all_days else 0

overview_cards = [
    ("is-accent", total_mine, "次", "我提交的记录"),
    ("", len(all_days), "天", "有提交的日子"),
    ("", total_branches, "个", "涉及分支（含主线）"),
    ("", span_days, "天", "跨度 %s → %s" % (span_first[5:], span_last[5:])),
    ("", file_touches, "次", "文件改动（同文件多次计数）"),
    ("", repo_commits, "条", "两仓库分支提交总量"),
]
stats_html = "".join(
    '<div class="stat %s"><div class="stat-num">%s<span class="unit">%s</span></div>'
    '<div class="stat-label">%s</div></div>' % (cls, num, unit, label)
    for cls, num, unit, label in overview_cards)

split_html = "".join('<i style="width:%.1f%%"></i>' % (r["stats"]["mine"] / total_mine * 100) for r in repos)
split_legend = "".join(
    '<span><i class="dot dot-%d"></i><b>%s</b> %d 次（%.0f%%）</span>'
    % (i + 1, esc(r["key"]), r["stats"]["mine"], r["stats"]["mine"] / total_mine * 100)
    for i, r in enumerate(repos))

def graph_legend(repo):
    """拓扑图图例：跟着实际画出来的东西走 —— 没画上游段就不放蓝实线，没有上游分叉就不放蓝虚线。"""
    up = upstream_split(repo)
    forks = fork_stats(repo)
    chips = ['<span><span class="lg-main"></span>主线（%s）</span>' % esc(repo["main"])]
    if up:
        chips.append('<span><span class="lg-up"></span>上游作者的 %s</span>' % esc(repo["main"]))
    chips.append('<span><span class="lg-lane"></span>子分支</span>')
    if forks["mine"]:
        chips.append('<span><span class="lg-fork"></span>从我的提交分出</span>')
    if forks["up"]:
        chips.append('<span><span class="lg-fork-up"></span>从上游的提交分出</span>')
    chips.append('<span class="lg-note">圆点 = 我当天的提交（越大越多）｜ <span class="lg-head"></span>分支 HEAD ｜ '
                 '<span class="lg-past"></span>两个月前已折叠 ｜ 右侧数字 = 我 / 分支共</span>')
    chips.append('<span class="lg-hint">← 手机上可左右滑动</span>')
    return '<div class="g-legend">%s</div>' % "".join(chips)


graph_cards = "".join(
    '<div class="chart-card">'
    '<div class="chart-head"><span class="chart-title">%s</span>'
    '<span class="chart-sub">%d 个分支 ｜ 我 %d 条 ｜ %s ~ %s</span></div>'
    '<div class="graph-wrap">%s</div>%s</div>'
    % (esc(r["key"]), len(r["branches"]), r["stats"]["mine"], r["stats"]["first"], r["stats"]["last"],
       svg_branch_graph(r), graph_legend(r))
    for r in repos)

branch_tables = "".join(
    '<div class="chart-card"><div class="chart-head"><span class="chart-title">%s 分支明细</span>'
    '<span class="chart-sub">按「我的提交」排序</span></div>%s</div>'
    % (esc(r["key"]),
       html_branch_table(dict(r, branches=sorted(r["branches"], key=lambda b: -b["commits_mine"]))))
    for r in repos)


def html_filters(repos):
    kinds = collections.Counter()
    for r in repos:
        kinds.update(r["stats"]["kinds"])
    out = ['<div class="filters" role="group" aria-label="按仓库筛选">',
           '<button class="filter-btn is-on" type="button" data-filter="repo" data-value="all">全部仓库</button>']
    for r in repos:
        out.append('<button class="filter-btn" type="button" data-filter="repo" data-value="%s">%s（%d）</button>'
                   % (esc(r["key"]), esc(r["key"]), r["stats"]["mine"]))
    out.append("</div>")

    # 分支筛选：夹在仓库和类型之间；没选具体仓库时整组收着，选了仓库才把这一仓库的分支动画显示出来
    out.append('<div class="filters filters-branch" role="group" aria-label="按分支筛选">')
    out.append('<button class="filter-btn is-on" type="button" data-filter="branch" data-value="all" style="--i:0">全部分支</button>')
    pairs = [(r["key"], b) for r in repos for b in r["branches"]]
    for n, (key, b) in enumerate(sorted(pairs, key=lambda kb: -kb[1]["commits_mine"]), 1):
        out.append('<button class="filter-btn" type="button" data-filter="branch" data-repo="%s"'
                   ' data-value="%s|%s" style="--i:%d"'
                   ' data-tip="%s ｜ 分支 %s ｜ 我的提交 %d 条（分支共 %d 条）">'
                   '<i class="bd"></i>%s（%d）</button>'
                   % (esc(key), esc(key), esc(b["name"]), n, esc(key), esc(b["name"]),
                      b["commits_mine"], b["commits_total"], esc(b["name"]), b["commits_mine"]))
    out.append("</div>")

    out.append('<div class="filters" role="group" aria-label="按提交类型筛选">')
    kind_tip = (' data-tip="类型由 Cloudflare Workers AI 逐条读提交信息判定；判不出来的才沿用「前缀 + 关键词」那套规则。"'
                if KIND_FIXED else "")
    out.append('<button class="filter-btn is-on" type="button" data-filter="kind" data-value="all"%s>'
               '全部类型</button>' % kind_tip)
    for k in KIND_ORDER:
        if kinds.get(k):
            out.append('<button class="filter-btn" type="button" data-filter="kind" data-value="%s">'
                       '<i class="kd kd-%s"></i>%s（%d）</button>'
                       % (k, k, KIND_LABEL[k], kinds[k]))
    out.append("</div>")
    return "".join(out)


timeline_html, timeline_total = html_timeline(repos)
REPO_SUB = "、".join(r["key"] for r in repos)

CSS_C = """
  /* 分支拓扑图例：只留「线条怎么画」，圆点含义走一句话说明，不再一项一个图形 */
  .lg-main, .lg-lane, .lg-fork, .lg-up, .lg-fork-up { display: inline-block; width: 18px; height: 0; border-top: 2.8px solid var(--accent); vertical-align: 4px; margin-right: 6px; }
  .lg-lane { border-top-width: 1.8px; border-top-color: var(--border-strong); }
  .lg-fork { border-top-width: 1.4px; border-top-style: dashed; opacity: 0.85; }
  .lg-up { border-top-width: 2.4px; border-top-color: var(--up); }
  .lg-fork-up { border-top-width: 1.4px; border-top-style: dashed; border-top-color: var(--up); opacity: 0.85; }
  .lg-past { display: inline-block; width: 7px; height: 7px; border-radius: 50%; border: 1.4px dashed var(--muted); vertical-align: 1px; margin-right: 5px; }
  .lg-head { display: inline-block; width: 11px; height: 11px; border-radius: 50%; border: 2.2px solid var(--accent); background: var(--bg); vertical-align: -1px; margin-right: 5px; }
  .g-legend .lg-note { color: var(--muted); }
  .lg-hint { display: none; color: var(--accent); }

  /* AI 判定的类型（可选，来自 worklog-kinds.json）：只改类型本身，页面上不加新的一维 */
  .tl-meta .tag[data-tip] { cursor: help; }

  .wl-fresh { display: flex; align-items: center; gap: 8px; margin: 0 0 18px; padding: 10px 13px; border-radius: 10px; font-size: 12.5px; line-height: 1.5; color: var(--muted); background: var(--card); box-shadow: inset 0 0 0 1px var(--border); }
  .wl-fresh[hidden] { display: none; }
  /* 从 KV 取回来的新提交：提示条变成一块小列表（列表为空时完全不出现） */
  .wl-fresh.is-list { display: block; }
  .wl-fresh-list { margin: 8px 0 0; padding: 0; list-style: none; display: grid; gap: 6px; }
  .wl-fresh-list li { display: flex; align-items: center; gap: 8px; min-width: 0; font-size: 12px; }
  .wl-fresh-list .wl-new-day { font-variant-numeric: tabular-nums; opacity: 0.8; }
  .wl-fresh-list .wl-new-sub { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wl-fresh-list a { color: var(--accent); text-decoration: none; white-space: nowrap; }
  .wl-fresh-list a:hover { text-decoration: underline; }
  .wl-fresh-more { margin-top: 8px; opacity: 0.85; }
  .wl-fresh-more a { color: var(--accent); text-decoration: none; }
  .wl-fresh-more a:hover { text-decoration: underline; }
  /* 兜底：元素自己带 display（比如 .filter-btn 的 inline-flex）时 [hidden] 会失效，把它压住 */
  [hidden] { display: none !important; }
  /* 悬浮小卡片（替代浏览器原生 title 提示框）：贴指针右下角，贴边自动翻到另一侧 */
  .tip {
    position: fixed; left: 0; top: 0; z-index: 60;
    max-width: min(78vw, 340px); padding: 6px 10px; border-radius: 9px;
    background: var(--card); color: var(--fg); border: 1px solid var(--border);
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.06), 0 8px 20px rgba(0, 0, 0, 0.35);
    font-size: 12.5px; line-height: 1.45; white-space: pre-line;
    pointer-events: none; opacity: 0; scale: 0.94; translate: 0 3px;
    transform-origin: top left; will-change: opacity, scale;
    transition: opacity 0.16s ease, scale 0.18s var(--ease-out), translate 0.18s var(--ease-out);
  }
  .tip.is-on { opacity: 1; scale: 1; translate: 0 0; }
  .tip.is-wide { max-width: min(88vw, 430px); }
  @media (prefers-reduced-motion: reduce) {
    .tip { transition: opacity 0.1s linear; scale: 1; translate: 0 0; }
  }

  /* 时间线：点击日期行展开（折叠状态自己管，切换筛选后一律收起 + 高度过渡动画） */
  .tl-day { margin-bottom: 6px; scroll-margin-top: 18px; }
  .tl-day::before { top: 14px; }
  /* 切筛选时：要离开的条目先淡出，整段时间线跟着刷一下，留下的条目 / 日期行一个个浮上来
     （日期行也要动：平时日期面板都是收起的，只让条目动的话肉眼看不到，仓库之间切换会像没反应） */
  .tl-item.is-leaving { opacity: 0; transform: translateY(-4px); transition: opacity 0.17s linear, transform 0.17s var(--ease-out); }
  .tl.is-swap .tl-item:not([hidden]):not(.is-leaving),
  .tl.is-swap .tl-day:not([hidden]):not(.is-out) { animation: wl-item-in 0.26s var(--ease-out) backwards; animation-delay: calc(var(--i, 0) * 18ms); }
  @keyframes wl-item-in { from { opacity: 0; transform: translateY(-5px); } }
  /* 日期行退场：先把高度塌到 0 再收起来。否则十几行日期瞬间消失，页面高度「咯噔」掉一截、
     浏览器滚动位置也跟着跳一下（高度由 JS 量好写进行内，这里只负责过渡）。
     这里不用 --ease-out：那条曲线起步太冲，几百像素的高度差会在头一帧就掉掉一小半，看着还是「一顿」 */
  .tl-day.is-out {
    overflow: hidden; margin-bottom: 0; opacity: 0; transform: translateY(-4px);
    --wl-soft: cubic-bezier(0.33, 0.05, 0.2, 1);
    transition: height 0.3s var(--wl-soft), margin-bottom 0.3s var(--wl-soft),
                opacity 0.22s linear, transform 0.3s var(--wl-soft);
  }
  /* 反过来也要有：从「只剩几条」切回「全部」时，日期行要是瞬间冒出来，页面高度一样是一下涨一截。
     is-in = 正在长回来（起点 0 由 JS 写进行内高度），is-out 保留过渡是为了半路反转时能接着往回长 */
  .tl-day.is-in {
    overflow: hidden;
    --wl-soft: cubic-bezier(0.33, 0.05, 0.2, 1);
    transition: height 0.3s var(--wl-soft), margin-bottom 0.3s var(--wl-soft),
                opacity 0.22s linear, transform 0.3s var(--wl-soft);
  }
  /* 从热力图跳过来时给这一天上个高亮，方便一眼看到目标 */
  .tl-day.is-flash > .tl-sum { animation: tlFlash 1.6s var(--ease-out); }
  @keyframes tlFlash {
    0% { box-shadow: 0 0 0 0 color-mix(in srgb, var(--accent) 60%, transparent); background: color-mix(in srgb, var(--accent) 14%, transparent); }
    100% { box-shadow: 0 0 0 16px color-mix(in srgb, var(--accent) 0%, transparent); background: var(--card); }
  }
  @media (prefers-reduced-motion: reduce) { .tl-day.is-flash > .tl-sum { animation: none; } }
  .tl-sum {
    display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
    padding: 8px 11px; border-radius: 11px; cursor: pointer;
    transition: background-color 0.18s ease, box-shadow 0.18s ease;
  }
  .tl-day.is-open > .tl-sum { background: var(--card); box-shadow: var(--shadow-card); }
  .tri { width: 12px; height: 12px; flex: none; color: var(--muted); transition: transform 0.26s var(--ease-out), color 0.18s ease; }
  .tl-day.is-open .tri { transform: rotate(90deg); color: var(--accent); }
  .tl-sum .tl-date { font-size: 13.5px; font-weight: 650; font-variant-numeric: tabular-nums; display: inline; }
  .tl-sum em { font-size: 12px; font-style: normal; color: var(--muted); }
  .tl-sum .tag-repo { margin-left: auto; }
  .tl-sum .tag-repo + .tag-repo { margin-left: 0; }
  .tl-sum .tag-repo[hidden] + .tag-repo { margin-left: auto; }   /* 收起别的仓库的标签后，剩下的那个接着靠右 */
  .tl-day .tl-items { margin-top: 9px; }
  /* 折叠动画：网格行高 0fr → 1fr，内容由 .tl-clip 裁剪（多一层为了把 margin 也裁掉） */
  .tl-panel { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.36s var(--ease-out); }
  .tl-clip { overflow: hidden; min-height: 0; opacity: 0; transition: opacity 0.24s ease; }
  .tl-day.is-open > .tl-panel { grid-template-rows: 1fr; }
  .tl-day.is-open > .tl-panel > .tl-clip { opacity: 1; }
  @media (prefers-reduced-motion: reduce) {
    .tl-panel, .tl-clip, .tri { transition: none; }
    .tl.is-swap .tl-item, .tl.is-swap .tl-day { animation: none; }
    .tl-day.is-out, .tl-day.is-in { transition: none; }
  }
  @media (hover: hover) and (pointer: fine) {
    .tl-sum:hover { background: var(--card-hover); }
  }
  @media (hover: none), (pointer: coarse) {
    .tl-sum:active { background: var(--card-hover); }
  }
  .tag-commit { font-family: ui-monospace, Consolas, monospace; color: var(--fg); }
  @media (hover: hover) and (pointer: fine) {
    .tag-commit:hover { background: var(--accent-soft); color: var(--accent); }
    .tag-branch:hover { color: var(--fg); }
  }
  @media (max-width: 640px) {
    .lg-hint { display: inline; }
    .graph-wrap { box-shadow: inset -22px 0 16px -18px color-mix(in srgb, var(--fg) 35%, transparent); }
    /* 窄屏别把整张图压到看不清：给个更大的最小宽度，左右滑动看（下面图例里已经写了这句提示） */
    .graph-wrap svg { min-width: 760px; }
  }

  /* ---------- 字体：英文与数字优先 Inter，中文优先苹方 / 鸿蒙 / 思源，等宽优先 JetBrains Mono / Cascadia ---------- */
  body {
    font-family: "Inter var", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text",
                 "Segoe UI", Roboto, "Helvetica Neue", "PingFang SC", "HarmonyOS Sans SC",
                 "Noto Sans SC", "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
  }
  code, kbd, .tag-branch, .tag-commit, .g-label, .g-meta, .tl-meta code, .btable td code {
    font-family: "JetBrains Mono", "Cascadia Code", "Cascadia Mono", "SF Mono", ui-monospace,
                 Menlo, Consolas, "Liberation Mono", monospace;
    font-variant-ligatures: none;
  }
"""

FILTER_JS = """
      /* ---------- 工作日志：悬浮小卡片（替代浏览器原生 title 提示框） ---------- */
      (function () {
        var tip = document.createElement('div');
        tip.className = 'tip';
        document.body.appendChild(tip);
        var target = null, dir = 1, timer = null;

        /* 贴指针右下方 16 / 18px；右侧放不下翻到左侧，底部放不下翻到上方 */
        function fit(ev) {
          var pad = 8, w = tip.offsetWidth, h = tip.offsetHeight;
          var vw = window.innerWidth, vh = window.innerHeight;
          var right = ev.clientX + 16, left = ev.clientX - w - 16;
          var canRight = right + w <= vw - pad + (dir > 0 ? 20 : 0);   /* 滞回：已靠右就不再左右抖 */
          dir = canRight ? 1 : -1;
          var x = canRight ? right : left;
          if (x + w > vw - pad) x = vw - w - pad;
          if (x < pad) x = pad;
          var y = ev.clientY + 18;
          if (y + h > vh - pad) y = ev.clientY - h - 16;
          if (y < pad) y = pad;
          tip.style.left = x + 'px';
          tip.style.top = y + 'px';
        }

        function show(el, ev, hold) {
          target = el;
          var text = el.getAttribute('data-tip') || '';
          tip.textContent = text;
          tip.classList.toggle('is-wide', text.length > 34);
          fit(ev);                       /* 先摆好位置再淡入，出现时不会从旧位置滑过来 */
          tip.classList.add('is-on');
          clearTimeout(timer);
          if (hold) timer = setTimeout(hide, 2600);   /* 触屏：停留 2.6 秒自动收起 */
        }

        function hide() {
          target = null;
          clearTimeout(timer);
          tip.classList.remove('is-on');
        }
        window.__tipHide = hide;         /* 点热力图跳转时顺手收掉小卡片 */

        function toEl(node) {
          return node && node.closest ? node.closest('[data-tip]') : null;
        }

        /* 只有触屏 / 手写笔走「点一下显示」，其余（含 pointerType 为空的合成事件）都按鼠标 hover 处理 */
        function isTouch(ev) { return ev.pointerType === 'touch' || ev.pointerType === 'pen'; }

        document.addEventListener('pointerover', function (ev) {
          var el = toEl(ev.target);
          if (!isTouch(ev) && el && el !== target) show(el, ev, false);
        });
        document.addEventListener('pointerout', function (ev) {
          if (isTouch(ev)) return;
          if (target && (!ev.relatedTarget || !target.contains(ev.relatedTarget))) hide();
        });
        document.addEventListener('pointermove', function (ev) {
          if (target && !isTouch(ev)) fit(ev);
        });
        document.addEventListener('pointerdown', function (ev) {
          if (!isTouch(ev)) return;
          var el = toEl(ev.target);      /* 触屏没有 hover，点一下显示 2.6 秒 */
          if (el) show(el, ev, true); else hide();
        });
        window.addEventListener('scroll', hide, { passive: true });
        window.addEventListener('resize', hide, { passive: true });
      })();

      /* ---------- 工作日志：时间线折叠（点日期行展开 / 收起） ---------- */
      (function () {
        var days = Array.prototype.slice.call(document.querySelectorAll('.tl-day'));
        if (!days.length) return;
        window.__wlCollapseAll = function () {
          days.forEach(function (day) {
            day.classList.remove('is-open');
            var b = day.querySelector('.tl-sum');
            if (b) b.setAttribute('aria-expanded', 'false');
          });
        };
        days.forEach(function (day) {
          var btn = day.querySelector('.tl-sum');
          if (!btn) return;
          btn.addEventListener('click', function () {
            var open = day.classList.toggle('is-open');
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
          });
        });
      })();

      /* ---------- 工作日志：时间线筛选（仓库 / 类型），每次切换都回到全部折叠 ---------- */
      (function () {
        var items = Array.prototype.slice.call(document.querySelectorAll('.tl-item'));
        var days = Array.prototype.slice.call(document.querySelectorAll('.tl-day'));
        var tags = Array.prototype.slice.call(document.querySelectorAll('.tag-repo[data-repo]'));
        if (!items.length) return;
        var state = { repo: 'all', branch: 'all', kind: 'all' };
        var branchGroup = document.querySelector('.filters-branch');
        var branchBtns = Array.prototype.slice.call(document.querySelectorAll('[data-filter="branch"]'));

        function syncBranch() {                        /* 选了具体仓库，才把「全部分支」和这一仓库的分支按钮动画显示出来 */
          var all = (state.repo === 'all');
          if (all) state.branch = 'all';
          branchBtns.forEach(function (b) {
            var mine = !b.dataset.repo || b.dataset.repo === state.repo;   /* 没有 data-repo 的那颗 = 本仓库全部分支 */
            b.hidden = all || !mine;
            b.classList.toggle('is-on', !all && b.dataset.value === state.branch);
          });
          if (branchGroup) branchGroup.classList.toggle('is-on', !all);
        }

        function match(el) {
          return (state.repo === 'all' || el.dataset.repo === state.repo)
              && (state.kind === 'all' || el.dataset.kind === state.kind)
              && (state.branch === 'all'
                  || (' ' + (el.dataset.branches || '') + ' ').indexOf(' ' + state.branch + ' ') >= 0);
        }

        function flip() {                              /* 落地：换 hidden、仓库标签、重置折叠，顺便给可见条目排好入场的先后 */
          items.forEach(function (el) {
            el.classList.remove('is-leaving');
            el.hidden = !match(el);
          });
          settleDays();                                /* 塌到 0 的日期行在这里正式收起来 */
          syncLabels();
          syncDays();
          stageItems();
          stageDays();
          if (window.__wlCollapseAll) window.__wlCollapseAll();
        }

        function syncLabels() {                        /* 按仓库筛选时只收起别的仓库的标签，本仓库的留着 */
          tags.forEach(function (t) {
            t.hidden = (state.repo !== 'all' && t.dataset.repo !== state.repo);
          });
        }

        function willStay(day) {                       /* 这一天筛完还有内容吗 */
          return !!day.querySelector('.tl-item:not([hidden]):not(.is-leaving)');
        }

        var growPend = null;
        function waitGrow() {                          /* 0.3s 的过渡，留点余量再把手写高度交还给内容 */
          if (growPend) clearTimeout(growPend);
          growPend = setTimeout(endGrow, 340);
        }

        function endGrow() {                           /* 长回自然高度了：清掉行内高度 / 外边距和过渡类 */
          if (growPend) { clearTimeout(growPend); growPend = null; }
          days.forEach(function (day) {
            if (!day.classList.contains('is-in') || day.classList.contains('is-out')) return;
            day.classList.remove('is-in');
            day.style.height = '';
            day.style.marginBottom = '';
          });
        }

        function collapseDay(day) {                    /* 塌到 0：先记下当前高度，动画走完（settleDays）再收起来 */
          if (day.classList.contains('is-in')) {
            day.classList.remove('is-in');
            day.style.marginBottom = '';
          }
          var h = day.getBoundingClientRect().height;  /* 不取整：十几行取整凑起来能差出几个像素 */
          day.dataset.h = h;
          day.dataset.mb = parseFloat(getComputedStyle(day).marginBottom) || 0;
          day.style.height = h + 'px';
          void day.offsetHeight;                       /* 让浏览器认下这个起点 */
          day.classList.add('is-out');
          day.style.height = '0px';
        }

        function unCollapse(day) {                     /* 塌到一半又轮到自己了：按记下的高度接着往回长 */
          day.classList.remove('is-out');
          day.classList.add('is-in');
          day.style.height = (parseFloat(day.dataset.h) || 0) + 'px';
          day.style.marginBottom = (parseFloat(day.dataset.mb) || 0) + 'px';
          waitGrow();
        }

        function growDay(day) {                        /* 本来收着的日期行要冒出来：先定住 0，再长回自然高度 */
          day.hidden = false;
          if (day.classList.contains('is-open')) return;   /* 面板开着量不准，直接露出来（少见） */
          day.classList.remove('is-out');
          day.style.height = '';
          day.style.marginBottom = '';
          var h = day.getBoundingClientRect().height;  /* 松开量一下自然高度 */
          var mb = parseFloat(getComputedStyle(day).marginBottom) || 0;
          day.style.height = '0px';
          day.style.marginBottom = '0px';
          void day.offsetHeight;                       /* 认下 0 这个起点 */
          day.classList.add('is-in');
          day.style.height = h + 'px';
          day.style.marginBottom = mb + 'px';
          waitGrow();
        }

        function syncDays() {                          /* 计数跟着筛选走；日期行两头都走高度过渡
                                                          （瞬间出现 / 瞬间消失，页面高度会一下子涨落，
                                                            浏览器滚动位置也跟着跳，所以要「塌下去 + 长回来」） */
          var out = 0;
          days.forEach(function (day) {
            var em = day.querySelector('.tl-sum em');
            var n = day.querySelectorAll('.tl-item:not([hidden]):not(.is-leaving)').length;
            if (em) {
              if (!em.dataset.wd) em.dataset.wd = em.textContent.split('·')[0].trim();   /* 星期几固定，只在第一次取 */
              em.textContent = em.dataset.wd + ' · ' + n + ' 次提交';
            }
            if (n) {                                       /* 还有内容：该露的露出来，正在退场的往回长 */
              if (day.hidden) growDay(day);
              else if (day.classList.contains('is-out')) unCollapse(day);
            } else if (!day.hidden && !day.classList.contains('is-out')) {
              collapseDay(day);
              out++;
            }
          });
          return out;
        }

        function settleDays() {                        /* 退场动画走完：塌到 0 的日期行正式收起来 */
          days.forEach(function (day) {
            if (!day.classList.contains('is-out')) return;
            if (!willStay(day)) day.hidden = true;      /* 此时高度已经是 0，收起来不会再跳 */
            day.classList.remove('is-out');
            day.style.height = '';
          });
        }

        function stageItems() {                        /* 入场先后：只数该留 / 该来的，按文档顺序从上往下错开 */
          var n = 0;
          items.forEach(function (el) {
            if (match(el)) el.style.setProperty('--i', Math.min(n++, 14));
          });
        }

        function stageDays() {                         /* 日期行同理（它才是肉眼能看到的那个） */
          var n = 0;
          days.forEach(function (day) {
            if (!day.hidden && day.querySelector('.tl-item:not([hidden]):not(.is-leaving)')) {
              day.style.setProperty('--i', Math.min(n++, 14));
            }
          });
        }

        var pend = null;
        function apply() {                             /* 切筛选：该来的先放出来排好队，要走的淡出，整段时间线跟着刷一下 */
          var tl = document.querySelector('.tl'), out = 0;
          items.forEach(function (el) {                /* 1. 该留 / 该来的：撤销退场、放出来 */
            if (match(el)) { el.classList.remove('is-leaving'); el.hidden = false; }
          });
          stageItems();                                /* 2. 趁动画还没开始，先把错开的延迟写进去 */
          items.forEach(function (el) {                /* 3. 该走的：淡出，170ms 后再真正落地 */
            if (!match(el) && !el.hidden) { el.classList.add('is-leaving'); out++; }
          });
          syncLabels();
          var dayOut = syncDays();
          if (tl) {
            tl.classList.toggle('is-repo', state.repo !== 'all');     /* 只是状态标记，标签的收放交给 syncLabels */
            tl.classList.remove('is-swap');
            void tl.offsetWidth;                                      /* 重排一次，让下面的动画重新播放 */
            tl.classList.add('is-swap');
          }
          stageDays();
          if (pend) { clearTimeout(pend); pend = null; }
          /* 条目淡出 0.17s、日期行塌高度 0.3s，都得等它们走完再落地 */
          if (out || dayOut) pend = setTimeout(function () { pend = null; flip(); }, 320);
          else flip();
        }
        window.__wlFilterNow = function () {           /* 给自检脚本用：立刻把筛选落地，不等退场 / 生长动画 */
          if (pend) { clearTimeout(pend); pend = null; }
          flip();
          endGrow();
        };

        Array.prototype.slice.call(document.querySelectorAll('[data-filter]')).forEach(function (btn) {
          btn.addEventListener('click', function () {
            var group = btn.dataset.filter;
            Array.prototype.slice.call(document.querySelectorAll('[data-filter="' + group + '"]'))
              .forEach(function (b) { b.classList.toggle('is-on', b === btn); });
            state[group] = btn.dataset.value;
            if (group === 'repo') syncBranch();
            apply();
          });
        });

        syncBranch();              /* 初始没选具体仓库：分支那组先收着 */
      })();

      /* ---------- 工作日志：点热力图小方格 → 平滑滚到时间线并展开那一天 ---------- */
      (function () {
        var cells = Array.prototype.slice.call(document.querySelectorAll('.heat i[data-day]'));
        if (!cells.length) return;

        window.__wlGotoDay = function (day) {
          var target = document.querySelector('.tl-day[data-day="' + day + '"]');
          if (!target) return false;
          if (target.hidden) {              /* 被筛选藏起来了：先切回「全部」，否则滚过去也看不见 */
            Array.prototype.slice.call(document.querySelectorAll('[data-filter][data-value="all"]'))
              .forEach(function (b) { b.click(); });
          }
          var sum = target.querySelector('.tl-sum');
          if (sum && !target.classList.contains('is-open')) sum.click();
          if (window.__tipHide) window.__tipHide();
          target.classList.remove('is-flash');
          void target.offsetWidth;          /* 强制重排，让高亮动画能重复播放 */
          target.classList.add('is-flash');
          setTimeout(function () { target.classList.remove('is-flash'); }, 1700);
          var still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
          if (still) {
            target.scrollIntoView({ block: 'start' });
          } else {
            var y0 = window.pageYOffset;
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            setTimeout(function () {          /* 兜底：万一平滑滚动没生效（或被打断），直接到位 */
              if (Math.abs(window.pageYOffset - y0) < 4) {
                target.scrollIntoView({ block: 'start' });
              }
            }, 420);
          }
          return true;
        };

        /* 事件走委托：贪吃蛇把格子重画一遍后照样能点 */
        document.addEventListener('click', function (ev) {
          var cell = ev.target.closest ? ev.target.closest('.heat i[data-day]') : null;
          if (cell) window.__wlGotoDay(cell.dataset.day);
        });
        document.addEventListener('keydown', function (ev) {
          if (ev.key !== 'Enter' && ev.key !== ' ') return;
          var cell = ev.target.closest ? ev.target.closest('.heat i[data-day]') : null;
          if (cell) { ev.preventDefault(); window.__wlGotoDay(cell.dataset.day); }
        });
      })();

      /* ---------- 工作日志：热力图小方格按卡片宽度定尺寸（正方形、列距 = 边长 + 缝，蛇按列走才不偏） ---------- */
      (function () {
        var wrap = document.querySelector('.heat-wrap');
        var grid = wrap && wrap.querySelector('.heat');
        if (!wrap || !grid) return;
        var cols = Math.ceil(grid.querySelectorAll('i[data-l]').length / 7);
        var GAP = 3;
        function fit() {
          if (!cols) return;
          /* 留 10px 余量：蛇头比方格略大、悬停放大也会撑出一点点，别让它们把横向滚动条挤出来 */
          var cell = Math.floor((wrap.clientWidth - (cols - 1) * GAP - 10) / cols);
          wrap.style.setProperty('--wl-cell', Math.max(11, Math.min(26, cell)) + 'px');
        }
        fit();
        window.addEventListener('resize', fit);
      })();

      /* ---------- 工作日志：热力图贪吃蛇（观感参照 Platane/snk：长蛇身、吃掉即淡出、吃完循环重来） ---------- */
      (function () {
        var btn = document.getElementById('snakeBtn');
        var grid = document.querySelector('.heat');
        /* 没有可吃的格子就别开局：data-day 只长在有提交的格子上；服务端觉得没得吃时也已经把按钮禁用了 */
        if (!btn || btn.disabled || !grid || !grid.querySelector('i[data-day]')) return;
        var LEN = 7;                                   /* 蛇身段数（含蛇头） */
        var PAUSE = 2000;                              /* 吃完后停 2 秒再开下一轮 */
        var TARGET = 30000;                            /* 一轮大概走多久（毫秒）：按格子数反推每步时长。
                                                          参考 snk 官方生成的一年是 73700ms 线性跑完，这里取同一个量级，慢到看得清 */
        var STEP_MIN = 55, STEP_MAX = 130;             /* 每步时长上下限：太短是跳格，太长就磨蹭 */
        var HEAD_GROW = 2;                             /* 蛇头比小方格每边大 1px（snk 的蛇头也略大于方格、圆角更圆） */
        var timer = null, layer = null, head = null, segs = [], body = [], snap = null, dir = 'right', running = false;
        var label = btn.querySelector('span');

        function met() {                               /* 量真实几何：方格边长、缝、横/竖走一格差多少像素。
                                                          列宽有可能被 justify-content 拉过，所以直接量第 0、1、7 格的间距 */
          var cells = grid.querySelectorAll('i[data-l]');
          var r0 = cells.length ? cells[0].getBoundingClientRect() : { width: 11, left: 0, top: 0 };
          var r1 = cells[1] ? cells[1].getBoundingClientRect() : r0;
          var r7 = cells[7] ? cells[7].getBoundingClientRect() : r1;
          var gap = parseFloat(window.getComputedStyle(grid).columnGap) || 0;
          return { cell: r0.width, gap: gap,
                   stepX: (r7.left - r0.left) || (r0.width + gap),
                   stepY: (r1.top - r0.top) || (r0.width + gap) };
        }

        function place(el, cur, back, front, m, grow, b) {   /* 放在 cur 格正中；身后/身前哪条轴上有邻居就往那条轴多探 b 像素，
                                                               正好把方格之间的缝盖住（snk 的蛇身也是这样连成一条的）；
                                                               拐角处两条轴都有邻居，就补成一个方块，弯道也不漏缝 */
          var ax = 0, ay = 0;
          [back, front].forEach(function (n) {
            if (!n) return;
            if (n[0] !== cur[0]) ax = 1;
            if (n[1] !== cur[1]) ay = 1;
          });
          var w = m.cell + grow + (ax ? b : 0);
          var h = m.cell + grow + (ay ? b : 0);
          el.style.width = w + 'px';
          el.style.height = h + 'px';
          el.style.left = (cur[0] * m.stepX + (m.cell - w) / 2) + 'px';
          el.style.top = (cur[1] * m.stepY + (m.cell - h) / 2) + 'px';
        }

        function buildPath() {                         /* 路线：从最右列最上的亮点出发，依次走「最短且不撞自己身体」的路去吃下一个亮点 */
          var cells = Array.prototype.slice.call(grid.querySelectorAll('i[data-l]'));
          var cols = Math.ceil(cells.length / 7);
          var lit = [], maxCol = -1, headRow = 7;
          cells.forEach(function (el, i) {
            if (+el.getAttribute('data-l') > 0) {
              var col = Math.floor(i / 7), row = i % 7;
              lit.push([col, row]);
              if (col > maxCol) maxCol = col;
            }
          });
          if (maxCol < 0) return null;                 /* 一颗亮格都没有，没什么可吃 */
          lit.forEach(function (c) { if (c[0] === maxCol && c[1] < headRow) headRow = c[1]; });
          var path = [[maxCol, headRow]], body = [], todo = [], head = path[0];
          lit.forEach(function (c) { if (c[0] !== head[0] || c[1] !== head[1]) todo.push(c); });
          while (todo.length) {
            var bi = 0, bd = -1, i, j, d;
            for (i = 0; i < todo.length; i++) {        /* 先挑离蛇头最近的亮点 */
              d = Math.abs(todo[i][0] - head[0]) + Math.abs(todo[i][1] - head[1]);
              if (bd < 0 || d < bd) { bd = d; bi = i; }
            }
            var leg = route(head, todo[bi], cols, body);
            if (!leg) break;                           /* 被自己围死了，这一轮就吃到这儿 */
            for (i = 0; i < leg.length; i++) {
              path.push(leg[i]);
              body.unshift(leg[i]);                    /* 身体 = 最近走过的 LEN-1 格，蛇头不能踩回去 */
              if (body.length > LEN - 1) body.pop();
              for (j = todo.length - 1; j >= 0; j--) { /* 路过顺手吃掉的亮点也划掉 */
                if (todo[j][0] === leg[i][0] && todo[j][1] === leg[i][1]) todo.splice(j, 1);
              }
            }
            head = path[path.length - 1];
          }
          var out = route(head, [cols - 1, 6], cols, body);   /* 最短绕到右下角那一格就停住，不再往外走 */
          if (out) out.forEach(function (c) { path.push(c); });
          return { path: path, cols: cols, dots: lit.length };
        }

        function route(from, to, cols, body) {         /* BFS 最短路由：把身体占着的那几格当墙，
                                                          所以既不会原路掉头，也不会穿过自己的身体 */
          var blk = {}, k0 = from[0] + ',' + from[1], goal = to[0] + ',' + to[1];
          body.forEach(function (c) { blk[c[0] + ',' + c[1]] = 1; });
          var dirs = [[1, 0], [-1, 0], [0, 1], [0, -1]];
          if (body.length) {                           /* 少拐弯：当前朝向排在最前面 */
            var hd = [from[0] - body[0][0], from[1] - body[0][1]];
            dirs.sort(function (a, b) {
              return (b[0] === hd[0] && b[1] === hd[1] ? 1 : 0) - (a[0] === hd[0] && a[1] === hd[1] ? 1 : 0);
            });
          }
          var q = [from], prev = {}, seen = {}, h, i;
          seen[k0] = 1;
          for (h = 0; h < q.length; h++) {
            var k = q[h][0] + ',' + q[h][1];
            if (k === goal) break;
            for (i = 0; i < 4; i++) {
              var nc = q[h][0] + dirs[i][0], nr = q[h][1] + dirs[i][1], nk = nc + ',' + nr;
              if (nc < 0 || nc >= cols || nr < 0 || nr > 6) continue;
              if (seen[nk] || (blk[nk] && nk !== goal)) continue;
              seen[nk] = 1;
              prev[nk] = k;
              q.push([nc, nr]);
            }
          }
          if (!seen[goal]) return null;
          var cells = [], kk = goal;
          while (kk !== k0) { var p = kk.split(','); cells.push([+p[0], +p[1]]); kk = prev[kk]; }
          return cells.reverse();
        }

        function dirOf(a, b) {                         /* 蛇头朝向，用来摆眼睛 */
          if (b[0] > a[0]) return 'right';
          if (b[0] < a[0]) return 'left';
          if (b[1] > a[1]) return 'down';
          return 'up';
        }

        function makeBody(pos0, m) {                   /* 一轮开始时把蛇头、蛇身建好；之后只改位置，靠 transition 平滑滑过去 */
          segs = [];
          var i, seg;
          for (i = 0; i < LEN - 1; i++) {
            seg = document.createElement('div');
            seg.className = 'snake-seg';
            place(seg, pos0, null, null, m, 0, m.gap);
            seg.style.opacity = '0';                   /* 还没长出来的几段先贴着蛇头藏着 */
            layer.appendChild(seg);
            segs.push(seg);
          }
          head = document.createElement('div');
          head.className = 'snake-head';
          head.setAttribute('data-dir', dir);
          head.innerHTML = '<i></i><i></i>';
          place(head, pos0, null, null, m, HEAD_GROW, m.gap);
          layer.appendChild(head);
        }

        function paint() {                             /* 按当前 body 把蛇画出来（蛇头 + 一段段蛇身） */
          var m = met(), i, seg, tail;
          for (i = 1; i < body.length; i++) {          /* 蛇身：一段顶一段，整条连着（粗细一致，只靠透明度往尾巴淡） */
            seg = segs[i - 1];
            if (!seg) continue;
            tail = (i === body.length - 1);            /* 尾巴后面没有东西了，就朝前一格接上 */
            place(seg, body[i], tail ? body[i - 1] : body[i + 1], body[i - 1], m, 0, m.gap);
            seg.style.opacity = String(Math.max(0.16, 0.92 - i * 0.12));
          }
          for (; i <= LEN - 1; i++) {                  /* 多出来的段贴着蛇头藏好，等蛇长到那么长再露头 */
            seg = segs[i - 1];
            if (!seg) continue;
            place(seg, body[0] || [0, 0], null, null, m, 0, m.gap);
            seg.style.opacity = '0';
          }
          if (!head) return;
          if (!body.length) { head.style.opacity = '0'; return; }   /* 全收完了，蛇头也一起消失 */
          head.setAttribute('data-dir', dir);
          place(head, body[0], body[1], null, m, HEAD_GROW, m.gap);
        }

        function draw(pos) {
          body.unshift(pos);
          if (body.length > LEN) body.pop();
          paint();
        }

        function shrink() {                            /* 吃完停在边界上：尾巴一格一格收掉，收到蛇头也没有了 */
          if (!body.length) return false;
          body.pop();
          paint();
          return body.length > 0;
        }

        function eat(col, row) {
          var el = grid.querySelectorAll('i[data-l]')[col * 7 + row];
          if (el && +el.getAttribute('data-l') > 0) {   /* 吃掉：去掉颜色、去掉悬浮框、也不能再点 */
            el.setAttribute('data-l', '0');
            el.classList.add('is-eaten');
            el.removeAttribute('data-tip');
            el.removeAttribute('data-day');
            el.removeAttribute('role');
            el.removeAttribute('tabindex');
            return true;
          }
          return false;
        }

        function restore() {                           /* 重新生成小方格，重置贪吃蛇 */
          grid.innerHTML = snap;
          grid.classList.remove('is-playing');
          layer = null;
          head = null;
          segs = [];
          body = [];
          running = false;
          if (label) label.textContent = '玩一下贪吃蛇';
        }

        function stop() {                              /* 手动停下、或没有可吃的格子 */
          if (timer) { clearInterval(timer); timer = null; }
          restore();
        }

        function burst(from) {                         /* 蛇头在边界裂开：每个亮点化成小方块，飞快飞回自己原来的位置 */
          if (!snap || !from) return 0;
          var m = met(), box = document.createElement('div'), dots = [], i, lv;
          box.innerHTML = snap;                        /* 从开跑前的快照里数出原本有哪些亮点、在第几格 */
          var all = box.querySelectorAll('i[data-l]');
          for (i = 0; i < all.length; i++) {
            lv = +all[i].getAttribute('data-l');
            if (lv > 0) dots.push([Math.floor(i / 7), i % 7, lv]);
          }
          if (!dots.length) return 0;
          var open = document.createElement('div');
          open.className = 'burst-layer';
          grid.appendChild(open);
          var x0 = from[0] * m.stepX + m.cell / 2 - 1, y0 = from[1] * m.stepY + m.cell / 2 - 1, nodes = [];
          dots.forEach(function (c) {
            var el = document.createElement('div');
            el.className = 'burst-dot';
            el.setAttribute('data-l', String(c[2]));
            el.style.left = x0 + 'px';
            el.style.top = y0 + 'px';
            el.style.width = '2px';
            el.style.height = '2px';
            open.appendChild(el);
            nodes.push(el);
          });
          void open.offsetWidth;                       /* 先把「起手」这一帧坐实，后面才动得起来 */
          nodes.forEach(function (el, k) {
            var c = dots[k];
            el.style.transitionDelay = Math.round(k * 14) + 'ms';
            el.style.opacity = '1';
            el.style.left = (c[0] * m.stepX) + 'px';
            el.style.top = (c[1] * m.stepY) + 'px';
            el.style.width = m.cell + 'px';
            el.style.height = m.cell + 'px';
          });
          return 360 + nodes.length * 14;
        }

        function cycle() {                             /* 跑一轮：吃到右下角 → 原地消失 → 小方块飞回原位 → 停 2 秒再来 */
          var info = buildPath();
          if (!info) { stop(); return; }
          if (!snap) snap = grid.innerHTML;            /* 只留一份原样快照，循环时复用 */
          if (timer) clearInterval(timer);
          grid.classList.add('is-playing');
          layer = document.createElement('div');
          layer.className = 'snake-layer';
          layer.dataset.steps = info.path.length;      /* 这一轮要走多少步（自检脚本拿它估算一轮时长） */
          grid.appendChild(layer);
          var i = 0, eaten = 0;
          var delay = Math.max(STEP_MIN, Math.min(STEP_MAX, Math.round(TARGET / info.path.length)));
          layer.style.setProperty('--snk', delay + 'ms');   /* 每步滑多久：跟定时器对齐，看起来就是一直在爬 */
          body = [info.path[0]];
          makeBody(info.path[0], met());
          timer = setInterval(function () {
            if (!running) return;
            if (i >= info.path.length) {               /* 已经贴到右下角了：不再往外走，让蛇在原地一格一格消掉 */
              if (shrink()) return;
              clearInterval(timer);
              timer = null;
              if (layer) {
                layer.style.transition = 'opacity 0.3s linear';
                layer.style.opacity = '0';
              }
              /* 蛇头刚消失：把身子「裂开」成一个个小方块，飞快飞回各自原来的位置 */
              var grow = burst(info.path[info.path.length - 1]);
              setTimeout(function () {
                if (!running) return;
                grid.innerHTML = snap;                 /* 小方块都落位了，再把小方格重新生成、贪吃蛇重置 */
                grid.classList.remove('is-playing');
                layer = null;
                head = null;
                segs = [];
                body = [];
                stop();                                /* 小方块都飞回原位了，这一轮到此为止（想再看就再点一下） */
              }, PAUSE + grow);
              return;
            }
            var p = info.path[i++];
            if (body[0]) dir = dirOf(body[0], p);
            if (p[0] < info.cols && eat(p[0], p[1])) eaten++;
            draw(p);
          }, delay);
        }

        btn.addEventListener('click', function () {
          if (running) { stop(); return; }             /* 再点一下停下并把小方格复原 */
          running = true;
          if (!snap) snap = grid.innerHTML;
          if (label) label.textContent = '停止贪吃蛇';
          btn.setAttribute('data-tip', '再点一下停下（停下会把小方格复原）');
          cycle();
        });
      })();

      /* ---------- 工作日志：打开时先去 Worker 拉一次（有更新的整页就换掉；否则从 KV 取新提交） ---------- */
      (function () {
        var api = '__WL_API__', mine = '__WL_BUILD__', KIND_CN = __KIND_CN__, REPO = '__WL_REPO__';
        if (!api) return;
        var base = api.replace(/[/]+$/, '');
        var host = document.getElementById('wlFresh');

        function stampOf(html) {                       /* 从整页里取出构建时间 */
          var m = html.match(/id="wlFresh" data-build="([^"]+)"/);
          return m ? m[1] : '';
        }
        function note(text) {
          if (!host) return;
          host.textContent = text;
          host.hidden = false;
        }
        function esc(s) {
          return String(s === null || s === undefined ? '' : s)
            .replace(/[&<>"]/g, function (c) {
              return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
            });
        }
        function swap(html) {                          /* 整页换新：document.write 会重跑脚本，事件自然重挂 */
          try {
            var doc = new DOMParser().parseFromString(html, 'text/html');
            if (!doc.querySelector('main.content') || !doc.querySelector('.heat')) return false;
            document.open();
            document.write(html);
            document.close();
            return true;
          } catch (e) {
            return false;
          }
        }

        function showDelta(items, total) {             /* KV 里比本页新的提交：列出来（类型胶囊复用页面样式） */
          if (!host || !items.length) return false;
          var rows = items.map(function (x) {
            return '<li><span class="wl-new-day">' + esc(x.day) + '</span>'
              + '<span class="tag"><i class="kd kd-' + esc(x.kind) + '"></i>'
              + esc(KIND_CN[x.kind] || x.kind) + '</span>'
              + '<span class="wl-new-sub" title="' + esc(x.subject) + '">' + esc(x.subject) + '</span>'
              + '<a href="' + esc(x.url) + '" target="_blank" rel="noopener">' + esc(x.short) + ' ↗</a></li>';
          }).join('');
          var byKind = {};
          items.forEach(function (x) {
            var k = KIND_CN[x.kind] || x.kind;
            byKind[k] = (byKind[k] || 0) + 1;
          });
          var stat = Object.keys(byKind).map(function (k) { return k + ' ' + byKind[k]; }).join(' · ');
          host.classList.add('is-list');
          host.innerHTML = '<div>这页之后又有 ' + items.length + ' 条新提交（' + esc(stat) + '；Worker 那边合计 '
            + esc(String(total === undefined || total === null ? '' : total)) + ' 条）：</div>'
            + '<ul class="wl-fresh-list">' + rows + '</ul>'
            + (REPO ? '<div class="wl-fresh-more">提交节奏、分支拓扑、分支明细与时间线由云端每天重建一次'
              + '（<a href="https://github.com/' + esc(REPO) + '/actions/workflows/worklog.yml"'
              + ' target="_blank" rel="noopener">现在就去重建整页</a>）</div>' : '');
          host.hidden = false;
          return true;
        }

        function loadDelta() {                         /* 只取比自己构建时间新的那几条 */
          fetch(base + '/timeline.json?since=' + encodeURIComponent(mine), { cache: 'no-store' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.items && d.items.length) showDelta(d.items, d.mine); })
            .catch(function () { /* 拉不到就什么都不做，页面照旧 */ });
        }

        fetch(base + '/stage.json', { cache: 'no-store' })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (st) {
            if (!st || st.empty || !st.generatedAt || st.generatedAt <= mine) return null;
            return fetch(base + '/page.html', { cache: 'no-store' })
              .then(function (r) { return r.ok ? r.text() : null; })
              .then(function (html) {
                if (html) {
                  var pageAt = stampOf(html);
                  if (pageAt && pageAt > mine && swap(html)) return true;   /* 拿到比本地更新的整页 */
                }
                return false;
              });
          })
          .catch(function () { /* 拉不到就当纯静态页看，什么都不用提示 */ });

        loadDelta();                                   /* 不管摘要有没有新数据，都去 KV 问一次增量 */
      })();
"""

MAIN_TMPL = """
    <!-- 1. 首屏 -->
    <section class="hero">
      <h1 class="hero-title">工作日志</h1>
      <p class="section-desc">fork 只是起点，重新定义才是目的。这里是我在 __REPOS__ 两个仓库<strong>所有分支</strong>里由自己提交的全部改动，共 <strong>__MINE__ 条</strong>，数据由 git 历史自动生成。</p>
      <div class="btn-row">
        <a class="btn btn-primary" href="https://github.com/DelicateDuck582" target="_blank" rel="noopener">__I_GIT__<span>我的 GitHub</span></a>
        <a class="btn btn-outline" href="#timeline"><span>看提交记录</span>__I_ARROW__</a>
        <a class="btn btn-outline" href="index.html">__I_BACK__<span>返回首页</span></a>
      </div>
      <p class="hero-note">说明：这里的数字只算<strong>我署名</strong>的提交（__REPOS__ 两个仓库、所有分支），别人提交的、从上游带过来的、以及被时间线筛选挡住的都不显示，所以 GitHub 上能看到的提交会比这里多一些。</p>
    </section>

    <!-- 2. 总览 -->
    <section class="section" id="overview">
      <h2 class="section-title">总览</h2>
      <p class="section-desc">__SPAN__ 这段时间里，有 __DAYS__ 天在提交，平均每次改动动到 __AVG__ 个文件。</p>
      <div class="stat-grid">__STATS__</div>
      <div class="split">
        <div class="split-bar">__SPLIT__</div>
        <div class="split-legend">__SPLIT_LEGEND__</div>
      </div>
    </section>

    <!-- 3. 分支拓扑 -->
    <section class="section" id="graph">
      <h2 class="section-title">分支拓扑</h2>
      <p class="section-desc">每个仓库一张图：横轴时间、纵轴分支泳道。曲线表示「这条分支从哪条分支的哪个提交分出来」，泳道上的圆点是我当天的提交（越大说明当天越多），空心圆是分支 HEAD。蓝色 = 上游作者的部分（主线里我接手之前的那一截、从上游提交分出去的曲线），橙色 = 我的。</p>
      __GRAPH_CARDS__
    </section>

    <!-- 4. 提交节奏 -->
    <section class="section" id="activity">
      <h2 class="section-title">提交节奏</h2>
      <p class="section-desc">同样的改动习惯，时间久了会有形状。</p>
      <div class="chart-card">
        <div class="chart-head"><span class="chart-title">每月提交量</span><span class="chart-sub">合计 __MINE__ 次</span></div>
        __BARS__
      </div>
      <div class="chart-card">
        <div class="chart-head"><span class="chart-title">活跃热力图</span><span class="chart-sub">列 = 一周（周一 → 周日），颜色越深当天提交越多；点小方格可跳到下方时间线的当天__SNAKE_BTN__</span></div>
        __HEAT__
      </div>
      <div class="chart-card">
        <div class="chart-head"><span class="chart-title">提交类型</span><span class="chart-sub">__KIND_SUB__</span></div>
        __KINDS__
      </div>
      <div class="chart-card">
        <div class="chart-head"><span class="chart-title">涉及文件类型</span><span class="chart-sub">按被改动文件的后缀统计（同一文件多次改动会重复计数）</span></div>
        __EXTS__
      </div>
    </section>

    <!-- 5. 分支明细 -->
    <section class="section" id="branches">
      <h2 class="section-title">分支明细</h2>
      <p class="section-desc">每个分支上我提交了多少、从哪个点分出来、以及分支最后一次提交的哈希。</p>
      __TABLES__
    </section>

    <!-- 6. 提交时间线 -->
    <section class="section" id="timeline">
      <h2 class="section-title">提交时间线</h2>
      <p class="section-desc">按时间倒序排列，共 __TIMELINE_TOTAL__ 条记录，可按仓库或类型筛选。</p>
      __FILTERS__
      <div class="tl">__TIMELINE__</div>
    </section>
"""


def build_main():
    avg_files = round(file_touches / total_mine, 1) if total_mine else 0
    out = MAIN_TMPL
    arrow = ('<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">'
             '<path d="M4.5 12h14"/><path d="m13 6.5 5.5 5.5L13 17.5"/></svg>')
    for token, value in (
        ("__REPOS__", esc(REPO_SUB)),
        ("__MINE__", str(total_mine)),
        ("__SPAN__", "%s → %s" % (span_first, span_last)),
        ("__DAYS__", str(len(all_days))),
        ("__AVG__", str(avg_files)),
        ("__STATS__", stats_html),
        ("__SPLIT__", split_html),
        ("__SPLIT_LEGEND__", split_legend),
        ("__GRAPH_CARDS__", graph_cards),
        ("__BARS__", html_month_bars(repos)),
        ("__HEAT__", html_heatmap(repos)),
        ("__KINDS__", html_kind_bars(repos)),
        ("__KIND_SUB__", KIND_SUB),
        ("__EXTS__", html_ext_bars(repos)),
        ("__TABLES__", branch_tables),
        ("__FILTERS__", html_filters(repos)),
        ("__TIMELINE_TOTAL__", str(timeline_total)),
        ("__TIMELINE__", timeline_html),
        ("__I_GIT__", icon("git")),
        ("__I_ARROW__", arrow),
        ("__I_BACK__", icon("back")),
        ("__SNAKE_BTN__", snake_button(repos)),
        ("__WL_API__", API_BASE),
        ("__WL_BUILD__", BUILD_AT),
    ):
        out = out.replace(token, value)
    return ('<div class="wl-fresh" id="wlFresh" data-build="%s" hidden></div>%s'
            % (esc(BUILD_AT), out))


main_html = build_main()

tpl = open(INDEX, encoding="utf-8").read()
tpl = tpl.replace("<title>首页 | DelicateDuck582</title>", "<title>工作日志 | DelicateDuck582</title>")
tpl = tpl.replace('<meta name="description" content="DelicateDuck582 的个人主页">',
                  '<meta name="description" content="DelicateDuck582 的工作日志：cloud-mail 与 SPlayer 全部分支的提交记录">')
tpl = tpl.replace("</style>", CSS_A + CSS_B + CSS_C + font_css() + "\n</style>", 1)
tpl = tpl.replace('<a href="#home">首页</a>', '<a href="index.html">首页</a>')
tpl = tpl.replace('<span class="current">DelicateDuck582</span>', '<span class="current">工作日志</span>')
tpl = tpl.replace('href="#about" data-nav="about"', 'href="index.html#about"')
tpl = tpl.replace('href="#projects" data-nav="projects"', 'href="index.html#projects"')
tpl = tpl.replace('href="#social" data-nav="social"', 'href="index.html#social"')
tpl = tpl.replace('<a class="nav-link" href="worklog.html">',
                  '<a class="nav-link is-active" href="worklog.html" aria-current="page">')

m = re.search(r'<main class="content" id="home">.*?</main>', tpl, re.S)
if not m:
    raise SystemExit("模板中找不到 <main> 区块")
tpl = tpl[:m.start()] + '<main class="content" id="home">' + main_html + "  </main>" + tpl[m.end():]
tpl = tpl.replace("</script>", FILTER_JS.replace("__WL_API__", API_BASE).replace("__WL_BUILD__", BUILD_AT)
                  .replace("__KIND_CN__", json.dumps(KIND_LABEL, ensure_ascii=False))
                  .replace("__WL_REPO__", REPO_SLUG)
                  + "  </script>", 1)

with open(OUT, "w", encoding="utf-8") as f:
    f.write(tpl)

print("写出 %s  %d bytes" % (OUT, os.path.getsize(OUT)))
print("提交 %d 条 ｜ 时间线 %d ｜ 分支 %d 个 ｜ 文件改动 %d 次 ｜ 跨度 %s ~ %s（%d 天）"
      % (total_mine, timeline_total, total_branches, file_touches, span_first, span_last, span_days))
if KIND_FIXED:
    moved = collections.Counter("%s → %s" % (c["kind_rule"], c["kind"])
                               for r in repos for c in r["commits"] if c.get("kind_rule"))
    now = collections.Counter(c["kind"] for r in repos for c in r["commits"])
    print("AI 类型判定：改了 %d / %d 条的归类（worklog-kinds.json）" % (KIND_FIXED, total_mine))
    print("          改法：%s" % "；".join("%s %d 条" % kv for kv in moved.most_common(6)))
    print("          现在分布：%s" % "，".join("%s %d" % (KIND_LABEL[k], n) for k, n in now.most_common()))
else:
    print("AI 类型判定：无（没有 worklog-kinds.json），类型仍按前缀 + 关键词那套规则自动归类")

