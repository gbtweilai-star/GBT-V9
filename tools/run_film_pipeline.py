import sys, json, time
from pathlib import Path
ROOT = Path(r"C:\Users\ADMIN\Desktop\GBT小土豆V9")
sys.path.insert(0, str(ROOT))
from core import film_studio as F

SCRIPT = ("你知道吗，一集短剧的开头只有三秒。第一秒给冲突，第二秒给悬念，第三秒给画面。"
          "写剧本时先写冲突，再写悬念，最后才写画面。拍不出来不要紧，先把这三行写下来。")
t0 = time.time()
res = F.run_pipeline(SCRIPT, title="三秒开头怎么写的", out="多镜头成片.mp4")
res["总耗时秒"] = round(time.time() - t0, 1)
out = ROOT / "state" / "film_run.json"
out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print("ok:", res.get("ok"), "| trace:", res.get("trace"), "| 总耗时:", res["总耗时秒"], "秒")
print("成片:", json.dumps(res.get("成片"), ensure_ascii=False)[:400])
