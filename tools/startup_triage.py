# tools/startup_triage.py —— 启动自检的「报警闭环」台账（禁"不影响"）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人令（2026-10-09）：「每次启动时自检只要发现故障或者黄色报警，全部都要追踪根因彻底排除
#   加固优化，而不是不管不顾每次都是丢一句"不影响"？这句话也给我彻底清理了。」
#
# 规矩：每条 故障/黄警 都要有 {现象 → 根因 → 处置 → 证据}；缺一项 ⇒ 停在「未闭环」，
#   不许标已处理，也不许用"不影响/忽略即可"糊过去（那些词在 core/ux_doctrine 里已是禁句）。
# 用法：python tools/startup_triage.py            # 打印台账（未闭环优先）
#       python tools/startup_triage.py --check    # 有未闭环就退出码 1（给启动脚本当闸）
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))       # ★必须：否则 import core 会 ModuleNotFoundError（踩过两次）
LEDGER = ROOT / "state" / "selfcheck_triage.jsonl"

SEED = [
    dict(现象="主脑模型可被静默换成被排除厂商（GLM/Kimi）", 级别="黄警",
         根因="主脑的脑子只由 env(GBT_LLM_MODEL…) 决定，没有任何角色归属校验",
         处置="新增 core/llm_roles.py 角色钉死表 + 被排除厂商报警 + 变更台账；/api/llm-roles 可查",
         证据="实测把 GBT_LLM_MODEL 指到 z-ai/glm-5.3 → 立刻报 (主脑, 智谱 GLM)"),
    dict(现象="GLM/Kimi 被登记排除后仍可能被调用", 级别="黄警",
         根因="排除只写在登记表里，路由层没有机械闸",
         处置="run_plugin 接 is_excluded() ⇒ 命中即拒跑并给理由；splice_report 列出被排除的槽",
         证据="实测 code:kimi-k2.7-code#2 → ok=False 被排除=True"),
    dict(现象="仓库无版本历史，东西被改被删无法追责", 级别="黄警",
         根因="仓里从来没有 .git（第一天核查确认）",
         处置="建 git 基线（34,634 文件）+ 指纹台账 tools/inventory_snapshot.py（39,839 文件逐个 sha256）",
         证据="git f7ac6179 已提交；state/inventory/inv-*-基线.json 已落盘"),
    dict(现象="成片大腿上出现折线纹（我先当成『蒙皮破面』，追了两轮）", 级别="故障",
         根因="**我先追错了**。实测三件事：① 零骨骼旋转与抬臂 25° 的右腿特写都干净；② 把未调色的原片同一时间点抽帧一看，那些折线纹本来就在模型上（属资产自带纹理/几何线）；③ 我的调色链（brightness + medium_contrast + 强锐化 + 颗粒）把高光推到过曝（该区域亮度 190~202、过曝 4.66%），于是细纹被衬成撕裂感 —— **放大了它，但没有制造它**。",
         处置="① 调色改 shadow-only lift（白点钉 1.0）+ hqdn3d 降噪 + 轻锐化 3:3:0.35 + 轻颗粒（alls=2）；② 质检加『局部最差过曝』判据（5×5 网格取最差块，阈值 8%）；③ **定论+已修**：纯白材质渲同一位置一条线都没有（局部对比度 1.313）⇒ 线在**贴图**上；按『肤色邻域里的细黑线』抹掉 60,241 像素（base+normal 同步），黑皮衣细节未动，重打包出 rigged-clean.glb 并把管线默认切过去，原版保留可回退。",
         证据="render/诊断-腿-终版.png（同裁切 RAW vs FIXED）· 终检：全局过曝 0.0% / 局部最差 0.0% / 响度 -14.1 / 红灯 0"),
    dict(现象="面板曾出现假数字（可拼接 0）", 级别="故障",
         根因="我留的占位 +0 与 splice() 异步竞态互相覆盖",
         处置="删占位；汇总行单一归属；splice_report 从 48 次开库降到 1 次（1.23s）",
         证据="面板实测 可拼接 48/100 · 开着 48 · 预留 52"),
    dict(现象="%TEMP% 里 5,634 项删不掉", 级别="黄警",
         根因="被运行中进程占用（文件锁），强删会波及在跑的程序",
         处置="如实说明并保留；不为删文件去杀进程（那不是加固，是冒险）",
         证据="清理后可用 77GB → 103.1GB，释放约 26GB；残留项均为占用中"),
    dict(现象="网页 DCC 桥报「请确保本地插件已启用」", 级别="黄警",
         根因="插件虽装进 Blender，但未在 Blender 内启用；且浏览器没开调试口 ⇒ 网页连不上本地服务",
         处置="命令行启用插件并保存偏好；重启 Blender（不带 --factory-startup）⇒ 桥口起来",
         证据="实测新监听口 60600（pid=Blender）；userpref.blend 173,288 字节"),
    dict(现象="自检脚本曾在中文控制台崩溃（GBK 编不了 ✅）", 级别="黄警",
         根因="脚本没重设 stdout 编码，PowerShell 默认 GBK",
         处置="tools/selfcheck.py 顶部加 sys.stdout.reconfigure(utf-8)",
         证据="修复后 自检结论 ✅ 可落盘运行 · 17/17 导入 OK"),
    dict(现象="真执行审计扫出 17 处「默认干跑」+ 7 处「写死的通过」", 级别="黄警",
         根因="tools/audit_real_execution.py 用 AST 扫出来：老件里 run(dry_run=True) / probe_witness(dry_run=True) 这类默认值，把「调了」变成「只说不动」；测试桩里也有直接 return ok=True",
         处置="① 审计器已固化（可复跑）② 我改过的几处（equip/install/plug/dispatch/沙盒四查）已改默认真动手、真检测 ③ 其余老件逐条列在 state/real_execution_audit.json，按「谁默认只说不动」排队修，不假装已清",
         证据="state/real_execution_audit.json（file:line + 理由）· tools/audit_real_execution.py 复跑一致"),
    dict(现象="24 处核心函数零引用（看起来要干活却没人调）", 级别="黄警",
         根因="收紧后的零引用类扫出：body 层 evidence_row/anchor/recheck/witness_runtime/witness_voice、core 层 cloud_runner.record_call、tripo.render_*、pulse_sandbox.run_python、uia_control.send_text 等，全仓无静态引用——同 run_plugged 那一类",
         处置="已逐条登记不藏：① body 证据/锚点/复检三类接进交付闸与每日排查（下一批）② 其余按谁该调它逐条接线或标未接线；未接线的一律不许当已验证",
         证据="state/real_execution_audit.json（无调用方 20-24 条 file:line）· tools/audit_real_execution.py 复跑一致"),
    dict(现象="云插件全线 401 Authentication error（火力全开因此未闭环）", 级别="黄警",
         根因="直连实测：http 401 + code 10000 Authentication error，来源 state/keys.env + wrangler 登录态，账号 82dd88c2…；今日估算已用 34.5 neurons，未撞 4006（不是配额，是凭据被拒）",
         处置="这是【需主人登入/换 token】那一类：① 已让框架如实报 401 与原文，不假装 ② 本地兜底存在但本机内存 0.3-0.5GB 跑不动 0.6B ③ 拿到新 token 写进 state/keys.env 即通，届时交付闸自动转绿",
         证据="core.cloud_plugin.invoke 返回 ok=false/http=401/原文前200 · state/full_power_ledger.jsonl"),
    dict(现象="docker compose demo 未验证（本机 Docker 守护未运行）", 级别="黄警",
         根因="docker-compose.yml 里 demo profile 已在，但本机 docker daemon 连不上（npipe 找不到），无法起容器验证",
         处置="已如实标注「未验证」并挂进本台账；等 Docker 守护起来后跑 docker compose --profile demo up verify 收读数，再转闭环",
         证据="docker info 报 failed to connect to docker API at npipe；docker-compose.yml demo profile 段"),
    dict(现象="能力注册表与代码漂移：model.optimize@1 指向不存在的模块", 级别="黄警",
         根因="skills/__init__.py 的 INFRA_CAPABILITIES 声明 model.optimize@1 → build.model_optimize.pipeline；实测 ModuleNotFoundError: No module named build.model_optimize —— 注册表说有能力，盘上没有件",
         处置="已在能力闭环台账如实记未通 + 精确卡点；要闭环需引入 nvidia-modelopt 工具链（本机无），拿到工具链前保持未闭环，不许把注册表当能力",
         证据="state/capability_loop.jsonl：model.optimize@1 未通 · ModuleNotFoundError"),
    dict(现象="另两项能力未通：diagram@1（无 archify CLI）· voice.io@1（VoiceStudio 3900 不可达）", 级别="黄警",
         根因="外部件缺席：图形 CLI 未装；VoiceStudio 服务未起（本机语音实际走 core.studio.narrate 的 edge-tts，已通）",
         处置="如实标未通 + 卡点；要闭环需装 archify / 起 VoiceStudio，或把能力指向本机已通的等效通道",
         证据="state/capability_loop.jsonl 逐项卡点；verify_capability_loop 要求非通的项必带卡点"),
    dict(现象="3 条能力未验：蒙皮权重 / 全肢体动作件 / 生图生视频成片链", 级别="黄警",
         根因="三条都卡在要外部条件：① 蒙皮权重受制于该资产手与腿几何焊死；② 动作件要重渲；③ 生图生视频要 key 或额度",
         处置="逐条如实标未验并写清卡点；KEY/额度到位或换干净 T-pose 资产后再验，不许假装闭环",
         证据="operation_bindings 里 4 条仍标未验；逐条卡点见台账"),
    dict(现象="数字人资产接管（dh_intake）留痕不全", 级别="黄警",
         根因="写入有回执，但召回未命中时没如实记账，容易被读成接了就没管",
         处置="写入记 id、召回未命中如实记；台账可回放",
         证据="verify_dh_intake：写入 id=m4af7fb00ed9746 通过 / 召回未命中如实记"),
]


def main() -> int:
    check = "--check" in sys.argv
    have = []
    if LEDGER.is_file():
        have = [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]
    merged = {x["现象"]: x for x in SEED}
    for x in have:
        merged[x["现象"]] = x
    rows = list(merged.values())
    from core import ux_doctrine as UX
    t = UX.triage([{k: v for k, v in r.items() if k != "状态"} for r in rows])
    print("== 报警闭环台账（禁「不影响」）==")
    print("  共 %d 条 · 已闭环 %d · 未闭环 %d · 用了禁句 %d" %
          (t["总数"], t["已闭环"], t["未闭环"], len(t["用了禁句"])))
    for r in t["未闭环明细"]:
        print("  ❌ 未闭环 [%s] %s —— 缺 %s" % (r.get("级别", "?"), r["现象"][:44], "、".join(r["缺什么"])))
    for r in t["已闭环明细"][:6]:
        print("  ✅ 已闭环 %s  ← %s" % (r["现象"][:38], str(r.get("证据"))[:50]))
    if check and t["未闭环"]:
        print("\n  启动闸：有 %d 条未闭环 ⇒ 退出码 1（不许带黄警静默启动）" % t["未闭环"
])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
