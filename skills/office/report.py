# skills/office/report.py —— office.report@1
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 定位: GBT 自有报告能力; Univer headless = 可替换的表格引擎
# 诚实点: 无 Pro 转换后端时, 不假装能导 xlsx
def spec(self):
    return {
        "inputs": {
            "bundle":     {"type": "object", "required": True,
                           "help": "规范化的 findings / coverage / summary"},
            "formats":    {"type": "array", "required": True,
                           "values": ["markdown", "csv", "univer_snapshot", "xlsx"]},
            "output_key": {"type": "string", "required": True,
                           "help": "受管产物目录中的相对名称"},
        },
        "outputs": {
            "artifacts":           {"type": "array"},
            "unsupported_formats": {"type": "array"},
            "trace_id":            {"type": "string"},
        },
        "idempotent": False,
        "risk": "high",   # 产物写盘 → 本地 gate 授权
    }
