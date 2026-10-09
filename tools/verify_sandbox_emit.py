# tools/verify_sandbox_emit.py —— 牢房与吐口验收（模型关里面，只有触手能吐）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import sandbox_emit as SE   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 牢房与吐口验收 ==")
# 用本地后端跑，验收不烧云额度；本机 ollama 不在时退化为"关起来但无输出"（仍可验结构）
c = SE.Cell(name="verify", tenant="t001")
r = c.confine("验收：只回两个字「收到」", backend="local", model="qwen3:0.6b")
check("牢房能关起来跑（结构与封印生成）", (c.outbox / "reply.txt").is_file() and (c.outbox / "manifest.json").is_file(),
      "cell=%s ok=%s 后端=%s" % (c.cell_id, r.get("ok"), r.get("后端")))

man = json.loads((c.outbox / "manifest.json").read_text(encoding="utf-8"))
check("封印含 sha256/字节/模型", bool(man.get("sha256") and man.get("bytes") is not None), man.get("sha256", "")[:16])
check("反逃逸四查都有读数", set(r["逃逸检查"]) >= {"文件逃逸", "进程逃逸", "出网控制", "stdout 录音"},
      list(r["逃逸检查"]))
check("四查全过（关得住）", r.get("关得住") is True, r.get("关得住"))

check("非触手来吐被拒（'brain'）", c.spit(by="brain").get("ok") is False, c.spit(by="brain").get("reason"))
s1 = c.spit(by="t001")
check("触手能吐且封印校验通过", s1.get("ok") is True and s1.get("sha256") == man.get("sha256"),
      "吐的人 %s · %s 字节" % (s1.get("吐的人"), s1.get("字节")))

# 篡改测试：改了 outbox 里的内容，封印必须对不上 ⇒ 拒吐
rep = c.outbox / "reply.txt"
orig = rep.read_text(encoding="utf-8")
rep.write_text(orig + "（被篡改）", encoding="utf-8")
tampered = c.spit(by="t002")
check("篡改后封印对不上、拒吐", tampered.get("ok") is False and "封印" in (tampered.get("reason") or ""),
      tampered.get("reason"))
rep.write_text(orig, encoding="utf-8")

st = SE.run("status")
check("吐账/牢房账可读", isinstance(st.get("最近"), list) and st.get("最近"),
      [x.get("动作") for x in st["最近"][-3:]])

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 牢房与吐口通过（关得住四查 · 只有触手能吐 · 封印防篡改 · 落账）")
