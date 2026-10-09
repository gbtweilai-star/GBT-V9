# 黑客·sec_llm

- **类目**：黑客(安全域)
- **归属**：GBT小土豆V9
- **独立验收器**：`tools/verify_hacker_brain_v9.py`
- **登记状态**：已部署·待钥匙（abliteration 401 已验端点可达）

## 它到底干什么（细节）

黑客大脑问答（由**本件这颗脑子**驱动）

## 怎么调（真入口）

`core.hacker_brain_v9（https://api.abliteration.ai）`

## 输入

自然语言指令（授权范围内）

## 产物 / 落点

分析/结论 + 落账

## 判据（怎么算干完）

有输出 · 不拒答 · 连贯（三判缺一不算通）

## 实测读数 / 备注

门：external_llm（要主人授权）

## 门 / 红线

- 六类现实危险动作（转账·支付·删除·对外发布·隐私载体·不可回滚）**只走主人授权**；
- AI **不许给自己发授权**；本能力若挂 external_llm/filesystem_write 等作用域，需主人在终端授权。

---
_本文由 tools/gen_capability_docs.py 从登记表现算（导出 2026-10-09 18:54）_
