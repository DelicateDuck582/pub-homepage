#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建 site/companies/*.html（会社详情页）为 DESIGN_SPEC.md v1（极简科技黑）模板。

四步：
  1. 提取：从每个现有页面读取 COMPANY_NAME / <title> / <meta name="description"> /
     GAMES 数组（逐对象逐字段，容忍空串与转义）/ I18N 对象字面量 / footer 内联 HTML /
     notice 提示块（仅个别页面）。
  2. 生成：用新模板在内存里重新生成页面（单文件自包含，深色 token，行卡片，
     ghost 官方按钮 + primary 下载按钮 + 禁用态，等宽计数，无 emoji 图标，
     无 data-theme / themeToggle）。
  3. 校验：对内存中的生成结果重新提取，与第 1 步原始提取逐字段、逐顺序全等比较；
     每个文件恰好含一次 'vndb_lang'，且不含 'themeToggle' / 'data-theme'；
     含 '../web.html' 返回链接与 id="gameList"；title 与 meta description 与原文件一致；
     class 含 notice 的 div 数量与源文件一致。
  4. 落盘：仅当全部文件校验通过后才写盘，且逐个「写 .tmp + os.replace」原子替换，
     落盘后读回比对；任一环节失败以非零退出码结束，且不留半成品、不改动任何原文件。
  5. 报告：逐文件 PASS/FAIL + 汇总。

数据（name / alias / link / official）逐字节保留：值的写入使用 json.dumps 规范化，
并在校验阶段同时比较「字段值」与「原始字面量」。

用法：python build_companies.py
"""

import json
import os
import re
import sys
from pathlib import Path

COMPANY_DIR = Path(__file__).resolve().parent / "site" / "companies"

FIELDS = ("name", "alias", "link", "official")

ENTRY_RE = re.compile(
    r'\{\s*name:\s*"((?:[^"\\]|\\.)*)"\s*,'
    r'\s*alias:\s*"((?:[^"\\]|\\.)*)"\s*,'
    r'\s*link:\s*"((?:[^"\\]|\\.)*)"\s*,'
    r'\s*official:\s*"((?:[^"\\]|\\.)*)"\s*\}'
)
GAMES_RE = re.compile(r"const GAMES = \[(.*?)\n\];", re.S)
COMPANY_RE = re.compile(r'const COMPANY_NAME = "((?:[^"\\]|\\.)*)"')
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
DESC_RE = re.compile(r'<meta name="description" content="(.*?)">', re.S)
I18N_RE = re.compile(r"const I18N = \{.*?\n\};", re.S)
FOOTER_RE = re.compile(r"<footer>(.*?)</footer>", re.S)
DIV_TAG_RE = re.compile(r"<div\b([^>]*)>", re.I)
CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')
I18N_ATTR_RE = re.compile(r'data-i18n-html="([^"]*)"')
DIV_BOUNDARY_RE = re.compile(r"<div\b|</div>", re.I)


def _div_inner(source, start):
    """从 start 起按 <div>/</div> 配对，返回该 div 的内容；未闭合返回 None。"""
    depth = 1
    pos = start
    while True:
        m = DIV_BOUNDARY_RE.search(source, pos)
        if not m:
            return None
        if m.group(0).lower() == "</div>":
            depth -= 1
            if depth == 0:
                return source[start:m.start()]
        else:
            depth += 1
        pos = m.end()


def notice_divs(source):
    """独立抓取 class 含 notice 的 div（不依赖属性顺序），返回 [(key, inner), ...]。"""
    found = []
    for m in DIV_TAG_RE.finditer(source):
        cm = CLASS_ATTR_RE.search(m.group(1))
        if not cm or "notice" not in cm.group(1).split():
            continue
        inner = _div_inner(source, m.end())
        if inner is None:
            continue
        km = I18N_ATTR_RE.search(m.group(1))
        found.append((km.group(1) if km else "", inner))
    return found


# ---------------------------------------------------------------- 工具

def decode_literal(raw):
    """把 JS 字符串字面量内容按 JSON 规则解码为值（本数据集不含 JS 专有转义）。"""
    return json.loads('"' + raw + '"')


def encode_literal(value):
    """把值写回字符串字面量内容，保证可被 JS/JSON 正确解析。"""
    return json.dumps(value, ensure_ascii=False)[1:-1]


def html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------- 第 1 步：提取

def extract(source, path):
    """从页面源码提取全部需要保留的内容；任何缺失都直接抛错（不写文件）。"""
    m = COMPANY_RE.search(source)
    if not m:
        raise ValueError("%s: 未找到 COMPANY_NAME" % path)
    company_raw = m.group(1)

    m = TITLE_RE.search(source)
    if not m:
        raise ValueError("%s: 未找到 <title>" % path)
    title = m.group(1)

    m = DESC_RE.search(source)
    if not m:
        raise ValueError("%s: 未找到 <meta name=description>" % path)
    desc = m.group(1)

    m = GAMES_RE.search(source)
    if not m:
        raise ValueError("%s: 未找到 GAMES 数组" % path)
    block = m.group(1)

    games = []
    matched_spans = []
    for em in ENTRY_RE.finditer(block):
        raw = dict(zip(FIELDS, em.groups()))
        games.append({
            "raw": raw,
            "value": {f: decode_literal(raw[f]) for f in FIELDS},
        })
        matched_spans.append(em.span())
    leftover = block
    for start, end in reversed(matched_spans):
        leftover = leftover[:start] + leftover[end:]
    if re.sub(r"[\s,]", "", leftover):
        raise ValueError("%s: GAMES 数组存在无法解析的残留：%r" % (path, leftover[:80]))
    if len(re.findall(r"\blink:", block)) != len(games):
        raise ValueError("%s: GAMES 数组对象数与 link 出现次数不一致" % path)
    if not games:
        raise ValueError("%s: GAMES 数组为空" % path)

    m = I18N_RE.search(source)
    if not m:
        raise ValueError("%s: 未找到 I18N 对象字面量" % path)
    i18n_block = m.group(0)

    m = FOOTER_RE.search(source)
    if not m:
        raise ValueError("%s: 未找到 <footer>" % path)
    footer_inner = m.group(1)

    notices = notice_divs(source)
    notice = None
    if notices:
        notice = {"key": notices[0][0], "inner": notices[0][1]}

    return {
        "path": path,
        "company_raw": company_raw,
        "company": decode_literal(company_raw),
        "title": title,
        "desc": desc,
        "games": games,
        "i18n_block": i18n_block,
        "footer_inner": footer_inner,
        "notice": notice,
        "notice_count": len(notices),
    }


# ---------------------------------------------------------------- 第 2 步：模板

CSS = """  :root {
    color-scheme: dark;
    --bg: #0a0a0b;
    --card: #111113;
    --card-hover: #17171a;
    --fg: #ededed;
    --muted: #8e8e96;
    --border: rgba(237, 237, 237, 0.09);
    --border-strong: rgba(237, 237, 237, 0.22);
    --accent: #38bdf8;
    --accent-soft: rgba(56, 189, 248, 0.13);
    --ease: cubic-bezier(0.16, 1, 0.3, 1);
    --font: -apple-system, "Segoe UI Variable Text", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    --mono: ui-monospace, "Cascadia Mono", "SF Mono", Consolas, monospace;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  ::selection { background: color-mix(in srgb, var(--accent) 32%, transparent); }
  :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  body {
    font-family: var(--font);
    color: var(--fg);
    background: var(--bg);
    font-size: 15px;
    line-height: 1.6;
    min-height: 100vh;
    -webkit-font-smoothing: antialiased;
  }
  a { color: inherit; text-decoration: none; }

  /* ========== 顶栏 ========== */
  header {
    position: sticky;
    top: 0;
    z-index: 50;
    display: flex;
    align-items: center;
    gap: 14px;
    min-height: 56px;
    padding: 8px 6vw;
    background: rgba(10, 10, 11, 0.82);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
  }
  .back {
    flex-shrink: 0;
    padding: 6px 12px;
    border-radius: 10px;
    font-size: 13px;
    font-weight: 600;
    color: var(--fg);
    box-shadow: inset 0 0 0 1px var(--border-strong);
    transition: color 0.25s var(--ease), box-shadow 0.25s var(--ease);
  }
  .back:hover { color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .site-name {
    min-width: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--fg);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .header-right { margin-left: auto; flex-shrink: 0; }
  .lang-btn {
    padding: 5px 12px;
    border: 0;
    border-radius: 10px;
    background: transparent;
    color: var(--fg);
    font-family: inherit;
    font-size: 13px;
    font-weight: 500;
    box-shadow: inset 0 0 0 1px var(--border-strong);
    cursor: pointer;
    transition: color 0.25s var(--ease), box-shadow 0.25s var(--ease);
  }
  .lang-btn:hover { color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }

  /* ========== 内容列 ========== */
  .wrap { max-width: 760px; margin: 0 auto; padding: 40px 20px 80px; }

  .list-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;
  }
  .list-head h2 { font-size: 18px; font-weight: 700; color: var(--fg); }
  .list-head h2 span { font-weight: 600; color: var(--muted); }
  .list-head .count {
    flex-shrink: 0;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--muted);
    white-space: nowrap;
  }

  /* ========== 游戏行 ========== */
  .game-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 15px 18px;
    margin-bottom: 10px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--card);
    transition: border-color 0.25s var(--ease), background 0.25s var(--ease);
  }
  .game-item:hover {
    border-color: color-mix(in srgb, var(--accent) 40%, transparent);
    background: var(--card-hover);
  }
  .game-main { min-width: 0; }
  .game-name {
    font-size: 15px;
    font-weight: 600;
    color: var(--fg);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .game-alias {
    margin-top: 2px;
    font-size: 13px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .game-meta {
    margin-top: 4px;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--muted);
  }

  /* ========== 按钮组 ========== */
  .btn-group {
    display: flex;
    flex-shrink: 0;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
    gap: 8px;
  }
  .official-btn {
    padding: 8px 14px;
    border-radius: 10px;
    font-size: 13px;
    font-weight: 600;
    color: var(--fg);
    background: transparent;
    box-shadow: inset 0 0 0 1px var(--border-strong);
    white-space: nowrap;
    transition: color 0.25s var(--ease), box-shadow 0.25s var(--ease);
  }
  .official-btn:hover { color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .download-btn {
    padding: 8px 18px;
    border-radius: 10px;
    font-size: 13px;
    font-weight: 600;
    color: var(--bg);
    background: var(--fg);
    white-space: nowrap;
    transition: filter 0.25s var(--ease);
  }
  .download-btn:hover { filter: brightness(1.06); }
  .download-btn.disabled {
    background: transparent;
    box-shadow: inset 0 0 0 1px var(--border);
    color: var(--muted);
    cursor: not-allowed;
    pointer-events: none;
  }
  .download-btn.disabled:hover { filter: none; }

  /* ========== 提示区（正文，可保留少量 emoji） ========== */
  .notice {
    margin-bottom: 16px;
    padding: 14px 18px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--card);
    font-size: 13px;
    line-height: 1.7;
    color: var(--muted);
  }
  .notice a { color: var(--accent) !important; font-weight: 600; }

  .copyright-link { color: inherit; text-decoration: none; }

  footer {
    margin-top: 40px;
    padding-top: 24px;
    border-top: 1px solid var(--border);
    text-align: center;
    font-size: 13px;
    line-height: 1.7;
    color: var(--muted);
  }

  @media (max-width: 640px) {
    .game-item { flex-direction: column; align-items: stretch; }
    .btn-group { justify-content: flex-end; }
  }
"""

SCRIPT = """// ============================================================
// 数据区：该会社在 Steam 上发行的游戏（补丁下载链接）
// ============================================================
const COMPANY_NAME = "@@COMPANY@@";
const GAMES = [
@@GAMES@@
];
// ============================================================

// ============ 国际化 ============
const LANG_KEY = 'vndb_lang';
let lang = 'zh';
try {
  const savedLang = localStorage.getItem(LANG_KEY);
  if (savedLang) lang = savedLang;
} catch (e) {}

@@I18N@@

function t(key) {
  const m = I18N[key];
  return m ? (m[lang] || m['zh']) : key;
}

function setLang() {
  document.documentElement.lang = lang === 'en' ? 'en' : 'zh-CN';
  document.querySelectorAll('[data-i18n]').forEach(function (el) {
    el.textContent = t(el.getAttribute('data-i18n'));
  });
  document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
    el.innerHTML = t(el.getAttribute('data-i18n-html'));
  });
  const btn = document.getElementById('langToggle');
  if (btn) btn.textContent = lang === 'en' ? '中文' : 'EN';
  renderGames();
}

const listEl = document.getElementById('gameList');
const countEl = document.getElementById('listCount');

function renderGames() {
  listEl.innerHTML = '';
  countEl.textContent = lang === 'en' ? GAMES.length + ' games' : '共 ' + GAMES.length + ' 款';
  GAMES.forEach(function (game) {
    const item = document.createElement('div');
    item.className = 'game-item';
    // 注意：name / alias 是数据里有意的内联 HTML（如 favorite 的 <br>），不做 escapeHtml
    let aliasHtml = '';
    if (game.alias) {
      aliasHtml = '<div class="game-alias">' + game.alias + '</div>';
    }
    let btnHtml = '';
    if (game.official) {
      btnHtml += '<a class="official-btn" href="' + game.official + '" target="_blank" rel="noopener">' + t('officialPage') + '</a>';
    }
    const disabled = game.link === 'https://example.com' ? ' disabled' : '';
    btnHtml += '<a class="download-btn' + disabled + '" href="' + game.link + '" target="_blank" rel="noopener"' + (disabled ? ' aria-disabled="true" tabindex="-1"' : '') + '>' + t('download') + '</a>';
    item.innerHTML =
      '<div class="game-main">' +
        '<div class="game-name">' + game.name + '</div>' +
        aliasHtml +
        '<div class="game-meta">' + COMPANY_NAME + ' · Steam</div>' +
      '</div>' +
      '<div class="btn-group">' + btnHtml + '</div>';
    listEl.appendChild(item);
  });
}

// ========== 语言切换 ==========
(function initLang() {
  const btn = document.getElementById('langToggle');
  if (btn) btn.addEventListener('click', function () {
    lang = lang === 'zh' ? 'en' : 'zh';
    try { localStorage.setItem(LANG_KEY, lang); } catch (e) {}
    setLang();
  });
  setLang();
})();
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="@@DESC@@">
<title>@@TITLE@@</title>
<style>
@@CSS@@</style>
</head>
<body>

<header>
  <a class="back" href="../web.html" data-i18n="back">← 返回会社列表</a>
  <span class="site-name">GALGAME 补丁搜索</span>
  <div class="header-right">
    <button type="button" id="langToggle" class="lang-btn" title="切换语言 / Change Language">EN</button>
  </div>
</header>

<div class="wrap">
  <div class="list-head">
    <h2>@@COMPANY_TEXT@@<span data-i18n="ofGames">的游戏</span></h2>
    <span class="count" id="listCount"></span>
  </div>
  <div id="gameList"></div>@@NOTICE@@
</div>

<footer>@@FOOTER@@</footer>

<script>
@@SCRIPT@@</script>
</body>
</html>
"""


def render(record):
    games_js = ",\n".join(
        '  { name: "%s", alias: "%s", link: "%s", official: "%s" }'
        % tuple(encode_literal(g["value"][f]) for f in FIELDS)
        for g in record["games"]
    )
    notice = record["notice"]
    notice_html = ""
    if notice:
        notice_html = '\n  <div class="notice" data-i18n-html="%s">%s</div>' % (
            notice["key"],
            notice["inner"],
        )
    page = PAGE_TEMPLATE
    page = page.replace("@@CSS@@", CSS)
    page = page.replace("@@SCRIPT@@", SCRIPT)
    page = page.replace("@@I18N@@", record["i18n_block"])
    page = page.replace("@@GAMES@@", games_js)
    page = page.replace("@@COMPANY@@", encode_literal(record["company"]))
    page = page.replace("@@COMPANY_TEXT@@", html_escape(record["company"]))
    page = page.replace("@@TITLE@@", record["title"])
    page = page.replace("@@DESC@@", record["desc"])
    page = page.replace("@@FOOTER@@", record["footer_inner"])
    page = page.replace("@@NOTICE@@", notice_html)
    return page


# ---------------------------------------------------------------- 第 3 步：校验

def validate(record, source):
    """对生成后的文件源码做全部校验，返回 (是否通过, 失败原因列表)。"""
    problems = []

    try:
        got = extract(source, record["path"])
    except Exception as exc:  # 提取失败即校验失败
        return False, ["重新提取失败：%s" % exc]

    # a. 数据逐字段、逐顺序全等
    if got["company_raw"] != record["company_raw"] or got["company"] != record["company"]:
        problems.append("COMPANY_NAME 不一致：%r -> %r" % (record["company_raw"], got["company_raw"]))
    if len(got["games"]) != len(record["games"]):
        problems.append("GAMES 数量不一致：%d -> %d" % (len(record["games"]), len(got["games"])))
    for i, (was, now) in enumerate(zip(record["games"], got["games"])):
        for field in FIELDS:
            if was["value"][field] != now["value"][field]:
                problems.append(
                    "GAMES[%d].%s 值不一致：%r -> %r"
                    % (i, field, was["value"][field], now["value"][field])
                )
            elif was["raw"][field] != now["raw"][field]:
                problems.append(
                    "GAMES[%d].%s 字面量形式变化（值相同）：%r -> %r"
                    % (i, field, was["raw"][field], now["raw"][field])
                )

    # b. 语言键与主题键
    if source.count("vndb_lang") != 1:
        problems.append("'vndb_lang' 出现 %d 次（应为 1 次）" % source.count("vndb_lang"))
    if "themeToggle" in source:
        problems.append("仍包含 'themeToggle'")
    if "data-theme" in source:
        problems.append("仍包含 'data-theme'")

    # c. 返回链接与宿主节点
    if "../web.html" not in source:
        problems.append("缺少 ../web.html 返回链接")
    if 'id="gameList"' not in source:
        problems.append('缺少 id="gameList"')

    # d. title 与 description
    if got["title"] != record["title"]:
        problems.append("title 不一致：%r -> %r" % (record["title"], got["title"]))
    if got["desc"] != record["desc"]:
        problems.append("meta description 不一致：%r -> %r" % (record["desc"], got["desc"]))

    # 附加：I18N 词条与 footer / notice 原样
    if got["i18n_block"] != record["i18n_block"]:
        problems.append("I18N 词条字面量发生变化")
    if got["footer_inner"] != record["footer_inner"]:
        problems.append("footer 文案发生变化")
    if (got["notice"] is None) != (record["notice"] is None):
        problems.append("notice 提示块存在性发生变化")
    elif record["notice"] and (
        got["notice"]["key"] != record["notice"]["key"]
        or got["notice"]["inner"] != record["notice"]["inner"]
    ):
        problems.append("notice 提示块内容发生变化")
    # 不变量：产物中 class 含 notice 的 div 数量必须与源文件一致
    if got["notice_count"] != record["notice_count"]:
        problems.append(
            "notice 数量不一致：源 %d 个 -> 产物 %d 个"
            % (record["notice_count"], got["notice_count"])
        )

    # 附加：内联数据不得破坏 <script>
    if "</script" in "".join(g["raw"][f] for g in got["games"] for f in FIELDS):
        problems.append("数据中含 </script")

    return (not problems), problems


# ---------------------------------------------------------------- 第 4 步：主流程

def main():
    paths = sorted(COMPANY_DIR.glob("*.html"))
    if not paths:
        print("错误：%s 下没有 .html 文件" % COMPANY_DIR)
        return 2

    # 1. 先全部提取，任何失败都不写文件
    originals = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        try:
            originals.append((extract(source, path.name), source))
        except Exception as exc:
            print("提取失败：%s" % exc)
            return 2
    print("已提取 %d 个会社页，共 %d 款游戏\n" % (
        len(originals), sum(len(r["games"]) for r, _ in originals)))

    # 2. 先在内存里生成并完成全部校验（此阶段不落盘，任何失败原文件都不动）
    print("=== 生成后自校验（落盘前，仅内存） ===")
    plan = []
    failed = []
    for record, _ in originals:
        record["path_full"] = COMPANY_DIR / record["path"]
        page = render(record)
        ok, problems = validate(record, page)
        plan.append((record, page))
        status = "PASS" if ok else "FAIL"
        print("%-5s %-24s %2d games" % (status, record["path"], len(record["games"])))
        if not ok:
            failed.append((record["path"], problems))
            for problem in problems:
                print("        - %s" % problem)

    total = sum(len(r["games"]) for r, _ in originals)
    print("-" * 44)
    if failed:
        print("校验未通过：不写入任何文件（全部原文件保持不动）")
        print("文件数: %d    通过: %d    失败: %d" % (
            len(originals), len(originals) - len(failed), len(failed)))
        print("游戏总数: %d" % total)
        print("结果: %d/%d PASS" % (len(originals) - len(failed), len(originals)))
        return 1

    # 3. 全部通过后原子落盘：先写 .tmp 再 os.replace，并读回确认与校验内容一致
    written = 0
    for record, page in plan:
        path_full = record["path_full"]
        tmp = path_full.with_name(path_full.name + ".tmp")
        try:
            tmp.write_text(page, encoding="utf-8", newline="\n")
            os.replace(tmp, path_full)
        except Exception as exc:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            print("写入失败：%s（%s）" % (record["path"], exc))
            return 1
        if path_full.read_text(encoding="utf-8") != page:
            print("落盘内容与校验内容不一致：%s" % record["path"])
            return 1
        written += 1

    print("已原子写入 %d 个文件（.tmp + os.replace，读回一致）" % written)
    print("文件数: %d    通过: %d    失败: 0" % (len(originals), len(originals)))
    print("游戏总数: %d" % total)
    print("结果: %d/%d PASS" % (len(originals), len(originals)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
