# tools/verify_music.py —— 配乐合格线机检（响度/真峰值/stems 三轨/BGM 时长/前 8 秒有声音）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
# 口径来源：docs/音乐样板规格.md（A 已抓到出处；B 行业通行值标注待核对）
import argparse, json, re, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent


def sh(args, timeout=600):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def loudness(film: Path) -> dict:
    rc, _, err = sh(["ffmpeg", "-hide_banner", "-i", str(film), "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*input_i[^{}]*\}", err, re.S)
    if not m:
        return {"ok": False, "为什么": "loudnorm 没回读数"}
    d = json.loads(m.group(0))
    return {"ok": True, "integrated_lufs": float(d.get("input_i", "nan")), "true_peak_dbtp": float(d.get("input_tp", "nan")),
            "lra": d.get("input_lra"), "thresh": d.get("input_thresh")}


def first_seconds_db(film: Path, seconds: float = 8.0) -> dict:
    rc, _, err = sh(["ffmpeg", "-hide_banner", "-t", str(seconds), "-i", str(film), "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", err)
    return {"ok": bool(m), "mean_db": float(m.group(1)) if m else None}


def dur(f: Path) -> float:
    rc, out, _ = sh(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(f)])
    try:
        return float(json.loads(out)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--film", required=True)
    ap.add_argument("--stems", default="")
    ap.add_argument("--target-lufs", type=float, default=-14.0)
    ap.add_argument("--tol", type=float, default=2.0)
    a = ap.parse_args()
    film = Path(a.film)
    if not film.is_absolute():
        film = ROOT / film
    FAIL = []

    def check(name, ok, reading):
        print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
        if not ok:
            FAIL.append(name)

    print("== 配乐合格线机检 ==")
    if not film.is_file():
        print("  ❌ 成片不存在:", film)
        return 1
    L = loudness(film)
    if L.get("ok"):
        check("① 响度在目标 ±%.1f LUFS 内（目标 %.1f）" % (a.tol, a.target_lufs),
              abs(L["integrated_lufs"] - a.target_lufs) <= a.tol, "实测 %.2f LUFS" % L["integrated_lufs"])
        check("② 真峰值 ≤ -1 dBTP", L["true_peak_dbtp"] <= -1.0, "实测 %.2f dBTP" % L["true_peak_dbtp"])
    else:
        check("① 响度可读", False, L)
    # ③ stems 三轨
    stems = Path(a.stems) if a.stems else (film.parent / (film.stem + "-stems"))
    need = ("dx", "mx", "fx")
    have = []
    if stems.is_dir():
        for f in stems.iterdir():
            n = f.name.lower()
            if any(k in n for k in need):
                have.append(f.name)
    check("③ stems 三轨齐（DX 对白 / MX 音乐 / FX 音效）", len(have) >= 3 or all(any(k in "".join(have).lower() for k in [k]) for k in need),
          "目录 %s · 命中 %s" % (stems.name, have or "（无 stems 目录）"))
    # ④ BGM 时长覆盖成片
    fd = dur(film)
    mx = None
    if stems.is_dir():
        mx = next((f for f in stems.iterdir() if "mx" in f.name.lower()), None)
    if mx:
        check("④ BGM(MX) 时长 ≥ 成片时长", dur(mx) + 0.05 >= fd, "MX %.2fs vs 成片 %.2fs" % (dur(mx), fd))
    else:
        check("④ BGM(MX) 时长（缺 MX 轨，如实报）", False, "没有 MX 轨 ⇒ 现在是「整轨混音」，未达分轨交付")
    # ⑤ 前 8 秒有声音（钩子不静音）
    fs = first_seconds_db(film)
    check("⑤ 前 8 秒有声音（钩子不静音）", bool(fs.get("ok")) and (fs.get("mean_db") or -99) > -50,
          "前 8 秒均值 %.1f dB" % (fs.get("mean_db") if fs.get("mean_db") is not None else float("nan")))
    print()
    print("成片:", film.name, "· 时长 %.2fs" % fd)
    if FAIL:
        print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
        return 1
    print("结论：✅ 配乐合格线通过（响度/峰值/stems/BGM 时长/钩子有声）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
