# printer.py —— 把吞下的影像原原本本"打印"还原给用户
import json, subprocess, zipfile
from pathlib import Path
from PIL import Image

class Printer:
    def __init__(self, devour_dir: Path):
        self.dir, self.index = devour_dir, devour_dir / "index.jsonl"

    def frames(self):
        return [json.loads(l) for l in open(self.index)]

    # ── 打印方式一：无损重封装成视频（FFV1无损 或 高码率MP4给用户看）──
    def print_video(self, out="restored.mp4", lossless=False):
        codec = "ffv1" if lossless else "libx264"
        extra = ["-crf", "0"] if lossless else ["-crf", "12", "-pix_fmt", "yuv420p"]
        subprocess.run(["ffmpeg", "-y", "-framerate", "30",
                        "-i", str(self.dir / "f%08d.png"), "-c:v", codec, *extra, out], check=True)
        return out

    # ── 打印方式二：原始帧打包 zip（逐帧原样交付）──
    def print_frames_zip(self, out="frames.zip"):
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:   # STORED=不再压缩，原样封存
            for r in self.frames():
                z.write(self.dir / r["file"], r["file"])
        return out

    # ── 打印方式三：联络表 contact sheet（一页纸概览全部帧）──
    def print_contact_sheet(self, out="contact_sheet.png", cols=8):
        frames = self.frames()
        thumbs = [Image.open(self.dir / r["file"]).resize((160, 90)) for r in frames]
        rows = -(-len(thumbs) // cols)
        sheet = Image.new("RGB", (cols * 160, rows * 90), "black")
        for i, t in enumerate(thumbs):
            sheet.paste(t, ((i % cols) * 160, (i // cols) * 90))
        sheet.save(out)
        return out
