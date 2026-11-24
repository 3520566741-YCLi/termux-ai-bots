/* 资讯雷达 · 演示数据（?demo=1 时启用，完全在浏览器里，不发请求）
   自查用：?demo=1&mock=empty 全部为空；&mock=error 全部出错；&mock=slow 每个请求慢 3 秒。 */
'use strict';
(function (R) {
  const mode = new URLSearchParams(location.search).get('mock') || '';
  const now = () => Date.now() / 1000;
  const T = now();
  let nextId = 2000;

  const raw = [
    [5, 'A', 'nvidia', '英伟达官方新闻稿', 'stock', 0, ['NVDA'], ['英伟达'], 3, 'NVIDIA Announces $100 Billion Share Repurchase Program', '英伟达董事会批准新增 1000 亿美元股票回购授权，规模创公司纪录，盘后股价涨约 4%。', 3, 1],
    [4, 'B', 'wallstcn', '华尔街见闻快讯', 'stock', 0, ['TSLA'], ['特斯拉'], 11, '特斯拉第四季度交付 51.2 万辆，超出市场预期 12%', '特斯拉 Q4 交付量明显好于分析师预期，Model Y 新款贡献最大，盘前涨 6%。', 2, 1],
    [4, 'C', 'tg', 'Telegram 公开频道', 'crypto', 1, ['BTC'], ['比特币'], 18, '据知情人士：某头部交易所下周将上线比特币现货 ETF 期权', '社交渠道爆料，暂无官方或主流媒体确认，注意甄别。', 0, 0],
    [3, 'B', 'jin10', '金十数据', 'mixed', 0, [], [], 26, '美联储理事沃勒：12 月降息仍是选项，但取决于通胀数据', '鸽派理事表态，市场 12 月降息概率升至 62%。', 1, 0],
    [2, 'B', 'coindesk', 'CoinDesk', 'crypto', 0, ['BTC'], ['比特币'], 34, 'Bitcoin Tops $98,000 as ETF Inflows Continue', '', 0, 0],
    [5, 'A', 'sec', 'SEC 公告', 'crypto', 0, ['ETH'], ['以太坊'], 52, 'SEC 批准以太坊现货 ETF 质押功能', '美国 SEC 批准多只以太坊现货 ETF 开启质押，被视为重大利好，ETH 短线拉升 9%。', 4, 1],
    [3, 'B', 'reuters', '路透社', 'stock', 0, ['TSM', 'NVDA'], ['英伟达'], 75, '台积电称 CoWoS 产能明年再翻倍，主要客户为英伟达', '先进封装产能扩张，缓解 AI 芯片供给瓶颈。', 1, 0],
    [1, 'B', 'jin10', '金十数据', 'mixed', 0, [], [], 90, '某地今日发布暖冬消费券活动', '', 0, 0],
    [4, 'B', 'bloomberg', '彭博', 'stock', 0, ['TSLA'], ['特斯拉'], 140, '马斯克：Robotaxi 将于明年在 5 个城市上线', '马斯克在股东会上给出 Robotaxi 时间表，较此前更激进。', 3, 1],
    [null, 'B', 'wallstcn', '华尔街见闻快讯', 'stock', 0, [], [], 1, '苹果据悉将于明年春季发布新款 iPhone SE', '', 0, 0],
    [3, 'C', 'x', 'X（推特）', 'crypto', 1, ['ETH'], ['以太坊'], 200, '传闻：以太坊基金会再次出售 ETH', '链上数据显示疑似基金会地址转出 3 万枚，尚未证实。', 0, 0],
    [2, 'B', 'cls', '财联社', 'stock', 0, ['NVDA'], ['英伟达'], 260, '英伟达股价盘中涨 2.1%，报 168 美元', '', 0, 0],
  ];
  let items = raw.map((r, i) => ({
    id: 1000 - i, score: r[0], tier: r[1], source: r[2], source_label: r[3], market: r[4], rumor: r[5],
    tickers: r[6], matched: r[7], published: T - r[8] * 60, fetched: T - r[8] * 60 + 8, title: r[9], summary: r[10],
    confirms: r[11], pushed: r[12] ? T - r[8] * 60 + 12 : null, url: 'https://example.com/news/' + (1000 - i),
    reason: r[12] ? '首次报道，A/B 级来源已确认' : '', late_minutes: i === 8 ? 7 : 0, notion_url: i === 1 ? 'https://www.notion.so/demo' : '',
  }));
  // 更多旧消息，用于翻页
  for (let i = 0; i < 40; i++) {
    const b = raw[i % raw.length];
    items.push({ ...items[i % raw.length], id: 980 - i, published: T - (300 + i * 25) * 60, fetched: T - (300 + i * 25) * 60, pushed: null, title: b[9] + '（较早）', notion_url: '' });
  }

  const status = {
    now: T, dry_run: false,
    engine: { ok: true, heartbeat_age: 8 },
    sources: [
      { name: 'wallstcn', label: '华尔街见闻快讯', tier: 'B', enabled: true, ok_ago: 15, error: '', fails: 0, count: 1234 },
      { name: 'jin10', label: '金十数据', tier: 'B', enabled: true, ok_ago: 22, error: '', fails: 0, count: 2210 },
      { name: 'nvidia', label: '英伟达官方新闻稿', tier: 'A', enabled: true, ok_ago: 210, error: '', fails: 0, count: 12 },
      { name: 'sec', label: 'SEC 公告', tier: 'A', enabled: true, ok_ago: 95, error: '', fails: 0, count: 88 },
      { name: 'coindesk', label: 'CoinDesk', tier: 'B', enabled: true, ok_ago: 40, error: '', fails: 0, count: 640 },
      { name: 'tg', label: 'Telegram 公开频道', tier: 'C', enabled: true, ok_ago: 1900, error: 'http 429', fails: 3, count: 455 },
      { name: 'x', label: 'X（推特）', tier: 'C', enabled: false, ok_ago: null, error: '', fails: 0, count: 0 },
    ],
    usage: { today_tokens: 123456, cost_cny: 0.42, by_kind: { score: { n: 31, in: 98000, out: 12000 }, verify: { n: 6, in: 9000, out: 800 }, chat: { n: 2, in: 3000, out: 600 } } },
    settings: { push_threshold: 4, push_unwatched_min: 4, max_push_per_hour: 12, mute_until: 0, channels: ['ntfy', 'webpush'], digest_enabled: true, digest_slots: ['07:30', '12:30', '21:00'] },
    watch: [
      { id: 1, name: '英伟达', kind: 'stock', symbols: ['NVDA'], keywords: ['英伟达', 'NVIDIA', '黄仁勋'] },
      { id: 2, name: '特斯拉', kind: 'stock', symbols: ['TSLA'], keywords: ['特斯拉', 'Tesla', '马斯克'] },
      { id: 3, name: '比特币', kind: 'crypto', symbols: ['BTC'], keywords: ['比特币', 'Bitcoin'] },
      { id: 4, name: '以太坊', kind: 'crypto', symbols: ['ETH'], keywords: ['以太坊', 'Ethereum'] },
    ],
    ntfy: { subscribe_url: 'https://ntfy.sh/radar-demo-7f3k2' },
    webpush: { available: true, public_key: 'BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U' },
    notion: { configured: true, title: '资讯雷达存档' },
    tg: { configured: false, bot: '', chat_title: '' },
    today: { items: 3456, pushed: 3 },
  };

  const digests = [
    { id: 7, title: '11月24日 午报', created: T - 2 * 3600, n_items: 86, notion_url: '' },
    { id: 6, title: '11月24日 早报', created: T - 7 * 3600, n_items: 142, notion_url: 'https://www.notion.so/demo' },
    { id: 5, title: '11月23日 晚报', created: T - 16 * 3600, n_items: 210, notion_url: '' },
  ];
  function digestFull(d) {
    const refs = {};
    items.slice(0, 7).forEach(it => { refs[it.id] = { id: it.id, title: it.title, url: it.url, source_label: it.source_label, tier: it.tier, score: it.score, rumor: it.rumor, published: it.published }; });
    return { ...d, data: {
      headline: '英伟达千亿回购点燃科技股，以太坊 ETF 质押获批，加密市场全线走强。',
      watch: [
        { name: '英伟达', trend: 'up', text: '官宣 1000 亿美元回购，盘后涨 4%；台积电 CoWoS 产能扩张进一步缓解供给瓶颈。', ids: [1000, 994] },
        { name: '特斯拉', trend: 'up', text: 'Q4 交付超预期 12%，马斯克给出更激进的 Robotaxi 时间表。', ids: [999, 992] },
        { name: '比特币', trend: 'mixed', text: '站上 9.8 万美元后震荡；ETF 期权上线传闻未获证实。', ids: [998, 996] },
        { name: '以太坊', trend: 'up', text: 'SEC 批准现货 ETF 质押，短线拉升 9%；基金会抛售传闻待确认。', ids: [995, 990] },
      ],
      top: [
        { id: 1000, title: '英伟达宣布 1000 亿美元股票回购', text: '规模创公司纪录，体现管理层对现金流的信心，利好短期股价。' },
        { id: 995, title: 'SEC 批准以太坊现货 ETF 质押', text: 'ETF 可获得质押收益，机构配置吸引力上升。' },
      ],
      rumors: [{ id: 998, text: '某头部交易所将上线比特币现货 ETF 期权', check: '等交易所官网公告或彭博、路透报道。' }],
      next: ['今晚 21:30 美国 PCE 数据', '英伟达盘后是否继续上涨', '以太坊 ETF 首日质押规模'],
      script: ['大家好，这是今天的午报。', '英伟达宣布一千亿美元股票回购，盘后上涨百分之四。', '以太坊现货 ETF 质押获批，以太坊短线拉升百分之九。', '比特币站上九万八千美元后震荡。', '今晚关注美国 PCE 数据。'],
    }, refs };
  }

  let chat = [
    { role: 'assistant', text: '你好，我是资讯雷达。可以直接告诉我想关注什么、想怎么推送，比如“加上苹果”“静音两小时”“只推 5 分的”。', ts: T - 3600 },
    { role: 'user', text: '把门槛调到 4 分', ts: T - 3500 },
    { role: 'assistant', text: '好的，已把推送门槛设为 4 分：只有“重要”和“极重大”的消息才会推到手机。', ts: T - 3490, changes: ['推送门槛：4 分'] },
  ];

  const wait = ms => new Promise(r => setTimeout(r, mode === 'slow' ? ms + 3000 : ms));
  const fail = msg => { const e = new R.api.ApiError(msg || '演示：模拟出错', 500); throw e; };
  const clone = x => JSON.parse(JSON.stringify(x));

  async function handle(method, path, body) {
    const u = new URL(path, location.href);
    const p = u.pathname, q = u.searchParams;
    await wait(/draft|chat$|generate|notion|config/.test(p) && method === 'POST' ? 1400 : 350 + Math.random() * 250);
    if (mode === 'error') { if (p === '/api/feed' || p === '/api/status') throw new R.api.ApiError('连不上雷达', 0, true); fail(); }
    const empty = mode === 'empty';

    if (method === 'GET' && p === '/api/status') {
      const s = clone(status); s.now = now();
      if (empty) { s.watch = []; s.today = { items: 0, pushed: 0 }; }
      return s;
    }
    if (method === 'GET' && p === '/api/feed') {
      if (empty) return { items: [], next_before: null };
      const min = +q.get('min') || 0, before = +q.get('before') || Infinity, limit = Math.min(+q.get('limit') || 30, 100);
      const kw = (q.get('q') || '').toLowerCase();
      let list = items.filter(it => it.id < before && (it.score || 0) >= min);
      if (q.get('watched') === '1') list = list.filter(it => it.matched.length);
      if (q.get('pushed') === '1') list = list.filter(it => it.pushed);
      if (q.get('market')) list = list.filter(it => it.market === q.get('market'));
      if (kw) list = list.filter(it => (it.title + it.summary + it.tickers.join(' ') + it.matched.join(' ')).toLowerCase().includes(kw));
      const page = list.slice(0, limit);
      return { items: clone(page), next_before: list.length > limit ? page[page.length - 1].id : null };
    }
    let m;
    if (method === 'GET' && (m = p.match(/^\/api\/item\/(\d+)$/))) {
      const it = items.find(x => x.id === +m[1]);
      if (!it) { const e = new R.api.ApiError('这条消息已经不在了', 404); throw e; }
      const dups = it.confirms ? [
        { title: it.title.replace(/^/, '【快讯】'), source_label: '财联社', url: 'https://example.com/a', tier: 'B', published: it.published + 60 },
        { title: 'Reuters: ' + it.title, source_label: '路透社', url: 'https://example.com/b', tier: 'B', published: it.published + 180 },
        { title: it.title, source_label: '彭博', url: '', tier: 'B', published: it.published + 300 },
      ].slice(0, it.confirms) : [];
      return { item: clone(it), dups };
    }
    if (method === 'POST' && p === '/api/settings') {
      const s = status.settings;
      for (const k of ['push_threshold', 'push_unwatched_min', 'max_push_per_hour', 'digest_enabled', 'digest_slots', 'channels']) if (k in body) s[k] = body[k];
      if ('mute_minutes' in body) s.mute_until = body.mute_minutes ? now() + body.mute_minutes * 60 : 0;
      return { settings: clone(s) };
    }
    if (method === 'POST' && p === '/api/watch') {
      if (!body.name || !String(body.name).trim()) fail('名字不能为空');
      const ex = status.watch.find(w => w.name === body.name);
      if (ex) { ex.symbols = [...new Set([...ex.symbols, ...(body.symbols || [])])]; ex.keywords = [...new Set([...ex.keywords, ...(body.keywords || [])])]; ex.kind = body.kind || ex.kind; }
      else status.watch.push({ id: nextId++, name: body.name.trim(), kind: body.kind || 'stock', symbols: (body.symbols || []).map(s => s.toUpperCase()), keywords: body.keywords || [] });
      return { watch: clone(status.watch) };
    }
    if (method === 'POST' && p === '/api/watch/remove') {
      status.watch = status.watch.filter(w => w.name !== body.name);
      return { watch: clone(status.watch) };
    }
    if (method === 'GET' && p === '/api/digest/latest') return empty ? {} : digestFull(digests[0]);
    if (method === 'GET' && p === '/api/digests') return { items: empty ? [] : clone(digests) };
    if (method === 'GET' && (m = p.match(/^\/api\/digest\/(\d+)$/))) {
      const d = digests.find(x => x.id === +m[1]); if (!d) fail('找不到这期日报'); return digestFull(d);
    }
    if (method === 'POST' && p === '/api/digest/generate') {
      await wait(1800);
      const d = { id: digests[0].id + 1, title: '11月24日 临时快报', created: now(), n_items: 37, notion_url: '' };
      digests.unshift(d); return digestFull(d);
    }
    if (method === 'GET' && p === '/api/chat') return { messages: empty ? [] : clone(chat) };
    if (method === 'POST' && p === '/api/chat/clear') { chat = []; return { ok: true }; }
    if (method === 'POST' && p === '/api/chat') {
      const text = String(body.text || '');
      chat.push({ role: 'user', text, ts: now() });
      let reply = '收到。目前演示模式下我只会模拟回答。', changes = [];
      const add = text.match(/(?:加上|关注|添加)\s*([一-龥A-Za-z]+)/);
      if (add) {
        const name = add[1];
        if (!status.watch.find(w => w.name === name)) status.watch.push({ id: nextId++, name, kind: 'stock', symbols: [], keywords: [name] });
        reply = `已加入关注：${name}。之后和它有关的重要消息会优先推给你。`; changes.push('加入关注：' + name);
      } else if (/静音/.test(text)) {
        status.settings.mute_until = now() + 7200; reply = '好的，两小时内不推送，之后自动恢复。'; changes.push('静音到 ' + R.hm(now() + 7200));
      } else if (/英伟达|NVDA/i.test(text)) {
        reply = '英伟达今天最重要的消息：官宣 1000 亿美元回购（A 级来源，5 分），盘后涨约 4%。另有台积电扩产消息（3 分）。';
      }
      chat.push({ role: 'assistant', text: reply, ts: now(), changes });
      return { reply, changes };
    }
    if (method === 'POST' && p === '/api/draft') {
      const it = items.find(x => x.id === body.item_id) || items[0];
      const style = body.style || 'full';
      const head = body.digest_id ? '【今日要闻讨论】' : `【${(it.tickers[0] || '快讯')}】${it.title}`;
      const text = style === 'brief'
        ? `${head}\n${it.summary || it.title}\n大家怎么看？`
        : style === 'debate'
          ? `${head}\n\n看多：${it.summary || '消息面偏利好。'}\n看空：利好是否已提前反映在股价里？短线追高风险不小。\n\n你站哪边？说说理由 👇`
          : `${head}\n\n📌 发生了什么：${it.summary || it.title}\n📈 可能影响：短线情绪偏积极，关注明天开盘是否放量。\n❓ 讨论：这是长期利好还是一次性刺激？\n\n来源：${it.source_label}${body.extra ? '\n（补充：' + body.extra + '）' : ''}`;
      return { text };
    }
    if (method === 'POST' && p === '/api/tg/send') {
      if (!status.tg.configured) { const e = new R.api.ApiError('还没连接 Telegram 机器人，先去“设置”里填 Token 并选择群。', 400); throw e; }
      return { ok: true };
    }
    if (method === 'POST' && p === '/api/notion/save') {
      if (!status.notion.configured) { const e = new R.api.ApiError('还没连接 Notion，先去“设置”里填写。', 400); throw e; }
      const url = 'https://www.notion.so/demo-' + Math.random().toString(36).slice(2, 8);
      if (body.item_id) { const it = items.find(x => x.id === body.item_id); if (it) it.notion_url = url; }
      if (body.digest_id) { const d = digests.find(x => x.id === body.digest_id); if (d) d.notion_url = url; }
      return { url };
    }
    if (method === 'POST' && p === '/api/push/subscribe') return { ok: true };
    if (method === 'POST' && p === '/api/push/unsubscribe') return { ok: true };
    if (method === 'POST' && p === '/api/push/test') return { result: Object.fromEntries(status.settings.channels.map(c => [c, true])) };
    if (method === 'POST' && p === '/api/config/notion') {
      if (!body.db) fail('请填数据库链接或 ID');
      status.notion = { configured: true, title: '资讯雷达存档' };
      return { ok: true, title: '资讯雷达存档', columns: ['标题', '类型', '标的', '分数', '来源', '链接'] };
    }
    if (method === 'POST' && p === '/api/config/tg') {
      if (!/^\d+:[\w-]{20,}$/.test(body.token || '')) { const e = new R.api.ApiError('Token 格式不对，应类似 123456:ABC-DEF…', 400); throw e; }
      status.tg.configured = true; status.tg.bot = '@radar_demo_bot'; return { bot: '@radar_demo_bot' };
    }
    if (method === 'GET' && p === '/api/config/tg/groups') return { groups: [{ id: '-1001234567890', title: '投资讨论群' }, { id: '-1009876543210', title: '币圈小分队' }] };
    if (method === 'POST' && p === '/api/config/tg/chat') { status.tg.chat_title = body.title; return { ok: true }; }
    if (method === 'POST' && p === '/api/config/clear') {
      if (body.what === 'notion') status.notion = { configured: false, title: '' };
      if (body.what === 'tg') status.tg = { configured: false, bot: '', chat_title: '' };
      return { ok: true };
    }
    if (method === 'POST' && p === '/api/login') return { ok: true };
    fail('演示模式没有这个接口：' + p);
  }

  // 演示：每 40 秒冒出一条新消息，方便看“新消息”提示
  if (mode !== 'empty' && mode !== 'error') {
    setInterval(() => {
      const base = raw[Math.floor(Math.random() * 7)];
      const top = items[0].id + 1;
      items.unshift({ ...items[0], id: top, score: base[0] || 3, title: '【新】' + base[9], summary: base[10], published: now(), fetched: now(), pushed: null, notion_url: '' });
    }, 40000);
  }

  R.mock = { handle, mode };
})(window.R);
