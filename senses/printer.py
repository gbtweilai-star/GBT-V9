# senses/printer.py —— 打印复原：无损视频 / 帧包 / 联络表
# dev: 自由的风 · 吞噬能配套 · 吞下去的，原样吐还给用户
#
# 说明：本模块是回放端（senses.playback.Player）的"打印"门面：
#   视频  → print_video（无损 copy / 可播 MP4）
#   帧包  → print_frames（逐帧 PNG 原样导出）
#   联络表 → print_contact_sheet（一页概览）
import os
import zipfile
from pathlib import Path

from senses.playback import Player, Segment


class Printer:
    def __init__(self, player: Player) -> None:
        self.player = player

    # 一段 → 用户可看的成片
    def video(self, segs, out="restored.mp4", lossless=False) -> dict:
        path, n = self.player.print_video(segs, out=out, lossless=lossless)
        return {"path": path, "segments": n, "bytes": Path(path).stat().st_size}

    # 帧包：逐帧原样 → 再打成一个 zip（便于分发）
    def frame_pack(self, segs, outdir="restored_frames", zip_out=None) -> dict:
        outdir, n = self.player.print_frames(segs, outdir=outdir)
        files = sorted(Path(outdir).glob("*.png"))
        total = sum(f.stat().st_size for f in files)
        if zip_out:
            with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_STORED) as z:
                for f in files:
                    z.write(f, arcname=f.name)
        return {"dir": str(outdir), "files": len(files), "frames": n,
                "bytes": total, "zip": str(zip_out) if zip_out else ""}

    def sheet(self, segs, out="contact_sheet.png", cols=8) -> dict:
        path = self.player.print_contact_sheet(segs, out=out, cols=cols)
        return {"path": path, "bytes": Path(path).stat().st_size}

    # 一键：一段 → 三种打印全给
    def print_all(self, seg: Segment, outdir: str | os.PathLike = "panel/exports") -> dict:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        return {
            "video": self.video([seg], out=str(outdir / f"{seg.seg_id}.mp4")),
            "frames": self.frame_pack([seg], outdir=str(outdir / f"{seg.seg_id}_frames")),
            "sheet": self.sheet([seg], out=str(outdir / f"{seg.seg_id}_sheet.png")),
        }


__all__ = ["Printer"]
