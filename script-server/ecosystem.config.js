/**
 * 独立执行器（script-server）PM2 配置 —— 用于「只装执行器」的新设备。
 *
 * 与仓库根 ecosystem.config.js 的区别：
 *   - 只包含 script-server 一个 app（新设备不需要 backend/worker/Redis/数据库）
 *   - 自带 env 加载：读取「本目录 .env」（.env 不入库，权限自行收紧）
 *   - WF_SCRIPTS_DIR 默认 = 本目录的上级 scripts/；可用 .env 覆盖
 *
 * 用法：
 *   pm2 start ecosystem.config.js --only script-server && pm2 save
 *   或直接双击 start-script-server.bat（推荐，带健康检查与错误提示）
 */
const fs = require("fs");
const path = require("path");

function loadEnvFile(file) {
  const env = {};
  try {
    const text = fs.readFileSync(file, "utf8");
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith("#")) continue;
      const eq = line.indexOf("=");
      if (eq <= 0) continue;
      const key = line.slice(0, eq).trim();
      let value = line.slice(eq + 1).trim();
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }
      env[key] = value;
    }
  } catch {
    // 没有 .env 时只用下面的显式 env（至少要在 PM2 外部注入 WF_SCRIPT_TOKEN）
  }
  return env;
}

const dotenv = loadEnvFile(path.join(__dirname, ".env"));
const defaultScriptsDir = path.join(__dirname, "..", "scripts");

module.exports = {
  apps: [
    {
      name: "script-server",
      script: "python",
      cwd: __dirname,
      args: "-m uvicorn script_server:app --host 0.0.0.0 --port 9000 --workers 1",
      env: {
        ...dotenv,
        WF_SCRIPTS_DIR: dotenv.WF_SCRIPTS_DIR || defaultScriptsDir,
        // 只修服务自身 stdio 编码（Windows 中文/emoji 日志不乱码）；
        // 不要用 PYTHONUTF8=1，那会改变业务脚本 open() 的默认编码。
        PYTHONIOENCODING: "utf-8",
      },
      restart_delay: 3000,
      max_restarts: 10,
      min_uptime: "10s",
      // 停/重启时杀整个进程树：脚本子进程不能变孤儿
      treekill: true,
      kill_timeout: 10000,
      error_file: path.join(__dirname, "logs", "script-server-error.log"),
      out_file: path.join(__dirname, "logs", "script-server-out.log"),
      merge_logs: true,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
    },
  ],
};
