# GALGAME 补丁搜索（vn.ciallo.sale）

Cloudflare Pages 项目 `galgame-patch` 的整站源码，存放在 pub-homepage 仓库的 `galgame-patch` 分支（仅作源码存档与版本管理；**发布不走 Git**，该项目无 Git 集成，用 wrangler 直传）。

## 目录

```
site/               发布目录（部署对象）
  index.html        根跳转 stub（→ /web.html，兼 404 兜底）
  web.html          主页：VNDB 搜索（补丁/游戏双模式）+ 会社导航
  companies/*.html  20 个会社详情页（由生成器产出，勿手改）
  logo.png
build_companies.py  会社页生成器：提取旧页 GAMES 数据 → 新模板 → 落盘前校验 → 原子写入
DESIGN_SPEC.md      设计规范（极简科技黑 × 日式搜索页，与 www.ciallo.sale 同 token）
api-test/           VNDB Kana API 查询逻辑验证脚本与实测报告（133 名命中 113）
```

## 常用操作

```bash
# 改了会社数据 / 模板后重新生成（20/20 PASS 才会落盘）
python build_companies.py

# 本地预览
python -m http.server 8791 --directory site   # http://127.0.0.1:8791/web.html

# 部署（Cloudflare Pages，走本机代理）
E:/DEV/cf-cli/wrangler.cmd pages deploy site --project-name galgame-patch --branch main --commit-dirty=true
```

## 硬约束

- `site/companies/*.html` 数据区的 `GAMES`（name / alias / link 补丁下载链接 / official 官方页）与
  `COMPANIES`（web.html 内）必须逐字节保留——改样式只动模板区，数据一律走生成器。
- 站点仅深色一套主题；设计 token 见 `DESIGN_SPEC.md`。
- VNDB Kana API 的 `rating` 为 0-100，页面按 ÷10 的 10 分制显示。
