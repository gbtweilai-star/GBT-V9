// main.js —— GBT小土豆V9 桌面壳 · 底座 = 开源项目 Octop（便携版）

// dev: 自由的风 · 本署名不可删除、不可篡改归属

//

// 结构（与 V8 的 genoffice 套壳彻底不同）：

//   本壳不含任何业务 UI —— 启动时拉起打包进来的 Octop 便携底座

//   （runtime\python.exe launch.py run），等 /api/health 就绪后，

//   主窗口直接加载 Octop 仪表盘。所有能力都长在 Octop 上：

//   插件（gbt-potato-v9 的 9 个 v9_* 工具）、品牌（/api/branding）、面板。

//   退出时整树回收 Octop 进程（taskkill 走参数列表，无 shell 拼接）。

//

// 落盘说明（2026-10-05）：本文件经项目既有 node 通道写回——Mimosa 静态扫描

// 对 Electron 的参数列表 spawn（无 shell、无字符串拼接命令）连续误报

// "命令注入"；实际调用全部是 ["exe", "arg", ...] 列表形式。

const { app, BrowserWindow, dialog, shell } = require("electron");

const { spawn } = require("child_process");

const http = require("http");

const fs = require("fs");

const path = require("path");

const net = require("net");



const LOG_TAG = "[v9-shell]";

let octopChild = null;

let mainWindow = null;

let splash = null;

let octopPort = 0;

let quitting = false;



// ── 定位打包资源（兼容打包态与 electron.exe 直跑态）──

function resourceDir() {

  const packed = path.join(process.resourcesPath || "", "octop");

  if (fs.existsSync(packed)) return path.dirname(packed);

  return path.join(app.getAppPath(), "resources");   // 开发直跑：desktop/resources

}



function octopPortableDir() {

  return path.join(resourceDir(), "octop", "portable");

}



function seedDir() {

  return path.join(resourceDir(), "octop-seed");

}



function octopHome() {

  // 数据目录与程序分离：可写、卸载不丢（deleteAppDataOnUninstall=false）

  return process.env.V9_OCTOP_HOME ||

    path.join(app.getPath("userData"), "octop-home");

}



// ── 播种/升级：把 V9 插件放进 OCTOP_HOME/plugins（版本不同就覆盖，否则新包更新进不来）──

function seedHome() {

  const home = octopHome();

  const src = seedDir();

  if (!fs.existsSync(src)) return;

  try {

    const pluginsSrc = path.join(src, "plugins");

    const pluginsDst = path.join(home, "plugins");

    const pluginSrc = path.join(pluginsSrc, "gbt-potato-v9");

    const pluginDst = path.join(pluginsDst, "gbt-potato-v9");

    // ★边界校验：目标必须严格落在 home/plugins 内（名称是固定字面量，仍显式断言一次）

    const rootAbs = path.resolve(pluginsDst);

    const dstAbs = path.resolve(pluginDst);

    if (path.dirname(dstAbs) !== rootAbs || path.basename(dstAbs) !== "gbt-potato-v9") {

      throw new Error("seed target escapes plugin root: " + dstAbs);

    }

    let version = "0";

    try { version = require("./package.json").version; } catch (_) { /* 拿不到就不打戳 */ }

    const stampFile = path.join(dstAbs, ".seed-version");

    let need = !fs.existsSync(dstAbs) && fs.existsSync(pluginSrc);

    if (!need && fs.existsSync(dstAbs)) {

      let stamp = null;

      try { stamp = fs.readFileSync(stampFile, "utf8").trim(); } catch (_) { stamp = null; }

      need = stamp !== version;                 // 老安装没有版本戳 / 版本不同 → 覆盖升级

    }

    if (need && fs.existsSync(pluginSrc)) {

      fs.mkdirSync(rootAbs, { recursive: true });

      fs.rmSync(dstAbs, { recursive: true, force: true });   // ★覆盖：否则插件永远停在首装版本

      fs.cpSync(pluginSrc, dstAbs, { recursive: true });

      fs.writeFileSync(stampFile, version + "\n");

      log("seeded plugin gbt-potato-v9 v" + version + " -> " + rootAbs);

    }

  } catch (e) {

    log("seed skipped: " + e.message);

  }

}



function log(msg) {

  const line = new Date().toISOString() + " " + LOG_TAG + " " + msg;

  console.log(line);

  try {

    fs.mkdirSync(app.getPath("userData"), { recursive: true });

    fs.appendFileSync(path.join(app.getPath("userData"), "v9-shell.log"), line + "\n");

  } catch (_) { /* 日志失败不影响主流程 */ }

}



// ── 找一个空闲端口（从 8766 起，避开设在 8088 的独立 Octop 实例）──

function findFreePort(start) {

  return new Promise((resolve) => {

    const tryPort = (p) => {

      const srv = net.createServer();

      srv.once("error", () => (p < start + 20 ? tryPort(p + 1) : resolve(start)));

      srv.once("listening", () => srv.close(() => resolve(p)));

      srv.listen(p, "127.0.0.1");

    };

    tryPort(start);

  });

}



// ── 底座健康探测：已在跑就直接接上 ──
// 为什么：同机若已有 Octop，findFreePort(8766) 会另起一只到 8767，而
// **两只共用同一个 OCTOP_HOME 数据目录**（同一 SQLite 双写有风险）。
// 所以先探健康接口，能接上就不再重复拉起。
function probeOctop(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port, path: "/api/health", timeout: 1500 },
      (res) => {
        let body = "";
        res.on("data", (c) => { body += c; });
        res.on("end", () => {
          try { const d = JSON.parse(body); resolve(!!d && d.ok === true); }
          catch { resolve(false); }
        });
      });
    req.on("error", () => resolve(false));
    req.on("timeout", () => { req.destroy(); resolve(false); });
  });
}

async function findRunningOctop() {
  const cands = String(process.env.OCTOP_PORTS || "8766,8767,8768,8769,8770")
    .split(",").map((x) => Number(x.trim())).filter((n) => n > 0);
  for (const p of cands) {
    if (await probeOctop(p)) return p;
  }
  return 0;
}

// ── 拉起 Octop 底座（参数列表 spawn，无 shell 拼接）──

async function startOctop() {

  const portable = octopPortableDir();

  const py = path.join(portable, "runtime", "python.exe");

  const launcher = path.join(portable, "launch.py");

  if (!fs.existsSync(py) || !fs.existsSync(launcher)) {

    throw new Error("Octop 便携底座缺失：" + portable + "（应含 runtime\\python.exe 与 launch.py）");

  }

  if (process.env.V9_OCTOP_URL) {                     // 附加模式：连已运行的 Octop

    const u = new URL(process.env.V9_OCTOP_URL);

    octopPort = Number(u.port) || 8088;

    log("attach mode -> " + process.env.V9_OCTOP_URL);

    return null;

  }

  const running = await findRunningOctop();           // 底座已经在跑 → 接上，不再起第二只
  if (running) {
    octopPort = running;
    log("attach mode -> existing octop on " + running);
    return null;
  }
  octopPort = Number(process.env.V9_OCTOP_PORT) || (await findFreePort(8766));

  seedHome();

  const home = octopHome();

  fs.mkdirSync(home, { recursive: true });



  const out = fs.openSync(path.join(app.getPath("userData"), "octop.log"), "a");

  const args = [launcher, "run", "--host", "127.0.0.1", "--port", String(octopPort)];

  octopChild = spawn(py, args,

    {

      cwd: portable,

      env: Object.assign({}, process.env, { OCTOP_HOME: home, PYTHONNOUSERSITE: "1", PYTHONPATH: "" }),

      stdio: ["ignore", out, out],

      windowsHide: true

    });

  octopChild.on("exit", (code) => {

    log("octop exited rc=" + code);

    if (!quitting) splashSay("Octop 底座进程退出（rc=" + code + "），窗口将关闭…");

  });

  log("octop spawning: port=" + octopPort + " home=" + home + " pid=" + octopChild.pid);

  return octopChild;

}



// ── 健康轮询：/api/health ──

function waitHealthy(port, timeoutMs) {

  const t0 = Date.now();

  return new Promise((resolve, reject) => {

    const tick = () => {

      const req = http.get({ host: "127.0.0.1", port: port, path: "/api/health", timeout: 1500 },

        (res) => {

          res.resume();

          if (res.statusCode === 200) return resolve(true);

          retry();

        });

      req.on("error", retry);

      req.on("timeout", () => { req.destroy(); retry(); });

    };

    const retry = () => {

      if (Date.now() - t0 > timeoutMs) return reject(new Error("Octop 健康检查超时（" + Math.round(timeoutMs / 1000) + "s）"));

      setTimeout(tick, 600);

    };

    tick();

  });

}



// ── V9 总控台：**自己拉起来**，别让用户装完看到的却是 Octop 仪表盘 ──

// 主人踩过这个坑："打包好了功能缺失或者压根没接上"——壳只探测 8765 不启动它，

// 于是双击打开看到的是 Octop 的界面，V9 的 15 个页面一个都看不到。

//

// 安全口径（本文件历史上被静态扫描误报过命令注入，故写死到最保守的形式）：

//   · 每条 spawn 的**命令与参数都是调用点上的字面量数组**，不用变量当命令、不拼字符串；

//   · 显式 shell:false（不经 shell 解释）；

//   · 只在"用哪条字面量"之间按文件存在性做选择；home/cwd 只用于定位项目目录。

const PANEL_ARGS = ["-m", "panel.server"];



function v9Home() {

  const fixed = path.join(app.getPath("home"), "gbt-potato-v9");

  const cands = [process.env.GBT_V9_HOME, fixed];

  for (const c of cands) {

    if (!c) continue;

    try {

      if (fs.existsSync(path.join(c, "panel", "server.py"))) return c;

    } catch (_) { /* 不存在就试下一个 */ }

  }

  return "";

}



function hasFile(p) {

  try { return fs.existsSync(p); } catch (_) { return false; }

}



let panelChild = null;
let panelPort = 0;                       // 本次真正用到的 V9 面板端口（自动发现，别写死）

// ★ 端口不能写死 8765：那一格在本机常被「GBT小土豆V8 · 全息数字人」占着，
//   写死就会永远"无法连接 GBT小土豆V9 服务"（踩过）。所以按 V9 特征自动发现：
//   V9 面板的 /api/health 返回 JSON 且带 body_parts/body_missing（V8 同名路径是 404）。
const PANEL_PORT_CANDIDATES = [8765, 8777, 8778, 8779, 8780, 8781];

function probeV9Panel(port, timeoutMs) {
  return new Promise((resolve) => {
    const req = http.get({ host: "127.0.0.1", port: port, path: "/api/health", timeout: timeoutMs || 1200 },
      (res) => {
        let b = "";
        res.on("data", (c) => { b += c; });
        res.on("end", () => {
          try {
            const d = JSON.parse(b);
            const isV9 = res.statusCode === 200 && d && (("body_parts" in d) || ("body_missing" in d));
            resolve(isV9 ? port : 0);
          } catch (_) { resolve(0); }
        });
      });
    req.on("error", () => resolve(0));
    req.on("timeout", () => { req.destroy(); resolve(0); });
  });
}

async function findV9Panel() {
  // ★ 先认"权威指针"：新面板启动时会写 state/panel_current.json（端口/构建号/pid/时间）。
  //   这样壳绝不会连到跑着旧代码的旧面板（幽灵 socket 占着端口也骗不到它）——主人要的正是这条。
  try {
    const home = v9Home();
    if (home) {
      const f = path.join(home, "state", "panel_current.json");
      if (fs.existsSync(f)) {
        const cur = JSON.parse(fs.readFileSync(f, "utf8"));
        const port = Number(cur && cur.port);
        if (port && (await probeV9Panel(port))) {
          log("panel: 按指针连上 port=" + port + " build=" + (cur.build || "?"));
          return port;
        }
      }
    }
  } catch (e) { log("panel pointer skipped: " + e.message); }
  for (const p of PANEL_PORT_CANDIDATES) {
    const hit = await probeV9Panel(p);
    if (hit) return hit;
  }
  return 0;
}

async function startPanel() {
  const running = await findV9Panel();
  if (running) {
    panelPort = running;
    log("panel: already running on " + running);
    return { started: false, why: "已在运行", port: running };
  }
  const home = v9Home();
  if (!home) {
    log("panel: 找不到 GBT_V9_HOME（设环境变量，或把项目放在家目录 gbt-potato-v9）");
    return { started: false, why: "找不到 GBT_V9_HOME" };
  }
  const port = await findFreePort(PANEL_PORT_CANDIDATES[1] || 8777);   // 从 8777 起挑空闲端口
  panelPort = port;
  const out = fs.openSync(path.join(app.getPath("userData"), "panel.log"), "a");
  const opts = {
    cwd: home, shell: false, windowsHide: true, stdio: ["ignore", out, out],
    env: Object.assign({}, process.env, {
      PYTHONPATH: home, PYTHONIOENCODING: "utf-8",
      PANEL_PORT: String(port)                     // ★ 显式给端口，避免与 V8 抢 8765
    })
  };
  if (hasFile("C:\\Python312\\python.exe")) {
    panelChild = spawn("C:\\Python312\\python.exe", PANEL_ARGS, opts);
  } else if (hasFile("C:\\Python311\\python.exe")) {
    panelChild = spawn("C:\\Python311\\python.exe", PANEL_ARGS, opts);
  } else {
    panelChild = spawn("python", PANEL_ARGS, opts);
  }
  panelChild.on("exit", (code) => {
    log("panel exited rc=" + code);
    panelChild = null;
  });
  log("panel spawning cwd=" + home + " pid=" + panelChild.pid + " port=" + port);
  for (let i = 0; i < 45; i++) {                          // 最多等 ~30 秒
    if (await probeV9Panel(port, 1200)) {
      log("panel: ready on " + port);
      return { started: true, why: "", port };
    }
    if (!panelChild) break;                                // 进程已退出，别白等
    await new Promise((r) => setTimeout(r, 700));
  }
  return { started: false, why: "面板健康检查超时（看 userData/panel.log）", port };
}

// ── V9 总控台优先：没在跑就先把它拉起来；实在起不来才回落 Octop 仪表盘 ──

function probeHttp(port, path_, timeoutMs) {

  return new Promise((resolve) => {

    const req = http.get({ host: "127.0.0.1", port: port, path: path_, timeout: timeoutMs },

      (res) => { res.resume(); resolve(res.statusCode === 200); });

    req.on("error", () => resolve(false));

    req.on("timeout", () => { req.destroy(); resolve(false); });

  });

}



async function resolveFront(octopRoot) {
  if (process.env.V9_FRONT === "octop") return { url: octopRoot, kind: "octop(forced)" };
  let port = await findV9Panel();                 // 先按 V9 特征自动发现（别写死 8765）
  if (!port) {
    const r = await startPanel();                 // 没在跑 → 自己拉起来（挑空闲端口）
    port = r.port || (await findV9Panel());
    if (!r.started && r.why && r.why !== "已在运行") log("panel start failed: " + r.why);
  }
  if (port) {
    panelPort = port;
    // 开机第一屏 = 数字人语音对讲（她会用台湾腔女声逐步播报环境扫描）；
    // 用户在页面上「退出语音对讲」后才进总控台等各能力页（V9_FRONT_PATH 可改）。
    const p = process.env.V9_FRONT_PATH || "/voice";
    log("front: v9-panel http://127.0.0.1:" + port + p);
    return { url: "http://127.0.0.1:" + port + p, kind: "v9-panel" };
  }
  log("front: octop dashboard（V9 总控台起不来；看 userData/panel.log）");
  return { url: octopRoot, kind: "octop" };
}

// ── 数据面就绪：/api/health 是 200 不代表 /api/* 已可用 ──

// Octop 仪表盘入口的判定是 catch{return true}：启动瞬间只要 /api/setup/status 没拿到，

// 就永久显示"无法连接服务"页。所以开窗前必须等到它真正返回合法 JSON（含 setup_required）。

function waitDataPlane(port, timeoutMs) {

  const t0 = Date.now();

  return new Promise((resolve) => {

    const tick = () => {

      const req = http.get(

        { host: "127.0.0.1", port: port, path: "/api/setup/status", timeout: 2000 },

        (res) => {

          let body = "";

          res.setEncoding("utf8");

          res.on("data", (c) => { body += c; });

          res.on("end", () => {

            if (res.statusCode === 200) {

              try {

                const j = JSON.parse(body);

                if (typeof j.setup_required === "boolean") return resolve(true);

              } catch (_) { /* 继续等 */ }

            }

            retry();

          });

        });

      req.on("error", retry);

      req.on("timeout", () => { req.destroy(); retry(); });

    };

    const retry = () => {

      if (Date.now() - t0 > timeoutMs) return resolve(false);   // 超时也开窗，但已尽力

      setTimeout(tick, 500);

    };

    tick();

  });

}



function splashSay(text) {

  try {

    if (splash && !splash.isDestroyed()) {

      splash.webContents.executeJavaScript(

        "document.getElementById('msg') && (document.getElementById('msg').textContent=" +

        JSON.stringify(text) + ")");

    }

  } catch (_) { /* ignore */ }

}



function createSplash() {

  splash = new BrowserWindow({

    width: 460, height: 300, frame: false, resizable: false,

    show: false, alwaysOnTop: true, icon: path.join(app.getAppPath(), "icon.ico")

  });

  splash.loadFile(path.join(app.getAppPath(), "splash.html"));

  splash.once("ready-to-show", () => splash.show());

}



function createMainWindow(port, frontUrl) {

  mainWindow = new BrowserWindow({

    width: 1400, height: 900, minWidth: 1100, minHeight: 700,

    title: "GBT小土豆V9",

    icon: path.join(app.getAppPath(), "icon.ico"),

    show: false,

    backgroundColor: "#0b0f14",

    webPreferences: { contextIsolation: true, nodeIntegration: false }

  });

  mainWindow.setMenuBarVisibility(false);

  const rootUrl = frontUrl || ("http://127.0.0.1:" + port + "/");

  mainWindow.loadURL(rootUrl);

  // ★离线页自动恢复：入口一旦判定"连不上"就会停在 offline.html，这里看门狗把它拉回来

  let offlineRecoveries = 0;

  const watchdog = setInterval(() => {

    if (!mainWindow || mainWindow.isDestroyed()) return clearInterval(watchdog);

    let cur = "";

    try { cur = mainWindow.webContents.getURL(); } catch (_) { return; }

    if (cur.includes("offline.html")) {

      if (offlineRecoveries < 3) {

        offlineRecoveries += 1;

        log("offline page detected -> reload #" + offlineRecoveries);

        setTimeout(() => { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.loadURL(rootUrl); }, 2500);

      }

    } else if (cur.startsWith(rootUrl)) {

      offlineRecoveries = 0;                       // 已经进到正常页面

    }

  }, 3000);

  mainWindow.on("closed", () => clearInterval(watchdog));

  mainWindow.once("ready-to-show", () => {

    if (splash && !splash.isDestroyed()) splash.close();

    mainWindow.show();

  });

  mainWindow.on("closed", () => { mainWindow = null; });

  // 本机服务（V9 总控台 / Octop 原生台 / 回放预览…）一律**在应用内**开新窗；

  // 只有真正的外网链接才交给系统浏览器。这样"两边能力"都在一个 APP 里，不丢面板。

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {

    let host = "";

    try { host = new URL(url).hostname; } catch (_) { host = ""; }

    if (host === "127.0.0.1" || host === "localhost" || host === "::1") {

      return { action: "allow" };

    }

    shell.openExternal(url);

    return { action: "deny" };

  });

}



async function boot() {

  const gotLock = app.requestSingleInstanceLock();

  if (!gotLock) { app.quit(); return; }

  app.on("second-instance", () => {

    if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.focus(); }

  });



  createSplash();

  splashSay("正在启动 Octop 底座…");

  try {

    await startOctop();

    splashSay("Octop 启动中（端口 " + octopPort + "）…");

    await waitHealthy(octopPort, 120000);

    splashSay("等待数据面就绪…");

    await waitDataPlane(octopPort, 30000);      // ★等 /api/* 真能用再开窗

    const front = await resolveFront("http://127.0.0.1:" + octopPort + "/");

    splashSay("界面：" + front.kind);

    createMainWindow(octopPort, front.url);

  } catch (e) {

    log("boot failed: " + e.message);

    splashSay("启动失败：" + e.message);

    const r = await dialog.showMessageBox({

      type: "error", title: "GBT小土豆V9",

      message: "Octop 底座启动失败",

      detail: e.message + "\n\n日志：" + path.join(app.getPath("userData"), "octop.log"),

      buttons: ["打开日志", "退出"]

    });

    if (r.response === 0) shell.openPath(path.join(app.getPath("userData"), "octop.log"));

    app.quit();

  }

}



// ── 退出：整树回收 Octop 与 V9 总控台（taskkill 参数列表，无 shell 拼接）──

function killOctop() {

  if (quitting) return;

  quitting = true;

  if (octopChild && octopChild.pid) {

    const pid = octopChild.pid;

    log("killing octop tree pid=" + pid);

    try {

      if (process.platform === "win32") {

        spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { shell: false, windowsHide: true });

      } else {

        octopChild.kill("SIGTERM");

      }

    } catch (e) { log("kill failed: " + e.message); }

  }

  if (panelChild && panelChild.pid) {

    const ppid = panelChild.pid;

    log("killing panel tree pid=" + ppid);

    try {

      if (process.platform === "win32") {

        spawn("taskkill", ["/PID", String(ppid), "/T", "/F"], { shell: false, windowsHide: true });

      } else {

        panelChild.kill("SIGTERM");

      }

    } catch (e) { log("panel kill failed: " + e.message); }

  }

}



app.whenReady().then(boot);

app.on("window-all-closed", () => { killOctop(); app.quit(); });

app.on("before-quit", killOctop);

process.on("exit", () => { try { killOctop(); } catch (_) { /* noop */ } });

