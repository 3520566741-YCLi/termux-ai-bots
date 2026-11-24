# 资讯雷达网页 · 接口约定

后端：`news-radar/radar/web.py`（Python 标准库，`python -m radar web`）。前端：本目录（`news-radar/web/`）的静态文件，后端原样提供。
后端由“做 24 小时私人资讯助手”线程负责；前端由“设计手机网页界面”线程负责。改这份约定前先互相说一声。

## 运行方式

- 后端只监听 `127.0.0.1:8790`，由 `tailscale serve` 转成 `https://dotstudioone.tail8b6ca0.ts.net:8443/`（网页推送和“添加到主屏幕”需要 HTTPS）。
- 静态文件：`GET /` → `web/index.html`；`GET /<文件>` → `web/<文件>`（只允许本目录下的文件，不列目录）。
  `sw.js` 和 `manifest.webmanifest` 也放本目录根，作用域是 `/`。
- 响应头带 CSP：`default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'`。
  **不能用内联 `<script>`，不能引外部 CDN**（脚本放单独的 .js 文件，字体用系统字体 `-apple-system`）。内联 style 属性可以。

## 登录

- 首次用口令链接打开：`GET /login?t=口令` → 设 Cookie（`radar`，HttpOnly，SameSite=Strict，Secure，180 天）→ 303 跳到 `/`。
  口令在 Mini PC 上用 `python -m radar web-url` 查看。
- 也可以在登录页输入口令：`POST /api/login {"token": "..."}` → `{"ok": true}` 并设 Cookie。连续输错 10 次锁 10 分钟。
- 没登录时所有 `/api/*` 返回 **401** `{"error": "..."}`，前端显示登录页。静态文件不需要登录（里面不含数据）。

## 通用规则

- 所有 POST：`Content-Type: application/json`，并带请求头 `X-Radar: 1`（防跨站），否则 403。
- 出错：HTTP 4xx/5xx + `{"error": "给用户看的中文"}`。
- 时间都是 Unix 秒（浮点）。用户在北京时间。
- 文本一律当纯文本显示（`textContent`），不要 `innerHTML` 拼接后端字段。

## 数据结构

**Item（一条资讯）**
```json
{"id": 123, "title": "原标题", "summary": "DeepSeek 一句话中文摘要（可能为空）", "url": "https://…（可能为空）",
 "source": "wallstcn", "source_label": "华尔街见闻快讯", "tier": "A|B|C", "market": "stock|crypto|mixed",
 "score": 4, "rumor": 0, "tickers": ["NVDA"], "matched": ["英伟达"], "published": 1732430000.0, "fetched": 1732430010.0,
 "pushed": 1732430012.0, "confirms": 2, "reason": "推送前检查理由（可能为空）", "late_minutes": 0,
 "notion_url": "已存到 Notion 时的页面链接，否则空字符串",
 "image": "/img/<40位hash>.jpg 或空字符串", "image_w": 900, "image_h": 506,
 "saved": 1732430000.0, "note": "收藏备注", "slim": false}
```
- `image`：后端缓存的配图（同源，需登录 Cookie），宽最多 900；`image_w/h` 可能为 0（没装 Pillow 时），用来预留比例。
  图是异步找的：消息刚出现时可能还没图，过十几秒刷新会有。
- `saved`：收进“收藏”的时间，没收藏是 `null`。
- `slim: true`：没收藏的旧消息（北京时间当天 0 点前抓到的）已被每日清理，只剩标题、来源和原网页链接（summary、image、reason 为空），
  前端显示紧凑行，点击直接打开原网页。收藏的永远完整保留。没收藏的 30 天后整条删除。
- `score` 可能为 `null`（还没打分）。5=极重大（红），4=重要（橙），3=值得一看，2=一般，1=噪音。
- `tier`：A 官方、B 主流媒体、C 社交/传闻。`rumor=1` 显示“传闻”标记。`pushed` 非空表示已推送到手机。

**Digest（一期日报）**
```json
{"id": 7, "title": "11月24日 早报", "created": 1732430000.0, "n_items": 86, "notion_url": "",
 "data": {"headline": "一句话主线",
          "watch": [{"name": "英伟达", "trend": "up|down|mixed|quiet", "text": "两三句", "ids": [123]}],
          "top": [{"id": 123, "title": "标题", "text": "两三句"}],
          "rumors": [{"id": 123, "text": "传闻内容", "check": "怎么确认"}],
          "next": ["接下来盯的事"],
          "script": ["口播稿，一句一个元素（可用 speechSynthesis 朗读，zh-CN）"]},
 "refs": {"123": {"id": 123, "title": "原标题", "url": "…", "source_label": "…", "tier": "B", "score": 4, "rumor": 0, "published": 0}}}
```

## 接口

### 状态与设置
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 总览，见下 |
| POST | `/api/settings` | 改设置，body 里给要改的键（见下），返回 `{"settings": {...}}` |
| POST | `/api/watch` | 加/合并关注 `{"name": "苹果", "kind": "stock|crypto|other", "symbols": ["AAPL"], "keywords": ["Apple", "库克"]}` → `{"watch": [...]}` |
| POST | `/api/watch/remove` | `{"name": "苹果"}` → `{"watch": [...]}` |

`GET /api/status` 返回：
```json
{"now": 0, "dry_run": false,
 "engine": {"ok": true, "heartbeat_age": 12},
 "sources": [{"name": "wallstcn", "label": "华尔街见闻快讯", "tier": "B", "enabled": true, "ok_ago": 15, "error": "", "fails": 0, "count": 1234}],
 "usage": {"today_tokens": 123456, "cost_cny": 0.42, "by_kind": {"score": {"n": 10, "in": 0, "out": 0}}},
 "settings": {"push_threshold": 4, "push_unwatched_min": 4, "max_push_per_hour": 12, "mute_until": 0,
              "channels": ["ntfy", "webpush"], "digest_enabled": true, "digest_slots": ["07:30", "12:30", "21:00"]},
 "watch": [{"id": 1, "name": "英伟达", "kind": "stock", "symbols": ["NVDA"], "keywords": ["黄仁勋"]}],
 "ntfy": {"subscribe_url": "https://ntfy.sh/radar-xxxx"},
 "webpush": {"available": true, "public_key": "base64url"},
 "notion": {"configured": true, "title": "资讯雷达存档"},
 "tg": {"configured": false, "bot": "", "chat_title": ""},
 "today": {"items": 3456, "pushed": 3},
 "memory": {"count": 42, "devices": 1, "pending_devices": 0},
 "saved": {"count": 12}}
```
`POST /api/settings` 可以给：`push_threshold`(1~5)、`push_unwatched_min`(1~5)、`max_push_per_hour`(1~60)、
`mute_minutes`(0=取消静音，最多 1440)、`digest_enabled`(bool)、`digest_slots`(["HH:MM", …] 最多 6 个)、`channels`(["ntfy","webpush"] 子集)。

### 信息流
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/feed?min=3&watched=1&market=stock&q=英伟达&before=123&limit=50` | 参数都可选。`min` 最低分（默认 0），`watched=1` 只看命中关注的，`pushed=1` 只看推送过的，`before` 翻页（取 id 小于它的）。返回 `{"items": [Item], "next_before": 98}`（没有更多时 `null`）。按 id 倒序 |
| GET | `/img/<名字>` | 配图（需登录）。名字只来自 Item.image |
| GET | `/api/item/123` | `{"item": Item, "dups": [{"title": "", "source_label": "", "url": "", "tier": "B", "published": 0}]}`（dups = 去重时合并进来的其他来源报道） |

### 收藏（前端页签叫「收藏」）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/save` | `{"item_id": 123, "note": "可选备注"}` → `{"item": Item}`（重复收藏只更新备注；没图的会马上去找图） |
| POST | `/api/unsave` | `{"item_id": 123}` → `{"ok": true}` |
| GET | `/api/saved?q=&before=&limit=50` | 按收藏时间倒序 `{"items": [Item], "next_before": 1732430000.0}`；`before` 传上一页的 `next_before`（收藏时间） |

真正要长期留的再用 `/api/notion/save {"item_id"}` 存 Notion。`status` 里有 `"saved": {"count": 12}`。

### 日报
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/digest/latest` | 最新一期 Digest；还没有时 `{}` |
| GET | `/api/digest/7` | 指定一期 |
| GET | `/api/digests` | `{"items": [{"id": 7, "title": "", "created": 0, "n_items": 86}]}`（最近 20 期） |
| POST | `/api/digest/generate` | 立即出一期（30~90 秒），返回 Digest |

### 聊天（改关注、改设置、问消息）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/chat` | `{"messages": [{"role": "user|assistant", "text": "", "ts": 0}]}`（最近 60 条，正序） |
| POST | `/api/chat` | `{"text": "帮我加上苹果，静音两小时"}` → `{"reply": "…", "changes": ["加入关注：苹果", "静音到 16:30"]}`（5~30 秒）。`changes` 非空时前端刷新状态 |
| POST | `/api/chat/clear` | 清空聊天记录 |

聊天助手就是 **Vesper**（和 Mac 上 Deck 里的 Vesper 同名同口吻），`changes` 里可能出现“记住了：…”“忘掉了：…”。

### 记忆（Vesper 的长期记忆，手机和 Mac 共用）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/memories` | `{"memories": [{"uid": "32位", "text": "Mitchell 不碰港股", "kind": "投资", "created": 0, "updated": 0, "origin": "phone|mac:xxxx"}], "devices": [{"id": "a1b2c3d4", "name": "YCLideMacBook-Pro", "approved": false, "created": 0, "last_seen": 0}]}` |
| POST | `/api/memories/save` | `{"text": "…", "kind": "可选"}` → `{"memory": {...}}` |
| POST | `/api/memories/update` | `{"uid": "…", "text": "…"}` |
| POST | `/api/memories/delete` | `{"uid": "…"}` |
| POST | `/api/devices/approve` | `{"id": "a1b2c3d4"}` 批准 Mac 的同步申请（`status.memory.pending_devices > 0` 时请在“我的”里醒目提示） |
| POST | `/api/devices/remove` | `{"id": "…"}` 移除设备（撤销同步权限） |

（`/api/mem/pair`、`/api/mem/sync` 是 Mac 同步小程序用的，令牌认证，前端不用管。）

### 讨论文案 / 发到 Telegram 群
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/draft` | `{"item_id": 123}` 或 `{"digest_id": 7}`，可加 `"style": "brief|full|debate"`（默认 full）、`"extra": "补充要求"` → `{"text": "…"}`（5~20 秒） |
| POST | `/api/tg/send` | `{"text": "改好的文案", "item_id": 123}`（item_id 可选）→ `{"ok": true}`。只有用户点按钮才调用。没配置时 400 |

### Notion
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/notion/save` | 三选一：`{"item_id": 123}` / `{"digest_id": 7}` / `{"title": "", "text": "", "url": "", "tickers": []}`（自己写的笔记或链接）→ `{"url": "Notion 页面链接"}` |

### 网页推送（可选，要先“添加到主屏幕”）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/push/subscribe` | `{"subscription": PushSubscription.toJSON()}` → `{"ok": true}` |
| POST | `/api/push/unsubscribe` | `{"endpoint": "…"}` |
| POST | `/api/push/test` | 发一条测试推送到所有已开渠道 → `{"result": {"ntfy": true, "webpush": false}}` |

推送消息 payload（sw.js 收到的）：`{"title": "", "body": "", "url": "原文链接或 /"}`。

### 连接设置（在“设置”页填，Token 不回显）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/config/notion` | `{"token": "ntn_…（留空=不改）", "db": "数据库链接或 ID"}` → `{"ok": true, "title": "数据库名", "columns": ["类型", "标的", …]}`；失败 400 带原因 |
| POST | `/api/config/tg` | `{"token": "123:ABC…"}` → `{"bot": "@机器人用户名"}`（校验通过才保存） |
| GET | `/api/config/tg/groups` | `{"groups": [{"id": "-100…", "title": "群名"}]}`（先把机器人拉进群、在群里发一句话） |
| POST | `/api/config/tg/chat` | `{"id": "-100…", "title": "群名"}` → `{"ok": true}` |
| POST | `/api/config/clear` | `{"what": "notion|tg"}` 删掉对应配置 |

## 页面建议（后端视角，最终由前端定）
- 快讯：信息流（分数色条、传闻/已推送/迟到标记、代码 chip、“另有 N 个来源”），筛选 全部 / 关注 / 已推送 / ≥4 分，30 秒自动刷新。
  点开详情：摘要、原文、其他来源、按钮【讨论文案】【存到 Notion】【打开原文】。
- 日报：最新一期 + 历史，按钮【朗读】【讨论文案】【存到 Notion】【现在出一期】。
- 聊天：和 DeepSeek 对话改关注和设置，`changes` 用小标签显示。
- 聊天：页面名可以直接叫 Vesper。
- 我的：关注列表、推送设置（门槛、静音）、ntfy 订阅地址、网页推送开关、Vesper 的记忆（可删改）和记忆同步设备（批准/移除）、Notion、Telegram 机器人、来源状态、今日用量。
