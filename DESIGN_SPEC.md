# GALGAME 补丁搜索 · 设计规范 v1（极简科技黑 × 日式搜索页）

> 本文件是重设计的唯一样式依据。两个页面（web.html、companies/*.html）必须完全一致地遵守。
> 风格基准：www.ciallo.sale 主页（Linear/Vercel 式极简科技黑）＋ 日式搜索结果页的排版习惯（窄列、左缩略图右信息、结果数状态行、分段控件）。

## 0. 硬性规则

- **只有深色一套**。禁止浅色主题、禁止 `data-theme` 切换、禁止 themeToggle 按钮/JS/CSS。
- **保留中/英切换**（zh/en），沿用 `localStorage` key `vndb_lang`，沿用现有 I18N 词条文案（除明确注明者外）。
- 界面 chrome 不用 emoji 图标（用内联 SVG 或纯文字）；正文提示区可保留少量 emoji。
- 不引外部资源（无 CDN 字体/图标库），全部内联，单文件自包含。

## 1. Design Tokens（原样粘贴）

```css
:root {
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
}
```

- 字体栈：`-apple-system, "Segoe UI Variable Text", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif`
- 等宽（日期/计数/链接）：`ui-monospace, "Cascadia Mono", "SF Mono", Consolas, monospace`，字号 12px。
- 圆角：卡片 12px；输入框/按钮 10px；chip 标签 6px。
- 选中态：`::selection { background: color-mix(in srgb, var(--accent) 32%, transparent); }`
- 焦点：`:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }`

## 2. 组件

| 组件 | 规格 |
|---|---|
| 卡片/行 | `--card` 底 + `1px solid var(--border)`，radius 12px；hover：边框 `--accent`（≤40% 不透明度可用 color-mix），背景 `--card-hover`，过渡 `0.25s var(--ease)`，**不要**位移动画 |
| 主按钮 | `--fg` 底 + `--bg` 字（白底黑字），600 weight；hover 亮度 +6% |
| 次按钮（ghost） | 透明底，`box-shadow: inset 0 0 0 1px var(--border-strong)`，字 `--fg`；hover 边框 `--accent`、字 `--accent` |
| 强调链接/chip | 字 `--accent`；chip：`--accent-soft` 底 + `--accent` 字 + radius 6px + 11px |
| 禁用 | 背景 `transparent`，inset 边框 `--border`，字 `--muted`，`cursor: not-allowed` |
| 输入框 | `--card` 底、`--border` 边、radius 10px、字 `--fg`；focus：边框 `--accent` + `box-shadow: 0 0 0 3px var(--accent-soft)` |
| 状态行 | 结果列表上方一行：等宽小字 `--muted`：「约 N 条结果 · 0.42 秒」样式 |
| 分段控件（模式切换） | 胶囊容器 `--card` 底 + `--border`；选中段 `--accent-soft` 底 + `--accent` 字；未选中 `--muted`；替代旧 iOS 开关 |

## 3. 布局

- header：sticky；`background: rgba(10, 10, 11, 0.82)` + `backdrop-filter: blur(12px)`；底边 1px `--border`；高约 56px；左：站名（15px/600，logo 图可选 20px 圆角），右：语言按钮（ghost 小号）。
- 内容列：**max-width 760px** 居中（日式搜索页窄列）；内边距 `40px 20px 80px`。
- 页脚：居中 13px `--muted`，上边 1px `--border`，文案不变。

## 4. 搜索结果行（日式样式核心）

每条结果 = 水平行：
- 左：封面缩略图（宽 96px，radius 8px，1px `--border`；无图则占位块 `--card` 底 + `--muted` 的「无封面」小字）。
- 右（min-width:0）：
  1. 标题行：日文原题（15px/600 `--fg`）＋ 同行跟小号别名/中文名（13px `--muted`，超出省略）。
  2. 元信息行（等宽 12px `--muted`）：`会社 · YYYY-MM-DD · 时长 · ★7.85 (1.2k)`。
  3. chip 行：语言/其他标签。
  4. 动作行：`VNDB 页面 →` 强调链接＋其他必要链接（等宽展示 URL 可省略，只留文案）。
- 展开详情（保持现有能力）：面板 `--card` 底 radius 8px，简介、剧透折叠、外链；交互沿用现有逻辑，视觉套用新 token。

## 5. 会社网格（web.html 用）

- 每社一张卡：会社名（15px/600）＋ 数量（等宽 12px `--muted`，`N games`）。
- 卡片 hover：边框 `--accent`。网格 `repeat(auto-fill, minmax(210px, 1fr))`，gap 12px。
- 折叠区标题行：左侧小标题（14px/600 `--muted` 全大写字距 0.04em 英文），右侧「展开/收起」ghost 小按钮；不用大 emoji。

## 6. 会社详情页（companies/*.html）

- header：左「← 返回」ghost 链接（→ `../web.html`），中/左站名，右语言按钮。
- 列表头：`{会社名}`（18px/700）＋右侧计数（等宽 `--muted`：`共 N 款` / `N games`）。
- 游戏行：名字（15px/600 `--fg`）、别名（13px `--muted`）、元信息（等宽 12px `--muted`：`{会社} · Steam`）；右侧按钮组：`官方下载页面`（ghost）＋ `下载补丁`（primary；`link === 'https://example.com'` 时禁用态）。
- 移动端 ≤640px：行改纵向堆叠，按钮组右对齐换行。

## 7. 文案（i18n 增改项）

- 新增 `resultCount`：`约 {n} 条结果 · {s} 秒` / `About {n} results · {s}s`
- `patchModeTip` 改为：`显示匹配游戏的全部官方补丁，含中文的排在前面` / `Shows all official patches of matched games; Chinese first`
- 移除与夜间模式相关的任何 UI 文案。
