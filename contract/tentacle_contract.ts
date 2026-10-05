// ─── 能力枚举：每根触手声明自己会什么 ───
export type Capability = "capture" | "observe" | "act" | "subscribe";

// ─── 权限声明：最小化原则，删/发/转账必须 confirm ───
export type Risk = "read" | "write" | "destructive";

// ─── 契约本体：连接线两端的"协议" ───
export interface TentacleContract {
  id: string;                       // 触手唯一标识
  version: string;                  // 契约版本，向后兼容检查
  capabilities: Capability[];
  llm?: {                           // 每根触手可配自己的 LLM
    model: string;                  // e.g. "gpt-4o-mini" / "qwen-vl"
    prompt?: string;                // 触手专属提示词
    maxTokensPerMin?: number;       // 成本闸门
  };
  permissions: {
    apps: string[];                 // 允许操作的进程/包名，[] = 只读
    risk: Risk;                     // 允许的最高风险级
  };
  limits: {
    frameRate: number;              // 最高抽帧率 fps
    timeoutMs: number;              // 单动作超时
    maxConcurrent: number;          // 并发上限
  };
  hooks: HookSpec[];                // 这根触手关心的"钩子"
}

// ─── 钩子：过滤没用的，只钩有用的 ───
export interface HookSpec {
  type: "region_diff" | "ocr_keyword" | "window_event" | "schedule";
  params: Record<string, unknown>;  // e.g. { region: [x,y,w,h], threshold: 0.05 }
  action: "capture_frame" | "call_llm" | "notify";  // 命中后才触发的昂贵操作
  cooldownMs: number;               // 防抖，防风暴
}

// ─── 采集数据：统一帧格式 ───
export interface Frame {
  seq: number;
  ts: number;
  image: Buffer;                    // PNG/JPEG
  source: { app: string; window: string; region?: [number, number, number, number] };
}

// ─── 执行指令 + 结果验证：闭环的关键 ───
export interface ActCommand {
  method: "api" | "dom" | "axtree" | "coords";   // 优先级从左到右，坐标是兜底
  target: string;
  payload?: Record<string, unknown>;
  verify: {                         // 执行后必须回读验证
    expect: "element_exists" | "pixel_match" | "state_equals";
    probe: Record<string, unknown>;
    retries: number;
  };
}

export interface ActResult {
  ok: boolean;
  verified: boolean;                // verify 通过才算真成功
  error?: string;
  screenshot?: Buffer;              // 失败时留证，供回放
}

// ─── 触手运行时接口：所有适配器实现这四个方法 ───
export interface TentacleRuntime {
  capture(source: Frame["source"]): Promise<Frame>;
  observe(query: Record<string, unknown>): Promise<Record<string, unknown>>;
  act(cmd: ActCommand): Promise<ActResult>;
  subscribe(hook: HookSpec, cb: (f: Frame) => void): () => void;  // 返回取消函数
}
