# -*- coding: utf-8 -*-
"""
VNDB Kana API 查询逻辑验证脚本（stdout 版，本脚本不写任何文件）
拿《GALGAME详细目录信息.xlsx》里的游戏名实测新查询逻辑的命中率：
  1) 名称 -> /kana/vn 搜索（search 过滤器，含中文别名匹配）
  2) 命中的 VN -> 一次性查官方补丁 release（patch=true & official=true）
用法：python test_vndb.py > report.md 2> run.log
"""
import json
import re
import time
import urllib.request

import openpyxl

XLSX = r"C:\Users\27088\Documents\WPS Cloud Files\1673608025\GALGAME详细目录信息.xlsx"
PROXIES = {"https": "http://127.0.0.1:10808", "http": "http://127.0.0.1:10808"}
opener = urllib.request.build_opener(urllib.request.ProxyHandler(PROXIES))
API = "https://api.vndb.org/kana"

# 主页面 stripVndbSuffix 的扩展版：循环剥到不动为止
SUFFIX_RES = [
    r"\s*(?:FHD|QHD|HD)\s*Edition$",
    r"\s*-\s*FullVoice\s*Edition\s*-?$",
    r"\s*Plus\s*(?:FHD\s*)?Edition$",
    r"\s*EXTRA\s*1?2?$",
    r"\s*Plus$",
]


def strip_suffix(s: str) -> str:
    s = s.strip()
    for _ in range(4):
        prev = s
        for pat in SUFFIX_RES:
            s = re.sub(pat, "", s, flags=re.I).strip()
        if s == prev:
            break
    return s


def api(endpoint, payload, retries=3):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API + endpoint, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for i in range(retries):
        try:
            with opener.open(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if i == retries - 1:
                return {"error": str(e)}
            time.sleep(2.0 * (i + 1))
    return {}


def load_names():
    wb = openpyxl.load_workbook(XLSX, read_only=True)
    ws = wb["Sheet1"]
    names = []
    seen = set()
    for row in ws.iter_rows(min_row=3, values_only=True):
        for col in (1, 2):  # B=游戏名, C=别名
            v = row[col] if col < len(row) else None
            if v is None:
                continue
            v = str(v).strip()
            if not v or v in ("游戏名", "别名") or v.startswith("="):
                continue
            if v not in seen:
                seen.add(v)
                names.append(v)
    return names


def main():
    names = load_names()
    print(f"共 {len(names)} 个名称待测\n", flush=True)
    rows = []
    for i, name in enumerate(names, 1):
        kw = strip_suffix(name)
        entry = {"input": name, "kw": kw, "vn_hits": [], "miss": False, "err": None}
        data = api("/vn", {
            "filters": ["search", "=", kw],
            "fields": "id,title,alttitle,titles{lang,title},released",
            "results": 3,
        })
        if "error" in data:
            entry["err"] = data["error"]
        results = data.get("results") or []
        if not results:
            entry["miss"] = True
        for v in results[:3]:
            entry["vn_hits"].append({
                "id": v.get("id"), "title": v.get("title"),
                "alttitle": v.get("alttitle"), "released": v.get("released"),
            })
        if results:
            vid = results[0].get("id")
            rdata = api("/release", {
                "filters": ["and",
                            ["vn", "=", ["id", "=", vid]],
                            ["patch", "=", True],
                            ["official", "=", True]],
                "fields": "id,title,released,languages{lang},has_ero",
                "results": 100,
                "sort": "released",
                "reverse": True,
            })
            if "error" in rdata:
                entry["err"] = rdata["error"]
            else:
                rels = rdata.get("results") or []
                zh = [r for r in rels if any(
                    l.get("lang") in ("zh-Hans", "zh-Hant") for l in (r.get("languages") or []))]
                entry["patch_total"] = len(rels)
                entry["patch_zh"] = len(zh)
                entry["patches"] = [{
                    "id": r.get("id"), "title": r.get("title"),
                    "released": r.get("released"),
                    "langs": [l.get("lang") for l in (r.get("languages") or [])],
                } for r in rels[:6]]
        rows.append(entry)
        flag = "MISS" if entry["miss"] else "ok"
        print(f"[{i}/{len(names)}] {flag} {name} -> {kw}", flush=True)
        time.sleep(0.35)

    hits = [r for r in rows if not r["miss"] and not r["err"]]
    misses = [r for r in rows if r["miss"]]
    errs = [r for r in rows if r["err"]]

    print("\n# VNDB 查询逻辑验证报告\n")
    print(f"- 名称总数：{len(rows)}（游戏名+别名去重）")
    print(f"- VN 搜索命中：{len(hits)}，未命中：{len(misses)}，请求出错：{len(errs)}")
    if misses:
        print("\n## 未命中\n")
        for r in misses:
            print(f"- {r['input']}（查询词：{r['kw']}）")
    if errs:
        print("\n## 请求出错\n")
        for r in errs:
            print(f"- {r['input']}：{r['err']}")
    print("\n## 全部明细\n")
    print("| 输入 | 查询词 | VN top1 | alttitle | 发售 | 官方补丁(总/含中文) |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        if r["vn_hits"]:
            v = r["vn_hits"][0]
            pt = f"{r.get('patch_total','-')}/{r.get('patch_zh','-')}"
            print(f"| {r['input']} | {r['kw']} | {v['id']} {v['title']} "
                  f"| {v.get('alttitle') or ''} | {v.get('released') or ''} | {pt} |")
        else:
            print(f"| {r['input']} | {r['kw']} | — | — | — | — |")
    print(f"\n完成：命中 {len(hits)} / {len(rows)}，缺失 {len(misses)}，错误 {len(errs)}", flush=True)


if __name__ == "__main__":
    main()
