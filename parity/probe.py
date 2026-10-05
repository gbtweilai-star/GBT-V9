# parity/probe.py —— 五类真读数断言
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 测试配置只认 PARITY_DATABASE_URL / 临时 SQLite / PARITY_R2_* ,
#       绝不回退生产配置; 证据必须回读 + SHA-256 校验; 清理幂等且失败即升级

from __future__ import annotations
import asyncio, hashlib, json, uuid
from pathlib import Path
from typing import Any


class ProbeError(Exception): pass
class TransientProbeError(ProbeError): pass
class AssertFailed(Exception): pass
class EvidenceInvalid(Exception): pass
class NotRegistered(Exception): pass
class SpecMismatch(Exception): pass


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def _at_path(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def _matches(actual: Any, expected: dict) -> bool:
    return all(_at_path(actual, k) == v for k, v in expected.items())


async def _call(fn, *args, **kwargs):
    """适配器可同步可异步，统一在这里收敛。"""
    result = fn(*args, **kwargs)
    return await result if hasattr(result, "__await__") else result


async def _save_evidence(ctx, token: str, label: str, content: bytes) -> dict:
    if not isinstance(content, bytes) or not content:
        raise EvidenceInvalid("证据为空或不是 bytes")

    root = Path(ctx.evidence_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = (root / f"{token}-{label}.evidence").resolve()
    if root not in path.parents:                       # 路径越界防护
        raise EvidenceInvalid("证据路径越界")

    path.write_bytes(content)
    reread = path.read_bytes()                         # 真回读再算哈希
    digest = hashlib.sha256(reread).hexdigest()
    if digest != hashlib.sha256(content).hexdigest():
        raise EvidenceInvalid("证据回读哈希不一致")

    return {"path": str(path), "bytes": len(reread), "sha256": digest}


async def _run_one(a: dict, ctx, token: str) -> dict:
    kind = a.get("kind")
    observed, evidence = {}, []

    # 隔离守卫：SQLite 必须落在 scratch；PG 必须 parity_* 库；R2 必须 parity/<run-id>/
    await _call(ctx.isolation.assert_safe, kind, token)

    primary = None
    try:
        # ── ① 账本行：真写 → 新连接读回 ──
        if kind == "ledger_row":
            await _call(ctx.operations[a["operation"]], token, a)   # 调真实能力写入
            rows = await _call(ctx.ledger.read_back_rows,
                               table=a["table"], probe_token=token)

            minimum = int(a.get("min_count", 1))
            if len(rows) < minimum:
                raise AssertFailed(f"账本行数 {len(rows)} < {minimum}")
            expected = a.get("row_equals", {})
            if expected and not any(_matches(r, expected) for r in rows):
                raise AssertFailed("读回行字段不符合预期")

            observed = {"backend": ctx.ledger.backend, "rows": rows}
            evidence.append(await _save_evidence(ctx, token, kind, _json_bytes(observed)))

        # ── ② 产物：真有文件 → 读大小 + 核 SHA-256 ──
        elif kind == "artifact":
            result = await _call(ctx.operations[a["operation"]], token, a)
            ref = (result or {}).get("artifact_ref")
            if not ref:
                raise AssertFailed("能力未返回 artifact_ref")

            await _call(ctx.artifacts.assert_ephemeral_ref, ref, token)
            blob = await _call(ctx.artifacts.read_back, ref)        # 本地或 R2 GET
            if len(blob) < int(a.get("min_bytes", 1)):
                raise AssertFailed("产物缺失或尺寸不足")
            digest = hashlib.sha256(blob).hexdigest()
            if a.get("sha256") and digest != a["sha256"]:
                raise AssertFailed("产物 SHA-256 与预期不符")

            observed = {"ref": str(ref), "bytes": len(blob), "sha256": digest}
            evidence.append(await _save_evidence(ctx, token, kind, blob))

        # ── ③ HTTP：真打 ASGI 应用（不打任意 URL）──
        elif kind == "api_response":
            import httpx
            app = await _call(ctx.app_factory, a["app"], token)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://parity.test") as client:
                response = await client.request(
                    a.get("method", "GET"), a["path"],
                    json=a.get("json"), params=a.get("params"))

            try:
                body = response.json()
            except ValueError:
                body = None

            observed = {"status": response.status_code, "body": body}
            if response.status_code != int(a.get("status", 200)):
                raise AssertFailed(f"HTTP 状态码不符：{response.status_code}")
            if a.get("json_equals") and not _matches(body, a["json_equals"]):
                raise AssertFailed("HTTP JSON 响应字段不符")

            evidence.append(await _save_evidence(
                ctx, token, kind, response.content or _json_bytes(observed)))

        # ── ④ 审计 gap：数量必须精确匹配 ──
        elif kind == "audit_gap":
            result = await _call(ctx.operations[a["operation"]], token, a)
            trace_id = (result or {}).get("trace_id", token)
            gaps = await _call(ctx.ledger.read_gaps, trace_id=trace_id)
            expected = int(a.get("expected", 1))
            if len(gaps) != expected:
                raise AssertFailed(f"gap 数量 {len(gaps)} != {expected}")

            observed = {"trace_id": trace_id, "gaps": gaps}
            evidence.append(await _save_evidence(ctx, token, kind, _json_bytes(observed)))

        # ── ⑤ 原生探针：注册表真解析 + spec 合法 + probe 带真读数 ──
        elif kind == "native_probe":
            skill = ctx.registry.get(a["native_skill"])
            if skill is None:
                raise NotRegistered(a["native_skill"])

            spec = await _call(skill.spec)
            if (spec.get("name") != a["native_skill"]
                    or spec.get("version") != a.get("version", spec.get("version"))
                    or not isinstance(spec.get("inputs"), dict)
                    or not isinstance(spec.get("outputs"), dict)):
                raise SpecMismatch("skill spec 名称、版本或 schema 不符")

            probe = await _call(skill.probe)
            if not isinstance(probe, dict) or probe.get("passed") is not True:
                raise AssertFailed("native probe 未通过")
            if not probe.get("observed") or not probe.get("evidence"):
                raise EvidenceInvalid("native probe 必须提供真读数和证据")   # 禁 bool 蒙混

            observed = {"spec": spec, **(probe["observed"] or {})}
            evidence.append(await _save_evidence(
                ctx, token, kind, _json_bytes(probe["evidence"])))

        else:
            raise ProbeError(f"未知断言类型: {kind}")

        return {"passed": True, "observed": observed, "evidence": evidence, "reason": None}

    except BaseException as e:
        primary = e
        raise
    finally:
        # 清理幂等；证据文件保留给 CI 下载
        try:
            await asyncio.shield(_call(ctx.cleanup, kind, token, observed))
        except asyncio.CancelledError:
            raise
        except Exception:
            if primary is None:                 # 只有"主线已成功"才因清理失败升级
                raise ProbeError("cleanup_failed")


async def run_parity_probe(assertion: dict, ctx, *, run_id: str | None = None,
                           timeout_s: float = 20, retries: int = 1) -> dict:
    token = f"{run_id or uuid.uuid4().hex}-{uuid.uuid4().hex[:8]}"
    last = None

    for attempt in range(retries + 1):
        try:
            return await asyncio.wait_for(_run_one(assertion, ctx, token),
                                          timeout=timeout_s)
        except NotRegistered as e:
            last = ("not_registered", str(e))
        except SpecMismatch as e:
            last = ("spec_mismatch", str(e))
        except AssertFailed as e:
            last = ("assert_failed", str(e))
        except EvidenceInvalid as e:
            last = ("evidence_invalid", str(e))
        except (TransientProbeError, asyncio.TimeoutError) as e:
            last = ("probe_error", f"暂时失败/超时: {e}")
            if attempt < retries:
                await asyncio.sleep(0.1 * (2 ** attempt))     # 只退避瞬时错误
                continue
        except Exception as e:
            last = ("probe_error", f"{type(e).__name__}: {e}")
        break

    category, message = last or ("probe_error", "未知错误")
    return {"passed": False,
            "observed": {"kind": assertion.get("kind"), "token": token},
            "evidence": [],
            "reason": {"category": category, "message": message}}
