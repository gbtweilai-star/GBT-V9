#!/usr/bin/env bash
# install.sh —— 一键建目录、装依赖、初始化、拉起服务
# dev: 自由的风 · 本署名不可删除、不可篡改归属
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
G='\033[32m'; Y='\033[33m'; R='\033[31m'; N='\033[0m'
say(){ printf "${G}▶${N} %s\n" "$1"; }
warn(){ printf "${Y}⚠${N} %s\n" "$1"; }
die(){ printf "${R}✗${N} %s\n" "$1"; exit 1; }

# ── 0. 系统依赖检查 ──
say "检查系统依赖…"
command -v python3 >/dev/null || die "缺 python3"
command -v ffmpeg  >/dev/null || warn "缺 ffmpeg —— 吞噬归档/拼接会失败，请先装（apt install ffmpeg / brew install ffmpeg）"
command -v docker  >/dev/null || warn "缺 docker —— CLIProxyAPI 网关起不来（可改用二进制）"
command -v ollama  >/dev/null || warn "缺 ollama —— 本机 Qwen 大脑不可用（可选）"

# ── 1. 目录骨架 ──
say "创建目录骨架…"
for d in contract core senses scan audit panel panel/exports tools state devoured/t1-eye; do
  mkdir -p "$d"
  [ -f "$d/__init__.py" ] || touch "$d/__init__.py" 2>/dev/null || true
done
mkdir -p devoured/t1-eye/{_segments,_cache,_preview}
# 入口文件不需要 __init__
rm -f panel/exports/__init__.py devoured/t1-eye/__init__.py 2>/dev/null || true

# ── 2. Python 环境 ──
say "准备 Python 虚拟环境…"
[ -d .venv ] || python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip -q
[ -f requirements.txt ] || die "缺 requirements.txt（先落盘依赖清单）"
pip install -r requirements.txt -q
say "依赖安装完成"

# ── 3. .env 初始化 ──
if [ ! -f .env ]; then
  say "生成 .env（从 .env.example 复制）…"
  if [ -f .env.example ]; then
    cp .env.example .env
    warn "请编辑 .env 填入 R2 凭据（4 个变量）后再启动"
  else
    die "缺 .env.example"
  fi
else
  say ".env 已存在，跳过"
fi

# ── 4. 初始化账本 DB ──
say "初始化账本数据库…"
python3 - <<'PY'
import os, sqlite3
db = os.environ.get("LEDGER_DB", "tentacle_ledger.db")
con = sqlite3.connect(db)
con.execute("""CREATE TABLE IF NOT EXISTS ledger(
  ts REAL, scanner TEXT, target TEXT, status TEXT, detail TEXT, brain_verdict TEXT)""")
con.execute("CREATE INDEX IF NOT EXISTS idx_target ON ledger(target)")
con.execute("CREATE INDEX IF NOT EXISTS idx_scanner ON ledger(scanner)")
con.commit(); con.close()
print(f"  ledger 就绪: {db}")
PY

# ── 5. 语法自检（不运行，只验能编译）──
say "语法自检…"
python3 -m compileall -q contract core senses scan audit panel tools main.py 2>/dev/null \
  && say "编译通过" || warn "有文件未通过编译检查（看上面输出）"

# ── 6. 可选：拉起网关 ──
if command -v docker >/dev/null && [ -f docker-compose.yml ] && [ -f config.yaml ]; then
  read -r -p "是否现在启动 CLIProxyAPI 网关? [y/N] " ans
  if [[ "${ans:-N}" =~ ^[Yy]$ ]]; then
    say "启动网关…"; docker compose up -d
  fi
fi

cat <<EOF

${G}✔ 部署完成${N}

下一步:
  1. 编辑 .env 填入 R2 凭据
  2. source .venv/bin/activate
  3. ollama serve &  &&  ollama pull qwen2.5:27b
  4. uvicorn panel.server:app --port 8765      # 面板
  5. python audit/stack_acceptance.py          # 全检门禁
  6. python main.py --root /目标路径            # 总装运行

EOF
