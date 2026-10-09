# core/memory/worker.py —— 编码队列：捕捉是即时的，理解在后台慢慢做
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 复用**我们自己的**队列件（media.queue.JobQueue：有租约/心跳/退避/死信），
# 不另造一套。stage="brain.encode"：捕捉时入队，worker 领取后调编码器。
# 没有账本时退到进程内清单（保证"立刻写完"这条路径永远不因缺账本而失败）。
from core.swallow import swallow as _swallow
import threading
import time

STAGE = "brain.encode"
WORKER = "brain-encode"


def enqueue(memory_id: str, *, queue=None, led=None) -> dict:
    """把一个记忆 id 丢进编码队列（尽力而为：入不了队也不影响捕捉已经成功）。"""
    try:
        q = queue
        if q is None and led is not None:
            from media.queue import JobQueue
            q = JobQueue(led)
        if q is None:
            raise RuntimeError("no_queue")
        r = q.enqueue("brain", STAGE, "brain.encode", {"memory_id": memory_id},
                      priority=0, max_attempts=3)
        return {"ok": True, "job": (r or {}).get("job_id") if isinstance(r, dict) else str(r)}
    except Exception as exc:                                   # noqa: BLE001
        _LOCAL.append((memory_id, time.time()))
        return {"ok": True, "local": True, "reason": type(exc).__name__,
                "队列深度": len(_LOCAL)}


_LOCAL: list = []


def drain_local(*, st=None, limit: int = 200) -> dict:
    """把退到本地清单的（以及库里还没编码的）都编码掉。"""
    from core.memory import encoder as E
    st = st or E.S.store()
    ids = [mid for mid, _t in _LOCAL[: max(1, int(limit))]]
    _LOCAL[:] = _LOCAL[len(ids):]
    done = 0
    for mid in ids:
        if E.encode(mid, st=st).get("ok"):
            done += 1
    more = E.encode_pending(limit=max(1, int(limit)), st=st)
    return {"本地清单": len(ids), "编码": done, "库里补编码": more}


def drain_once(*, led=None, queue=None, st=None, limit: int = 50) -> dict:
    """领一次队列并编码（供面板/测试同步调用；也可被后台线程循环调用）。"""
    from core.memory import encoder as E
    st = st or E.S.store()
    got = 0
    q = queue
    if q is None and led is not None:
        try:
            from media.queue import JobQueue
            q = JobQueue(led)
        except Exception:                                      # noqa: BLE001
            q = None
    if q is not None:
        try:
            q.sweep_expired()
        except Exception as e:
            _swallow(__file__, e)
        for _i in range(max(1, int(limit))):
            job = q.claim(WORKER)
            if not job:
                break
            mid = ((job.get("params") or {}).get("memory_id")
                   if isinstance(job, dict) else None)
            if not mid and isinstance(job, dict):
                mid = (job.get("params") or {}).get("memory_id")
            try:
                r = E.encode(str(mid), st=st)
                if r.get("ok"):
                    q.complete(job.get("job_id"), job.get("lease_owner") or WORKER)
                    got += 1
                else:
                    q.fail(job.get("job_id"), job.get("lease_owner") or WORKER,
                           str(r.get("reason") or "encode failed"))
            except Exception as exc:                           # noqa: BLE001
                try:
                    q.fail(job.get("job_id"), job.get("lease_owner") or WORKER,
                           type(exc).__name__)
                except Exception as e:
                    _swallow(__file__, e)
    local = drain_local(st=st, limit=limit)
    return {"队列编码": got, "本地": local, "待编码": E.pending_count(st=st)}


def ensure_done(*, st=None, limit: int = 500) -> dict:
    """把库里所有 pending 都编码掉（面板"理解"按钮/导入后调用）。"""
    from core.memory import encoder as E
    st = st or E.S.store()
    r = E.encode_pending(limit=limit, st=st)
    return {"ok": True, **r, "剩余待编码": E.pending_count(st=st)}


_STOP = threading.Event()


def start_background(*, led=None, interval: float = 20.0, once_limit: int = 50):
    """后台线程：每隔 interval 秒把待编码的清一遍（面板启动时拉起）。"""
    def loop():
        while not _STOP.is_set():
            try:
                drain_once(led=led, limit=once_limit)
            except Exception as e:
                _swallow(__file__, e)
            _STOP.wait(float(interval))
    t = threading.Thread(target=loop, name="brain-encode", daemon=True)
    t.start()
    return t


def stop_background() -> None:
    _STOP.set()


def status(*, led=None, st=None) -> dict:
    from core.memory import encoder as E
    st = st or E.S.store()
    depth = None
    if led is not None:
        try:
            from media.queue import JobQueue
            stats = JobQueue(led).stats() or {}
            depth = (stats.get("depth") or stats) if isinstance(stats, dict) else stats
        except Exception:                                      # noqa: BLE001
            depth = None
    return {"待编码": E.pending_count(st=st), "本地清单": len(_LOCAL),
            "队列深度": depth, "stage": STAGE,
            "口径": "捕捉即时落库；编码走 media.queue（租约/心跳/退避/死信），缺账本退本地清单"}


__all__ = ["STAGE", "enqueue", "drain_once", "drain_local", "ensure_done",
           "start_background", "stop_background", "status"]
