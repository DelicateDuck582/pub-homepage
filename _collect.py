# -*- coding: utf-8 -*-
r"""采集 cloud-mail / SPlayer 两个仓库「所有分支」中 DelicateDuck582 的提交，输出 JSON。

依赖：脚本同目录下的 {cloud-mail,SPlayer}.git（--bare --filter=blob:none 克隆）。
纯本地只读操作。
"""
import collections
import json
import os
import re
import subprocess

WORK = os.path.dirname(os.path.abspath(__file__))   # 脚本所在目录：本地是 _worklog-dev，CI 里就是仓库目录
REPOS = [
    dict(key="cloud-mail", repo=os.path.join(WORK, "cloud-mail.git"), main="main",
         url="https://github.com/DelicateDuck582/cloud-mail"),
    dict(key="SPlayer", repo=os.path.join(WORK, "SPlayer.git"), main="dev",
         url="https://github.com/DelicateDuck582/SPlayer"),
]
ME_EMAILS = {"105136492+DelicateDuck582@users.noreply.github.com", "2708857263@qq.com"}
FS, RS = "\x1f", "\x1e"          # 字段分隔 / 记录分隔

# 写法别名 → 统一类型（约定式提交里常见的各种写法，都被收敛到同一批分类）
KIND_ALIAS = {
    "feat": "feat", "feature": "feat", "impl": "feat", "implement": "feat", "add": "feat",
    "fix": "fix", "bugfix": "fix", "hotfix": "fix", "patch": "fix",
    "refactor": "refactor", "refact": "refactor", "clean": "refactor", "cleanup": "refactor",
    "perf": "perf", "performance": "perf", "optimize": "perf", "optimise": "perf",
    "docs": "docs", "doc": "docs", "readme": "docs",
    "style": "style", "format": "style", "fmt": "style", "lint": "style",
    "test": "test", "tests": "test", "spec": "test",
    "chore": "chore", "deps": "chore", "dep": "chore", "release": "chore", "bump": "chore",
    "build": "build", "ci": "ci", "cd": "ci", "revert": "revert", "merge": "merge",
    "wip": "other", "i18n": "feat", "a11y": "feat",
    "security": "fix", "sec": "fix", "hardening": "fix",
    "sync": "refactor", "port": "refactor", "move": "refactor",
    "diagnose": "chore", "debug": "chore", "log": "chore", "logs": "chore",
}

# 没有规范前缀时按措辞猜（顺序即优先级：先动作词再对象词，比如「修复删除失败」算 fix 不算 refactor）
KIND_WORDS = (
    ("merge", ("合并分支", "合并上游", "拉取请求", "merge pull", "merge branch")),
    ("revert", ("回滚", "撤销上次", "revert")),
    ("fix", ("修复", "修正", "解决", "补全", "纠正", "兼容", "避免报错", "防止", "缺失", "报错", "失效")),
    ("feat", ("新增", "添加", "支持", "实现", "增加", "引入", "接入", "提供", "完善", "补上", "接入")),
    ("refactor", ("重构", "优化", "调整", "整理", "重命名", "精简", "抽离", "拆分", "移除",
                  "删除", "清理", "统一", "提取", "改造")),
    ("perf", ("性能", "加速", "缓存", "减包", "懒加载", "首屏", "体积", "耗时")),
    ("docs", ("文档", "注释", "说明", "readme", "使用指南", "更新说明")),
    ("test", ("测试", "用例", "spec", "单测")),
    ("style", ("样式", "排版", "布局", "主题", "配色", "字号", "间距", "响应式", "图标")),
    ("build", ("打包", "构建配置", "依赖锁", "vite.config", "webpack", "rollup", "tsconfig")),
    ("ci", ("流水线", "工作流", "workflow", "github action", "部署脚本", "自动构建")),
    ("chore", ("依赖", "版本", "配置", "发布", "忽略", "脚本", "注释掉")),
)


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("git %s -> %s" % (" ".join(args), p.stderr[:300]))
    return p.stdout


def is_ancestor(repo, a, b):
    """a 是否是 b 的祖先（git merge-base --is-ancestor 的返回码）。"""
    p = subprocess.run(["git", "-C", repo, "merge-base", "--is-ancestor", a, b],
                       capture_output=True, text=True)
    return p.returncode == 0


def is_me(name, email):
    return ("DelicateDuck" in (name or "")) or ("DelicateDuck" in (email or "")) or (email in ME_EMAILS)


def classify(subject, files=None):
    """判断提交类型：① 规范前缀（feat: / fix(scope): / feat：）② 中英关键词 ③ 改动文件类型兜底。"""
    s = subject.strip()
    low = s.lower()
    m = re.match(r"^([a-z]+)\s*[（(:：]", low)          # feat: / fix(login): / docs：…
    if m and m.group(1) in KIND_ALIAS:
        return KIND_ALIAS[m.group(1)]
    m = re.match(r"^([a-z]+)[\s]+", low)               # feat xxx
    if m and m.group(1) in KIND_ALIAS and m.group(1) not in ("add", "clean"):
        return KIND_ALIAS[m.group(1)]

    for start_only in (True, False):                   # 先看句首动词，再看是否出现在句中
        for kind, words in KIND_WORDS:
            for w in words:
                if (low.startswith(w) or s.startswith(w)) if start_only else (w in low):
                    return kind

    if files:                                          # 兜底：只动文档 / 测试 / 配置文件
        exts = {os.path.splitext(f)[1].lower() for f in files}
        names = [f.lower() for f in files]
        if exts and exts <= {".md", ".mdx", ".txt", ".rst"}:
            return "docs"
        if names and all(("test" in n or "spec" in n) for n in names):
            return "test"
        if names and all(n.endswith((".json", ".yml", ".yaml", ".toml", ".ini", ".lock", ".env.example"))
                         or n.startswith(".") or "/" not in n and "." not in n for n in names):
            return "chore"
    return "other"


out = {"repos": []}
for cfg in REPOS:
    repo = cfg["repo"]
    heads = [h for h in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines() if h]
    commits = collections.OrderedDict()      # sha -> record
    branch_meta = []

    for br in heads:
        raw = git(repo, "log", br, "--pretty=format:%H" + FS + "%h" + FS + "%an" + FS + "%ae" + FS
                  + "%aI" + FS + "%P" + FS + "%s" + RS)
        mine, total = 0, 0
        for rec in raw.split(RS):
            rec = rec.strip("\n")
            if not rec:
                continue
            parts = rec.split(FS)
            if len(parts) < 7:
                continue
            sha, short, an, ae, date, parents, subject = parts[:7]
            total += 1
            if not is_me(an, ae):
                continue
            mine += 1
            c = commits.get(sha)
            if c is None:
                c = commits[sha] = dict(sha=sha, short=short, name=an, email=ae, date=date,
                                        day=date[:10], parents=parents.split(),
                                        subject=subject, kind=classify(subject), branches=[])
            if br not in c["branches"]:
                c["branches"].append(br)
        days = sorted(commits[s]["day"] for s in commits if br in commits[s]["branches"])
        branch_meta.append(dict(name=br, head=git(repo, "rev-parse", "--short", br).strip(),
                                head_date=git(repo, "log", "-1", "--format=%aI", br).strip()[:10],
                                commits_total=total, commits_mine=mine,
                                parent="", fork_point="", fork_date="", merged_into="",
                                first=days[0] if days else "", last=days[-1] if days else "",
                                span=(days[-1][:4] + "-" + days[-1][5:7]) if days else ""))

    # ---- 真实拓扑：两两 merge-base，推出每条分支真正的父分支、分叉点、是否已并回别的分支 ----
    shas = {b["name"]: git(repo, "rev-parse", b["name"]).strip() for b in branch_meta}
    for b in branch_meta:
        me = b["name"]
        if me == cfg["main"]:                     # 主分支是图的根，没有父
            continue
        cands = []
        for p in shas:
            if p == me:
                continue
            try:
                mb = git(repo, "merge-base", me, p).strip()
            except RuntimeError:                  # 与任何分支都没有共同祖先（独立分支）
                continue
            if not mb or mb == shas[me]:
                continue                          # 分叉点就是本分支自己 → p 其实是从本分支派生的
            cands.append((mb == shas[p], git(repo, "log", "-1", "--format=%aI", mb).strip(),
                          p == cfg["main"], mb[:7], p))
        if cands:
            # ① 分叉点正好是对方 HEAD（直接接续）② 分叉点时间更晚 ③ 平手时优先主分支
            cands.sort(reverse=True)
            _, mb_date, _, mb_short, parent = cands[0]
            b["parent"], b["fork_point"], b["fork_date"] = parent, mb_short, mb_date[:16]
        for q in shas:                            # 已被谁完全包含 → 内容其实已并回那条分支
            if q != me and is_ancestor(repo, me, q):
                b["merged_into"] = q
                break

    # 这些提交改动了哪些文件（--author 过滤在 git 端完成，避免对整个历史做 tree diff）
    files_raw = git(repo, "log", "--all", "--author=DelicateDuck",
                    "--pretty=format:" + RS + "%H", "--name-only")
    filemap = collections.defaultdict(list)
    cur = None
    # 注意：不能用 splitlines()——它会把记录分隔符 \x1e 也当作换行，导致解析不到文件名
    for line in files_raw.split("\n"):
        if line.startswith(RS):
            cur = line[1:].strip()
        elif line.strip() and cur:
            filemap[cur].append(line.strip().strip('"'))   # git 会给含空格/特殊字符的路径加引号
    for sha, c in commits.items():
        fl = filemap.get(sha, [])
        c["files"] = len(fl)
        c["kind"] = classify(c["subject"], fl)     # 拿到改动文件后再补判一次，剩下的「其它」靠文件类型收口
        ext = collections.Counter()
        for f in fl:
            ext[os.path.splitext(f.rsplit("/", 1)[-1])[1].lower() or "(无扩展名)"] += 1
        c["ext"] = [e for e, _ in ext.most_common(4)]
        # 这次改动落在哪些目录（给 AI 归因用：比一堆文件名更能说明「改了哪块」，也省 token）
        dirs = []
        for f in fl:
            parts = f.split("/")
            if len(parts) > 1 and "." in parts[-1]:
                parts = parts[:-1]                          # 去掉文件名，只留目录
            d = "/".join(parts[:3])
            if d and d not in dirs:
                dirs.append(d)
        c["dirs"] = dirs[:5]

    mine_list = sorted(commits.values(), key=lambda c: c["date"])
    months = collections.Counter(c["day"][:7] for c in mine_list)
    kinds = collections.Counter(c["kind"] for c in mine_list)
    exts = collections.Counter(e for c in mine_list for e in c["ext"])
    out["repos"].append(dict(
        key=cfg["key"], url=cfg["url"], main=cfg["main"], branches=branch_meta,
        commits=[{k: c[k] for k in ("sha", "short", "date", "day", "subject", "kind",
                                    "branches", "files", "ext", "dirs", "parents")} for c in mine_list],
        stats=dict(total_branch_commits=sum(b["commits_total"] for b in branch_meta),
                   mine=len(mine_list), months=dict(sorted(months.items())),
                   kinds=dict(kinds.most_common()), exts=dict(exts.most_common(10)),
                   first=mine_list[0]["day"] if mine_list else "",
                   last=mine_list[-1]["day"] if mine_list else "",
                   active_days=len({c["day"] for c in mine_list}),
                   merges=sum(1 for c in mine_list if len(c["parents"]) > 1))))

path = os.path.join(WORK, "worklog-data.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

for r in out["repos"]:
    s = r["stats"]
    print("=" * 70)
    print("%s：分支 %d 个 ｜ 分支提交合计 %d ｜ 其中我 %d 条（%s ~ %s，活跃 %d 天，merge %d 条）"
          % (r["key"], len(r["branches"]), s["total_branch_commits"], s["mine"],
             s["first"], s["last"], s["active_days"], s["merges"]))
    for b in r["branches"]:
        print("   %-30s 总提交 %-5d 我 %-4d %s ~ %s  分叉自 %-26s %s%s"
              % (b["name"], b["commits_total"], b["commits_mine"], b["first"] or "-",
                 b["last"] or "-", b["parent"] or "（无共同祖先）",
                 (b["fork_date"] or "-")[:10],
                 ("  已并回 " + b["merged_into"]) if b.get("merged_into") else ""))
    print("   类型分布 :", s["kinds"])
    print("   后缀分布 :", dict(list(s["exts"].items())[:8]))
    print("   月度分布 :", s["months"])
print("\nJSON ->", path, os.path.getsize(path), "bytes")

