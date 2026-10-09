import sys, json
from pathlib import Path
ROOT = Path(r"C:\Users\ADMIN\Desktop\GBT小土豆V9")
sys.path.insert(0, str(ROOT))
from core import film_themes as FT
res = FT.produce_all()
for r in res["结果"]:
    print("主题 %-6s ok=%-5s 成片=%s | %ss | %s镜 | %s | 红灯%s | %s LUFS | 耗时%ss"
          % (r["主题"], r["ok"], r.get("成片"), r.get("时长"), r.get("镜数"), r.get("分辨率"),
             r.get("质检红灯"), r.get("响度LUFS"), r.get("耗时秒")))
print("成功 %d/%d" % (res["成功"], res["主题数"]))
