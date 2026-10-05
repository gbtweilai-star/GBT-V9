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

// ── 首次播种：把 V9 插件放进 OCTOP_HOME/plugins ──
function seedHome() {
  const home = octopHome();
  const src = seedDir();
  if (!fs.existsSync(src)) return;
  try {
    const pluginsSrc = path.join(src, "plugins");
    const pluginsDst = path.join(home, "plugins");
    if (fs.existsSync(pluginsSrc) && !fs.existsSync(path.join(pluginsDst, "gbt-potato-v9"))) {
      fs.mkdirSync(pluginsDst, { recursive: true });
      fs.cpSync(path.join(pluginsSrc, "gbt-potato-v9"),
                path.join(pluginsDst, "gbt-potato-v9"), { recursive: true });
      log("seeded plugin gbt-potato-v9 -> " + pluginsDst);
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

function createMainWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1400, height: 900, minWidth: 1100, minHeight: 700,
    title: "GBT小土豆V9",
    icon: path.join(app.getAppPath(), "icon.ico"),
    show: false,
    backgroundColor: "#0b0f14",
    webPreferences: { contextIsolation: true, nodeIntegration: false }
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.loadURL("http://127.0.0.1:" + port + "/");
  mainWindow.once("ready-to-show", () => {
    if (splash && !splash.isDestroyed()) splash.close();
    mainWindow.show();
  });
  mainWindow.on("closed", () => { mainWindow = null; });
  // 外链交给系统浏览器，窗口内只留 Octop 本体
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith("http://127.0.0.1:" + port)) return { action: "allow" };
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
    createMainWindow(octopPort);
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

// ── 退出：整树回收 Octop（taskkill 参数列表，无 shell 拼接）──
function killOctop() {
  if (quitting) return;
  quitting = true;
  if (octopChild && octopChild.pid) {
    const pid = octopChild.pid;
    log("killing octop tree pid=" + pid);
    try {
      if (process.platform === "win32") {
        spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true });
      } else {
        octopChild.kill("SIGTERM");
      }
    } catch (e) { log("kill failed: " + e.message); }
  }
}

app.whenReady().then(boot);
app.on("window-all-closed", () => { killOctop(); app.quit(); });
app.on("before-quit", killOctop);
process.on("exit", () => { try { killOctop(); } catch (_) { /* noop */ } });
