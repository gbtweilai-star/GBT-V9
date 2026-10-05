# skills/native_office.py —— 对应 Univer 的 headless Facade
class OfficeDocument:
    name, version = "office.document", "1.0.0"
    # inputs: {kind: "sheet|doc|slide", op: "create|read|edit|export", ...}
    # run(): 调 Node headless 运行时(Facade API) → 结构化读写, 不模拟鼠标键盘
    #        产物(store_ref, checksum, version)进账本, 大对象进 R2 只存引用
