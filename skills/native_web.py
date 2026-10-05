# skills/native_web.py —— Scrapling 蒸馏成的原生抓取能力
# dev: 自由的风 · 本署名不可删除、不可篡改归属

class WebScrape:
    name, version = "web.scrape", "1.0.0"

    def spec(self):
        return {
            "name": self.name, "version": self.version,
            "inputs": {"type": "object", "required": ["url", "selector"],
                       "properties": {
                           "url":      {"type": "string"},
                           "selector": {"type": "string"},
                           "tier":     {"enum": ["http", "browser", "stealth"],
                                        "default": "http"},
                           "adaptive": {"type": "boolean", "default": False},
                           "auto_save": {"type": "boolean", "default": False},
                           "fields":   {"type": "array", "items": {"type": "string"}},
                           "max_chars": {"type": "integer", "minimum": 1,
                                         "maximum": 20000, "default": 4000}}},
            "outputs": {"type": "object", "properties": {
                "items": {"type": "array"}, "matched": {"type": "boolean"},
                "trace_id": {"type": "string"}}},
            "permissions": ["web.fetch"],
            "side_effects": ["ledger.write", "optional.artifact.write"],
        }

    async def run(self, ctx, inputs):
        # ① SSRF 防护：禁私网/回环/危险 scheme，重定向后**重新校验**
        target = ctx.url_policy.validate_public_http_url(inputs["url"])
        tier = inputs.get("tier", "http")

        # ② 风险闸门：域名授权 + robots + 速率 + 预算；browser/stealth 额外确认
        await ctx.risk_gate.check_web(target, tier=tier,
                                      robots=True, recheck_redirects=True)

        # ③ 域名限速（按 host 令牌桶）
        await ctx.domain_limiter.acquire(target.host)

        tid = ctx.tentacle.id
        page = await ctx.scrapling.fetch(
            tier=tier, url=target.url,
            session=ctx.sessions.for_tentacle(tid))       # 会话按触手隔离

        # ④ 自适应选择：auto_save 存元数据，下次 adaptive 找回
        items, sel_meta = ctx.extract_with_adaptive(
            page, inputs["selector"],
            adaptive=inputs.get("adaptive", False),
            auto_save=inputs.get("auto_save", False),
            fields=inputs.get("fields"))

        # ⑤ 钩子过滤 + 限长（省 token：只让有用的进契约）
        filtered = ctx.hooks.filter_web_results(items)
        result = ctx.clip(filtered, max_chars=inputs["max_chars"])

        await ctx.selector_store.upsert(sel_meta)         # 账本
        await ctx.ledger.record_web_scrape(
            trace_id=ctx.trace_id, tentacle_id=tid, host=target.host, tier=tier,
            result_count=len(result), matched=sel_meta["matched"],
            result_hash=ctx.hash(result))
        return {"items": result, "matched": sel_meta["matched"], "trace_id": ctx.trace_id}
