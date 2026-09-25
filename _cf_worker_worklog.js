/**
 * 工作日志数据服务 —— Cloudflare Worker + KV
 * ---------------------------------------------------------------------------
 * 它干三件事：
 *   1) cron（默认每天 21:30 北京时间）：用 GitHub API 抓两个仓库的分支、分叉关系与我的提交，
 *      整理成和本地 worklog-data.json 同构的数据，写进 KV；
 *      数据有变化时可选地给站点仓库发 repository_dispatch，让 GitHub Actions 重新渲染页面。
 *   2) 可选：用 Workers AI（绑定 AI）逐条判定提交类型（在既有的 13 个类型里选），存 KV 并提供
 *      GET /classify.json 给页面生成脚本取用（没有绑定就跳过，不影响采集）；
 *   3) 给页面打开时「先拉一下」用（KV 里的键：data / localData / stage / dataHash / kinds / timeline / page / pageAt）：
 *      GET /stage.json     轻量摘要（生成时间、总量、最新几条提交）——页面拿它比对自家构建时间；
 *      GET /timeline.json  时间线快照（每条提交一行，kind 已按 AI 判定盖过）；
 *                          ?since=<ISO> 只回比它新的，页面用它把新提交补出来，不必等整页重建；
 *      GET /data.json      完整数据（与 worklog-data.json 同构，排查用）；
 *      GET /classify.json  每条提交的 AI 类型判定（页面生成脚本取它落到类型上）；
 *      GET /page.html      最近一次发布上来的整页 HTML（本地任务或 Actions 用 PUT /publish 上传），
 *                          页面发现它更新时会直接换掉正文，等于打开就拿到最新渲染结果。
 *   4) 写入接口（需要 PUBLISH_TOKEN）：
 *      PUT /publish/page.html   上传页面 HTML
 *      PUT /publish/data.json   上传完整数据
 *      POST /sync               立刻抓一次（排查/手动触发用）
 *
 * 绑定与变量（见 wrangler.toml）：
 *   KV 绑定 WL              KV 命名空间
 *   AI 绑定 AI              可选：Workers AI（wrangler.toml 里 [ai] binding = "AI"），逐条判提交类型用
 *   Secrets GH_TOKEN        GitHub 只读（私有仓库必需；公开仓库也建议加，才不会被限流）
 *           PUBLISH_TOKEN   写接口口令，随便一串随机字符
 *   Vars    REPOS           JSON：仓库清单
 *           ME_LOGIN        我的 GitHub 登录名
 *           DISPATCH_REPO   可选：站点仓库（owner/repo），填了就发 repository_dispatch
 * ---------------------------------------------------------------------------
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,PUT,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type,Authorization',
};

const HIDDEN = /^(cloudflare\/|cf-|workers-|dependabot\/|renovate\/)/i;

/* GitHub 的时间一律是 UTC；页面的口径是作者本地时区（北京 +08:00），统一在这儿换算一次 */
function bjIso(utc) {
  return new Date(Date.parse(utc) + 8 * 3600e3).toISOString().slice(0, 19) + '+08:00';
}

/* 分类规则与本地 _collect.py 保持一致：规范前缀 → 关键词 → 改动文件兜底 */
const KIND_ALIAS = {
  feat: 'feat', feature: 'feat', impl: 'feat', implement: 'feat', add: 'feat',
  fix: 'fix', bugfix: 'fix', hotfix: 'fix', patch: 'fix',
  refactor: 'refactor', refact: 'refactor', clean: 'refactor', cleanup: 'refactor',
  perf: 'perf', performance: 'perf', optimize: 'perf', optimise: 'perf',
  docs: 'docs', doc: 'docs', readme: 'docs',
  style: 'style', format: 'style', fmt: 'style', lint: 'style',
  test: 'test', tests: 'test', spec: 'test',
  chore: 'chore', deps: 'chore', dep: 'chore', release: 'chore', bump: 'chore',
  build: 'build', ci: 'ci', cd: 'ci', revert: 'revert', merge: 'merge',
  wip: 'other', i18n: 'feat', a11y: 'feat',
  security: 'fix', sec: 'fix', hardening: 'fix',
  sync: 'refactor', port: 'refactor', move: 'refactor',
  diagnose: 'chore', debug: 'chore', log: 'chore', logs: 'chore',
};

const KIND_WORDS = [
  ['merge', ['合并分支', '合并上游', '拉取请求', 'merge pull', 'merge branch']],
  ['revert', ['回滚', '撤销上次', 'revert']],
  ['fix', ['修复', '修正', '解决', '补全', '纠正', '兼容', '避免报错', '防止', '缺失', '报错', '失效']],
  ['feat', ['新增', '添加', '支持', '实现', '增加', '引入', '接入', '提供', '完善', '补上']],
  ['refactor', ['重构', '优化', '调整', '整理', '重命名', '精简', '抽离', '拆分', '移除', '删除', '清理', '统一', '提取', '改造']],
  ['perf', ['性能', '加速', '缓存', '减包', '懒加载', '首屏', '体积', '耗时']],
  ['docs', ['文档', '注释', '说明', 'readme', '使用指南', '更新说明']],
  ['test', ['测试', '用例', 'spec', '单测']],
  ['style', ['样式', '排版', '布局', '主题', '配色', '字号', '间距', '响应式', '图标']],
  ['build', ['打包', '构建配置', '依赖锁', 'vite.config', 'webpack', 'rollup', 'tsconfig']],
  ['ci', ['流水线', '工作流', 'workflow', 'github action', '部署脚本', '自动构建']],
  ['chore', ['依赖', '版本', '配置', '发布', '忽略', '脚本', '注释掉']],
];

function classify(subject, files) {
  const s = String(subject || '').trim();
  const low = s.toLowerCase();
  let m = low.match(/^([a-z]+)\s*[（(:：]/);
  if (m && KIND_ALIAS[m[1]]) return KIND_ALIAS[m[1]];
  m = low.match(/^([a-z]+)\s+/);
  if (m && KIND_ALIAS[m[1]] && m[1] !== 'add' && m[1] !== 'clean') return KIND_ALIAS[m[1]];
  for (const startOnly of [true, false]) {
    for (const [kind, words] of KIND_WORDS) {
      for (const w of words) {
        if (startOnly ? (low.startsWith(w) || s.startsWith(w)) : low.indexOf(w) >= 0) return kind;
      }
    }
  }
  if (files && files.length) {
    const exts = new Set(files.map((f) => (f.match(/\.[^./]+$/) || [''])[0].toLowerCase()));
    const names = files.map((f) => f.toLowerCase());
    if (exts.size && [...exts].every((e) => ['.md', '.mdx', '.txt', '.rst'].includes(e))) return 'docs';
    if (names.every((n) => n.indexOf('test') >= 0 || n.indexOf('spec') >= 0)) return 'test';
    if (names.every((n) => /\.(json|yml|yaml|toml|ini|lock|example|env)$/.test(n) || n.startsWith('.'))) return 'chore';
  }
  return 'other';
}

/* 改动文件的后缀统计；plain = 全是配置类/点文件（与本地 _collect.py 的兜底一致） */
function extInfo(files) {
  const c = {};
  let plain = true;
  (files || []).forEach((f) => {
    const base = String(f.filename || '').split('/').pop();
    const tail = base.startsWith('.') ? base.slice(1) : base;   /* 与 os.path.splitext 一致：点开头不算后缀 */
    const m = tail.match(/\.[^.]*$/);
    const e = (m ? m[0] : '').toLowerCase() || '(无扩展名)';
    c[e] = (c[e] || 0) + 1;
    if (!(/\.(json|yml|yaml|toml|ini|lock|example|env)$/.test(base) || base.startsWith('.') || base.indexOf('.') < 0)) {
      plain = false;
    }
  });
  return { ext: Object.entries(c).sort((a, b) => b[1] - a[1]).slice(0, 12), plain: !!files.length && plain };
}

function jsonOut(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200,
    headers: Object.assign({ 'Content-Type': 'application/json; charset=utf-8' }, CORS),
  });
}

async function gh(env, path, budget) {
  if (budget) {
    /* 免费版单次调用只有 50 个子请求：预算用完就抛出去（结果都已进 KV 缓存），下次接着跑 */
    if (budget.left <= 0) {
      const err = new Error('SUBREQUEST_BUDGET');
      err.budget = true;
      throw err;
    }
    budget.left -= 1;
  }
  const headers = {
    Accept: 'application/vnd.github+json',
    'User-Agent': 'delicateduck-worklog',
    'X-GitHub-Api-Version': '2022-11-28',
  };
  if (env.GH_TOKEN) headers.Authorization = 'Bearer ' + env.GH_TOKEN;
  const res = await fetch('https://api.github.com' + path, { headers });
  if (!res.ok) {
    const t = await res.text();
    throw new Error('GitHub ' + path + ' -> ' + res.status + ' ' + t.slice(0, 180));
  }
  return res;
}

async function ghJson(env, path, budget) {
  return (await gh(env, path, budget)).json();
}

/* 分支总提交数：per_page=1 时 Link 头里的 rel="last" 页号就是总条数 */
function lastPage(res) {
  const link = res.headers.get('Link') || '';
  const m = link.match(/[?&]page=(\d+)>;\s*rel="last"/);
  return m ? parseInt(m[1], 10) : 1;
}

/* ---------------- 抓取：分支、分叉关系、我的提交 ---------------- */
async function collect(env, budget) {
  const repos = JSON.parse(env.REPOS);
  const me = env.ME_LOGIN || 'DelicateDuck582';
  const since = new Date(Date.now() - 400 * 864e5).toISOString();
  const result = { generatedAt: new Date().toISOString(), me, repos: [] };

  /* 日期口径：本地推上来的那份（localData）里的 sha 直接沿用它的原始时区时间，
     这样云端和页面的「哪天提交的」一致；本地没见过的新提交才用 +08:00 估算。
     注意读的是 localData 而不是 data —— data 会被云端采集覆盖，读它就成自我参照了 */
  const dateMap = {};
  try {
    const localRaw = await env.WL.get('localData');
    if (localRaw) {
      JSON.parse(localRaw).repos.forEach((r) => r.commits.forEach((c) => { dateMap[c.sha] = c.date; }));
    }
  } catch (e) {
    console.log('本地日期映射读取失败（用 +08:00 兜底）：' + e.message);
  }

  for (const cfg of repos) {
    const brs = (await ghJson(env, `/repos/${cfg.repo}/branches?per_page=100`, budget))
      .filter((b) => !HIDDEN.test(b.name));
    const names = brs.map((b) => b.name);
    const meta = {};

    for (const b of brs) {
      const mine = await ghJson(env, `/repos/${cfg.repo}/commits?sha=${encodeURIComponent(b.name)}`
        + `&author=${encodeURIComponent(me)}&since=${since}&per_page=100`, budget);
      /* 分支 HEAD 的 sha 与时间 + 分支总条数：HEAD 没动就用 KV 里那份，省一次子请求 */
      const bh = String((b.commit && b.commit.sha) || '');
      const hkey = `head:${cfg.repo}:${b.name}:${bh}`;
      let h = await env.WL.get(hkey, 'json');
      if (!h) {
        const r1 = await gh(env, `/repos/${cfg.repo}/commits?sha=${encodeURIComponent(b.name)}&per_page=1`, budget);
        const head = (await r1.json())[0] || {};
        h = {
          sha: head.sha || bh,
          date: String((head.commit && head.commit.author && head.commit.author.date) || '').slice(0, 10),
          total: lastPage(r1),
        };
        await env.WL.put(hkey, JSON.stringify(h), { expirationTtl: 60 * 86400 });
      }
      meta[b.name] = {
        name: b.name,
        head: String(h.sha || bh).slice(0, 12),
        head_full: h.sha || bh,
        head_date: h.date,
        commits_total: h.total || 1,
        mine,
      };
    }

    /* 父子关系：两两 compare 取 merge_base（GitHub 直接给 merge_base_commit），结果按 HEAD 缓存 */
    const base = {};
    for (const a of names) {
      for (const b of names) {
        if (a === b) continue;
        const key = `mb2:${cfg.key}:${a}:${b}:${meta[a].head_full}:${meta[b].head_full}`;
        let hit = await env.WL.get(key, 'json');
        if (!hit) {
          const d = await ghJson(env, `/repos/${cfg.repo}/compare/${encodeURIComponent(a)}...${encodeURIComponent(b)}`, budget);
          hit = d.merge_base_commit
            ? { sha: d.merge_base_commit.sha, date: bjIso(d.merge_base_commit.commit.author.date), status: d.status }
            : null;
          await env.WL.put(key, JSON.stringify(hit), { expirationTtl: 90 * 86400 });
        }
        base[a + '|' + b] = hit;
      }
    }

    for (const b of names) {
      const m = meta[b];
      if (b === cfg.main) continue;                         /* 主分支是图的根 */
      const cands = [];
      for (const p of names) {
        if (p === b) continue;
        const mb = base[p + '|' + b];
        if (!mb || !mb.sha || mb.sha === m.head_full) continue;   /* 分叉点是自己 → p 其实从本分支派生 */
        cands.push([mb.sha === meta[p].head_full ? 1 : 0, mb.date || '', p === cfg.main ? 1 : 0,
                    mb.sha.slice(0, 7), p]);                       /* ① 分叉点=对方 HEAD ② 更晚 ③ 主分支 */
      }
      cands.sort((x, y) => (y[0] - x[0]) || (y[1] > x[1] ? 1 : y[1] < x[1] ? -1 : 0) || (y[2] - x[2]));
      if (cands.length) {
        m.parent = cands[0][4];
        m.fork_point = cands[0][3];
        m.fork_date = cands[0][1];
      }
      for (const q of names) {                              /* 已被谁完全包含 → 内容其实已并回那条分支 */
        if (q === b) continue;
        const c = base[q + '|' + b];
        if (c && c.status === 'behind') { m.merged_into = q; break; }
      }
    }

    /* 我的提交：按 sha 去重，改动文件数按 sha 缓存在 KV（第二天再抓只补新的） */
    const commits = {};
    for (const b of names) {
      for (const c of meta[b].mine) {
        const sha = c.sha;
        if (!commits[sha]) {
          const fkey = `f:${cfg.key}:${sha}`;
          let f = await env.WL.get(fkey, 'json');
          if (!f) {
            const d = await ghJson(env, `/repos/${cfg.repo}/commits/${sha}`, budget);
            f = Object.assign({ files: (d.files || []).length }, extInfo(d.files || []));
            await env.WL.put(fkey, JSON.stringify(f), { expirationTtl: 400 * 86400 });
          }
          const subject = String(c.commit.message || '').split('\n')[0];
          /* 口径与本地 _collect.py 的 %aI 对齐：GitHub 给 UTC，这里换算成作者时区（北京 +08:00），
             否则 9-25 早上的提交会被算到 UTC 的 9-24，days 也就和本地对不上 */
          /* 优先用本地那份的原始时区时间（见上面 dateMap），新提交才用 UTC+8 估算 */
          const date = dateMap[sha] || bjIso(c.commit.author.date);
          let kind = classify(subject, null);
          if (kind === 'other' && f.ext.length) {           /* 兜底：只动文档 / 只动配置（点文件也算） */
            const es = f.ext.map((e) => e[0]);
            if (es.every((e) => ['.md', '.mdx', '.txt', '.rst'].includes(e))) kind = 'docs';
            else if (f.plain) kind = 'chore';
          }
          commits[sha] = {
            sha, short: sha.slice(0, 7), subject, kind, date, day: date.slice(0, 10),
            files: f.files, ext: f.ext, merge: (c.parents || []).length > 1 ? 1 : 0, branches: [],
          };
        }
        if (commits[sha].branches.indexOf(b) < 0) commits[sha].branches.push(b);
      }
    }

    /* 同一提交出现在多条包含它的分支里：归给拓扑上最深的一条 */
    Object.keys(commits).forEach((sha) => {
      const c = commits[sha];
      const inb = c.branches.slice();
      let cands = inb.filter((n) => inb.indexOf(meta[n].parent) < 0);
      if (!cands.length) cands = inb;
      cands.sort((a, b) => ((meta[b].fork_date || '') > (meta[a].fork_date || '') ? 1 : -1));
      c.own = cands[0] || '';
    });

    const list = Object.keys(commits).map((k) => commits[k]).sort((a, b) => (a.date < b.date ? 1 : -1));
    const branchOut = names.map((n) => {
      const days = list.filter((c) => c.branches.indexOf(n) >= 0).map((c) => c.day).sort();
      const o = Object.assign({}, meta[n]);
      delete o.mine;
      o.head_full = undefined;
      o.commits_mine = days.length;
      o.first = days[0] || '';
      o.last = days[days.length - 1] || '';
      return o;
    });

    const kinds = {}, exts = {};
    list.forEach((c) => {
      kinds[c.kind] = (kinds[c.kind] || 0) + 1;
      (c.ext || []).forEach((e) => { exts[e[0]] = (exts[e[0]] || 0) + e[1]; });
    });
    const allDays = new Set(list.map((c) => c.day));

    result.repos.push({
      key: cfg.key, repo: cfg.repo, url: cfg.url || ('https://github.com/' + cfg.repo),
      main: cfg.main, branches: branchOut, commits: list,
      stats: {
        mine: list.length,
        first: list.length ? list[list.length - 1].day : '',
        last: list.length ? list[0].day : '',
        active_days: allDays.size,
        merges: list.filter((c) => c.merge).length,
        kinds, exts,
        total_branch_commits: branchOut.reduce((s, b) => s + b.commits_total, 0),
      },
    });
  }
  return result;
}

/* 轻量摘要：页面打开时先拉它，跟自家构建时间比一比 */
function stageOf(data) {
  const newest = [];
  data.repos.forEach((r) => r.commits.forEach((c) => newest.push({
    day: c.day, repo: r.key, subject: c.subject, short: c.short, kind: c.kind,
    branches: c.branches, files: c.files, url: r.url + '/commit/' + c.sha,
  })));
  newest.sort((a, b) => (a.day < b.day ? 1 : a.day > b.day ? -1 : 0));
  const days = [...new Set(data.repos.flatMap((r) => r.commits.map((c) => c.day)))].sort();
  return {
    generatedAt: data.generatedAt,
    mine: data.repos.reduce((s, r) => s + r.stats.mine, 0),
    days: days.length,
    first: days[0] || '',
    last: days[days.length - 1] || '',
    repos: data.repos.map((r) => ({
      key: r.key, mine: r.stats.mine, branches: r.branches.length,
      first: r.stats.first, last: r.stats.last,
    })),
    newest: newest.slice(0, 20),
  };
}

/* 时间线快照：每条提交压成一行小对象存 KV（kind 用 AI 判定盖过的），页面按自己的构建时间取增量 */
function timelineOf(data, kinds) {
  const ks = kinds || {};
  const items = [];
  data.repos.forEach((r) => r.commits.forEach((c) => items.push({
    sha: c.sha, short: c.short, day: c.day, date: c.date, repo: r.key,
    subject: c.subject, kind: ks[c.sha] || c.kind,
    branches: c.branches, files: c.files, url: r.url + '/commit/' + c.sha,
  })));
  items.sort((a, b) => (Date.parse(b.date) || 0) - (Date.parse(a.date) || 0));
  return { at: new Date().toISOString(), generatedAt: data.generatedAt || '',
           mine: items.length, items };
}

async function putTimeline(env, data) {
  const store = (await env.WL.get('kinds', 'json')) || {};
  await env.WL.put('timeline', JSON.stringify(timelineOf(data, store.kinds || {})));
}

/* ---------------- 抓一次并落库 ---------------- */
async function sha1(text) {
  const buf = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/* ---------------- Workers AI：逐条判定提交类型（在既有 13 个类型里选） ----------------
 * 用途：前缀 + 关键词那套规则会判错（比如 sync(...): 同步并加新能力，本该是 feat）；
 *      这里把每条提交的标题 / 改动目录 / 文件数连同规则判定一起交给模型，让它重挑一个类型。
 * 绑定：[ai] binding = "AI"（见 wrangler.toml）；没绑定或调用失败就跳过，页面照旧。
 * 缓存：结果存在 KV 的 kinds 对象里（sha → 类型键），只对没判过的提交花额度。
 * --------------------------------------------------------------------------- */
const KIND_MODEL = '@cf/meta/llama-3.3-70b-instruct-fp8-fast';   /* 免费额度内；换模型只改这一行 */
const KIND_BATCH = 25;                                           /* 每次调用喂多少条提交 */
const KIND_KEYS = ['feat', 'fix', 'refactor', 'merge', 'docs', 'chore', 'style', 'perf',
                   'test', 'build', 'ci', 'revert', 'other'];
const KIND_SYS = [
  '你在给一个开发者工作日志校正「提交类型」：读每条提交的信息，在固定类型里挑一个。',
  '可选类型（只能输出这些键，原文英文小写）：',
  '  feat 新功能（新增能力 / 新接口 / 新页面）、fix 修复（修 bug、兜底兼容、修安全问题）、',
  '  refactor 重构（只调结构不改行为）、perf 性能、style 样式与格式、docs 文档（只改 .md / 注释 / 说明）、',
  '  test 测试、build 构建（依赖、lock、打包）、ci（工作流、部署脚本）、chore 杂项（版本号、清理这类小活）、',
  '  merge 合并提交（Merge branch / Merge pull request）、revert 回滚、other 其它（实在判断不了）。',
  '输入：{"repo":"仓库名","hint":"这个仓库是什么","commits":[{"sha":"...","subject":"标题","was":"规则按前缀/关键词判的类型","dirs":["改动目录"],"files":文件数}]}',
  '输出：只输出一个 JSON 对象，键是原样的 sha，值是上面 13 个键之一；不要解释、不要代码围栏、不要别的字段。',
  'was 只是参考，标题前缀常常不靠谱：例如「sync(cos-exchange): 与 browse-alist 同步 worker（/temp 在线查看 + Range + 整目录筛选）」是在加新能力，应判 feat，而不是按 sync 前缀当 chore。',
].join('\n');

/* 各仓库是什么，先告诉模型，帮它别按另一个仓库的思路判断 */
const REPO_HINT = {
  'cloud-mail': '邮箱系统：邮件收发与阅读、附件、云盘/文件浏览、多账号、管理后台、Cloudflare Worker 后端',
  'SPlayer': '音乐播放器：播放、歌单、歌词、搜索、下载、第三方 API 适配',
};

function parseAiJson(resp) {
  /* 容错解析：response 可能是字符串（也许包 ```json 围栏、前后带句解释），也可能已经被解析成对象 */
  if (!resp) return null;
  let o = resp;
  if (typeof resp === 'string') {
    const s = resp.replace(/```[a-z]*\n?/gi, '').trim();
    const a = s.indexOf('{'), b = s.lastIndexOf('}');
    if (a < 0 || b <= a) return null;
    try { o = JSON.parse(s.slice(a, b + 1)); } catch (e) { return null; }
  }
  return o && typeof o === 'object' && !Array.isArray(o) ? o : null;
}

/* 凑出要判类型的提交：提交清单以云端那份（data）为准，改动目录优先取本地那份（localData） */
async function kindItems(env) {
  const pick = async (key) => {
    const raw = await env.WL.get(key);
    try { return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  };
  const locals = await pick('localData');
  const remote = await pick('data');
  const dirsOf = {};
  ((locals && locals.repos) || []).forEach((r) => r.commits.forEach((c) => { dirsOf[c.sha] = c.dirs || []; }));
  const src = (remote && remote.repos && remote.repos.length) ? remote : locals;
  if (!src || !src.repos) return [];
  const seen = new Set();
  const out = [];
  src.repos.forEach((r) => r.commits.forEach((c) => {
    if (seen.has(c.sha)) return;
    seen.add(c.sha);
    out.push({
      repo: r.key, sha: c.sha, subject: c.subject, was: c.kind || 'other',
      dirs: dirsOf[c.sha] || [], files: c.files,
    });
  }));
  return out;
}

async function classifyBatch(env, repo, list) {
  const messages = [
    { role: 'system', content: KIND_SYS },
    { role: 'user', content: JSON.stringify({
      repo,
      hint: REPO_HINT[repo] || '',
      commits: list.map((c) => ({
        sha: c.sha.slice(0, 12), subject: c.subject, was: c.was, dirs: c.dirs, files: c.files,
      })),
    }) },
  ];
  const out = await env.AI.run(KIND_MODEL, { messages, max_tokens: 700, temperature: 0.1 });
  const got = parseAiJson(out && out.response) || {};
  const map = {};
  list.forEach((c) => {
    const v = got[c.sha.slice(0, 12)] || got[c.sha];
    const k = typeof v === 'string' ? v.trim().toLowerCase() : '';
    if (KIND_KEYS.indexOf(k) >= 0) map[c.sha] = k;      /* 只认这 13 个键，别的当没答 */
  });
  return { map, usage: (out && out.usage) || null };
}


async function classifyKinds(env, maxBatches, reset) {
  if (!env.AI) return { ok: false, error: '没有 Workers AI 绑定（wrangler.toml 里加 [ai] binding = "AI"）' };
  const items = await kindItems(env);
  const store = (await env.WL.get('kinds', 'json')) || {};
  const kinds = reset ? {} : Object.assign({}, store.kinds || {});
  const todo = items.filter((c) => !kinds[c.sha]);
  const byRepo = {};
  todo.forEach((c) => { (byRepo[c.repo] = byRepo[c.repo] || []).push(c); });

  let done = 0, calls = 0, neurons = 0;
  for (const repo of Object.keys(byRepo)) {
    const list = byRepo[repo];
    for (let i = 0; i < list.length; i += KIND_BATCH) {
      if (calls >= (maxBatches || 6)) break;
      let got;
      try {
        got = await classifyBatch(env, repo, list.slice(i, i + KIND_BATCH));
      } catch (e) {
        console.log('类型判定失败：' + e.message);
        break;
      }
      calls += 1;
      if (got.usage && got.usage.neurons) neurons += got.usage.neurons;
      Object.keys(got.map).forEach((sha) => { kinds[sha] = got.map[sha]; done += 1; });
      await env.WL.put('kinds', JSON.stringify({
        at: new Date().toISOString(), model: KIND_MODEL,
        usage: Math.round(neurons * 10) / 10, kinds,
      }));
    }
  }
  const pending = items.filter((c) => !kinds[c.sha]).length;
  return { ok: true, total: items.length, classified: done, pending, calls };
}

async function ghWrite(env, path, payload) {
  const token = env.DISPATCH_TOKEN || env.GH_TOKEN;
  const res = await fetch('https://api.github.com' + path, {
    method: 'POST',
    headers: {
      Accept: 'application/vnd.github+json',
      'User-Agent': 'delicateduck-worklog',
      'X-GitHub-Api-Version': '2022-11-28',
      Authorization: 'Bearer ' + token,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error('GitHub ' + path + ' -> ' + res.status + ' ' + (await res.text()).slice(0, 180));
}

async function sync(env, force, budget) {
  const data = await collect(env, budget || { left: 40 });
  const body = JSON.stringify(data);
  const hash = await sha1(body);
  const prev = await env.WL.get('dataHash');
  const stage = stageOf(data);
  await env.WL.put('data', body);
  await env.WL.put('stage', JSON.stringify(stage));
  await env.WL.put('dataHash', hash);
  /* AI 类型判定：数据变了、或还有提交没判过，就顺手做几批（失败不影响采集结果） */
  let kinds = 'skipped';
  if (force || hash !== prev || !(await env.WL.get('kinds'))) {
    try {
      const r = await classifyKinds(env, 6, false);
      kinds = r.ok === false ? 'failed' : (r.classified + '/' + r.total + '，还差 ' + r.pending);
    } catch (e) {
      kinds = 'failed';
      console.log('AI 类型判定失败：' + e.message);
    }
  }
  /* 有新的提交就只判新的那几个，然后把时间线快照（含刚判出来的类型）写进 KV */
  await putTimeline(env, data);
  let dispatched = false;
  if ((force || hash !== prev) && env.DISPATCH_REPO) {
    /* 数据有变化：叫站点仓库重新渲染一次页面（GitHub Actions 里跑生成脚本 → Pages 自动发布） */
    await ghWrite(env, `/repos/${env.DISPATCH_REPO}/dispatches`, { event_type: 'worklog-refresh' });
    dispatched = true;
  }
  return { ok: true, changed: force || hash !== prev, dispatched, mine: stage.mine, generatedAt: stage.generatedAt, kinds };
}

function textOut(body, type, status) {
  return new Response(body, {
    status: status || 200,
    headers: Object.assign({ 'Content-Type': (type || 'text/plain') + '; charset=utf-8' }, CORS),
  });
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(sync(env, false, { left: 40 }).catch((e) => console.log(
      (e.budget ? '子请求预算用完（正常，下次 cron 继续）：' : 'sync 失败：') + e.message)));
  },

  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname.replace(/\/+$/, '') || '/';
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });

    if (req.method !== 'GET') {                     /* 写接口：都要口令 */
      const auth = req.headers.get('Authorization') || '';
      if (!env.PUBLISH_TOKEN || auth !== 'Bearer ' + env.PUBLISH_TOKEN) return jsonOut({ error: 'unauthorized' }, 401);
      if (path === '/publish/page.html' && req.method === 'PUT') {
        const body = await req.text();
        await env.WL.put('page', body);
        await env.WL.put('pageAt', new Date().toISOString());
        return jsonOut({ ok: true, bytes: body.length });
      }
      if (path === '/publish/data.json' && req.method === 'PUT') {
        const body = await req.text();
        await env.WL.put('data', body);
        await env.WL.put('localData', body);    /* 留一份本地口径（含原始提交时区）给云端采集做参照 */
        try {                                   /* 顺带刷新摘要、hash 与时间线快照，页面读 /stage.json /timeline.json 才对得上 */
          const data = JSON.parse(body);
          const st = stageOf(data);
          if (!st.generatedAt) st.generatedAt = new Date().toISOString();
          await env.WL.put('stage', JSON.stringify(st));
          await env.WL.put('dataHash', await sha1(body));
          await putTimeline(env, data);
        } catch (e) {
          console.log('publish/data.json 刷新摘要失败：' + e.message);
        }
        return jsonOut({ ok: true });
      }
      if (path === '/classify') {                 /* AI 逐条判定提交类型（?batches=6 每次最多几批，?reset=1 全部重判） */
        try {
          return jsonOut(await classifyKinds(env,
            parseInt(url.searchParams.get('batches') || '', 10) || 6,
            url.searchParams.get('reset') === '1'));
        } catch (e) {
          return jsonOut({ ok: false, error: e.message }, 500);
        }
      }
      if (path === '/sync') {
        const budget = { left: parseInt(url.searchParams.get('budget') || '', 10) || 40 };
        try {
          return jsonOut(await sync(env, url.searchParams.get('force') === '1', budget));
        } catch (e) {
          if (e.budget) {
            return jsonOut({
              ok: true, partial: true,
              note: '子请求预算用完，缓存已推进；再 POST 一次继续（补齐前不会覆盖 KV 里的旧数据）',
            });
          }
          return jsonOut({ ok: false, error: e.message }, 500);
        }
      }
      return jsonOut({ error: 'not found' }, 404);
    }

    if (path === '/stage.json') {
      const v = await env.WL.get('stage');
      return jsonOut(v ? JSON.parse(v) : { empty: true });
    }
    if (path === '/data.json') {
      const v = await env.WL.get('data');
      return jsonOut(v ? JSON.parse(v) : { empty: true });
    }
    if (path === '/timeline.json') {               /* 时间线快照：?since=<ISO> 只回比它新的（页面按自家构建时间取增量） */
      const raw = await env.WL.get('timeline', 'json');
      if (!raw) return jsonOut({ empty: true });
      const since = url.searchParams.get('since') || '';
      const t0 = since ? Date.parse(since) : NaN;
      if (isNaN(t0)) return jsonOut(raw);
      const items = (raw.items || []).filter((x) => {
        const t = Date.parse(x.date || '');
        return isNaN(t) ? true : t > t0;           /* 时间认不出来的那几条也带上，宁多勿漏 */
      });
      return jsonOut({ at: raw.at, generatedAt: raw.generatedAt, mine: raw.mine,
                       newer: items.length, items: items.slice(0, 50) });
    }
    if (path === '/classify.json') {
      const v = await env.WL.get('kinds');
      return jsonOut(v ? JSON.parse(v) : { empty: true });
    }
    if (path === '/page.html') {
      const v = await env.WL.get('page');
      if (!v) return textOut('还没发布过页面：本地跑 `powershell -File _daily_update.ps1 -Publish`，或让 Actions 上传。', 'text/plain', 404);
      const at = (await env.WL.get('pageAt')) || '';
      return new Response(v, {
        headers: Object.assign({ 'Content-Type': 'text/html; charset=utf-8', 'X-Worklog-Page-At': at }, CORS),
      });
    }
    if (path === '/') {
      const stage = await env.WL.get('stage');
      const at = stage ? (JSON.parse(stage).generatedAt || '') : '（还没抓过，等 cron 或 POST /sync）';
      return textOut('DelicateDuck582 工作日志数据服务\n'
        + '最近一次采集：' + at + '\n'
        + 'GET /stage.json  轻量摘要（页面打开时拉）\n'
        + 'GET /data.json   完整数据\n'
        + 'GET /timeline.json   时间线快照（?since=<ISO> 取增量，页面打开时用它补新提交）\n'
        + 'GET /classify.json  每条提交的 AI 类型判定（时间线「提交类型」吃这一份）\n'
        + 'GET /page.html   最近发布的整页\n'
        + 'PUT /publish/page.html  上传整页（需 PUBLISH_TOKEN）\n'
        + 'POST /classify          给还没判过的提交补类型判定（需 PUBLISH_TOKEN，可加 ?batches=6&reset=1）\n'
        + 'POST /sync              立刻抓一次（需 PUBLISH_TOKEN，可加 ?budget=40&force=1；\n'
        + '                        免费版单次只有 50 个子请求，预算用完不会写数据，再 call 一次继续）\n');
    }
    return jsonOut({ error: 'not found' }, 404);
  },
};
