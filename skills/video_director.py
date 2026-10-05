# skills/video_director.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 编排: 选题 → 脚本/分镜 → 素材 → 时间线 → UI 执行 → 吞噬能质检 → 确认 → 导出
class VideoDirector:
    STEPS = ["video.script", "video.assets", "video.edit", "video.transition",
             "video.color", "video.effects", "video.subtitles", "video.audio",
             "video.qa", "video.export"]

    def __init__(self, registry, queue, brain, ledger):
        self.reg, self.queue, self.brain, self.ledger = registry, queue, brain, ledger

    async def make(self, topic, *, style, platform="douyin"):
        ctx = await self.reg.context()                 # 注入 touch/devour/asr 等
        # 并行但不互相踩: 素材下载等 IO 走队列, 编辑类必须串行(剪映单实例)
        script = await self.reg.get("video.script").run(ctx, project_id=topic,
                                                         topic=topic, style=style, platform=platform)
        # 编辑阶段: GPU/单实例互斥, 交队列排队
        async with self.queue.exclusive("jianying_ui"):
            for step in self.STEPS[2:-1]:
                job = await self.queue.enqueue(f"{step}:{topic}", payload={"script": script})
                res = await self.reg.get(step).run(ctx, project_id=topic, **job.payload)
                await self.ledger.record(step, topic, res)
                if not res.get("ok"):
                    return await self.brain.replan(topic, failed_step=step, reason=res)
                if step == "video.qa" and not res.get("verified"):
                    # 画质/字幕/音画同步没过 → 回大脑等指令, 不硬导出
                    return await self.brain.ask(topic, gate="video_qa", findings=res)
        exp = await self.reg.get("video.export").run(ctx, project_id=topic,
                                                      **self._export_preset(platform))
        return exp
