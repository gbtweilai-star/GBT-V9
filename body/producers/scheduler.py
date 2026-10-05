# body/producers/scheduler.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import asyncio, logging, os, random, time
from body.producers import devour, scan, queue

log = logging.getLogger("body.producers")
SPECS = {"devour": (devour.PERIOD, devour.collect),
         "queue":  (queue.PERIOD,  queue.collect),
         "scan":   (scan.PERIOD,   scan.collect)}
JITTER = float(os.getenv("BODY_PRODUCER_JITTER", 0.1))
TASKS: set = set()

SQL_WINDOW_CLAIM = """INSERT INTO body_snapshot_windows (domain, window_start)
                      VALUES (?,?) ON CONFLICT (domain, window_start) DO NOTHING"""


async def run_once(app, domain):
    """单域采样：leader 门控 → 限时 → 去重窗口 → 发布。失败【不发布】。"""
    period, collect = SPECS[domain]
    async with recheck_leader(app.state.ledger, key=f"body:producer:{domain}") as who:
        if who is None:
            return {"domain": domain, "skipped": "not_leader"}
        try:
            res = await asyncio.wait_for(collect(app.state.ledger, state=app.state),
                                         timeout=period * 0.8)   # 不阻塞业务
        except asyncio.TimeoutError:
            await app.state.ledger.record_blocked(f"producer_timeout:{domain}",
                                                  {"period": period})
            return {"domain": domain, "error": "timeout"}          # ★不发布 → 自然超期
        except Exception as e:
            log.exception("producer failed: %s", domain)
            await app.state.ledger.record_alert(
                "producer_failed", {"domain": domain, "error": type(e).__name__},
                level="warning", bypass_freeze=True)
            return {"domain": domain, "error": type(e).__name__}   # ★不发布
        return {"domain": domain, "revision": res}


async def snapshot_loop(app, *, jitter=JITTER):
    due = {d: 0.0 for d in SPECS}
    while True:
        now = time.time()
        for domain in SPECS:
            if now < due[domain]:
                continue
            due[domain] = now + SPECS[domain][0] * (1 + random.uniform(-jitter, jitter))
            t = asyncio.create_task(run_once(app, domain))          # 域间互不拖累
            TASKS.add(t)
            t.add_done_callback(TASKS.discard)
        await asyncio.sleep(1)


# lifespan
@app.on_event("startup")
async def _start_producers():
    app.state.producer_task = asyncio.create_task(snapshot_loop(app))

@app.on_event("shutdown")
async def _stop_producers():
    app.state.producer_task.cancel()
    await asyncio.gather(app.state.producer_task, *TASKS, return_exceptions=True)  # 收干净
