#!/usr/bin/env node
// Portable source-development commands. No shell, global Python, or port killing.
import { spawnSync } from "node:child_process";
import { existsSync, realpathSync } from "node:fs";
import { delimiter, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const python = join(root, "backend", ".venv", process.platform === "win32" ? "Scripts" : "bin", process.platform === "win32" ? "python.exe" : "python");
const env = { ...process.env, UV_PYTHON_INSTALL_DIR: join(root, ".toolchains", "python") };
const task = process.argv[2];
const extra = process.argv.slice(3);

function run(command, args, cwd = root) {
  const result = spawnSync(command, args, { cwd, env, stdio: "inherit", shell: false });
  if (result.error) throw new Error(`Cannot launch ${command}: ${result.error.code}. Check the development prerequisites.`);
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function requirePython() {
  if (!existsSync(python)) throw new Error("Run npm run bootstrap first (Node.js and uv are required).");
}

function npm(args) {
  const candidates = [process.env.npm_execpath, join(dirname(process.execPath), "node_modules", "npm", "bin", "npm-cli.js")];
  for (const dir of (process.env.PATH ?? "").split(delimiter)) {
    const path = join(dir, "npm");
    if (existsSync(path)) candidates.push(realpathSync(path));
  }
  const cli = candidates.find((path) => path && path.endsWith("npm-cli.js") && existsSync(path));
  if (!cli) throw new Error("Use npm run for this command, or install Node.js with npm.");
  run(process.execPath, [cli, ...args], join(root, "web"));
}

try {
  switch (task) {
    case "bootstrap":
      run("uv", ["python", "install", "3.13.14"]);
      run("uv", ["sync", "--project", join(root, "backend"), "--locked", "--all-groups"]);
      requirePython();
      run(python, [join(root, "scripts", "patch_tss.py")]);
      if (process.platform === "darwin") run(python, [join(root, "scripts", "prepare_native.py")]);
      break;
    case "build":
      requirePython();
      npm(["ci", "--ignore-scripts", "--no-audit", "--no-fund"]);
      npm(["run", "build"]);
      run(python, [join(root, "scripts", "web_notices.py")]);
      break;
    case "start":
    case "dev":
      requirePython();
      if (!existsSync(join(root, "web", "dist", "index.html"))) throw new Error("Run npm run build first.");
      console.log("Estera: http://localhost:3000\nKeep this terminal running. Ctrl-C or npm run stop exits and attempts iPhone reset.");
      run(python, ["-m", "openlocation_backend.web"]);
      break;
    case "stop":
      requirePython();
      run(python, ["-m", "openlocation_backend.instance", root]);
      break;
    case "check":
    case "check-web":
      requirePython();
      run(python, ["-m", "pytest", "-q", ...(extra.length ? extra : task === "check-web" ? ["tests/test_web.py", "tests/test_stop_access.py", "tests/test_instance.py"] : ["tests"])], join(root, "backend"));
      break;
    default:
      throw new Error("Choose bootstrap, build, start, dev, stop, check, or check-web.");
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
