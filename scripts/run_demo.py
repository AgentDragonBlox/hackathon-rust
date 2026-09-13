"""Run the Rust market, public-data grid, orchestrator and SvelteKit together."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    node = shutil.which("node")
    cargo = shutil.which("cargo")
    if cargo:
        rust = [cargo, "run", "--locked", "--bin", "agent_engines"]
    elif os.name == "nt" and shutil.which("wsl"):
        rust = ["wsl", "-e", "bash", "-c", 'exec "$HOME/.cargo/bin/cargo" run --locked --bin agent_engines']
    else:
        raise SystemExit("Install Rust with rustup, then reopen your terminal.")
    vite = ROOT / "dashboard" / "node_modules" / "vite" / "bin" / "vite.js"
    if not node or not vite.exists():
        raise SystemExit("Install Node.js and run npm ci in dashboard/ first.")
    for port in (8000, 8001, 8002, 5173):
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(f"Port {port} is already in use. Stop the previous demo before starting.")
    environment = {**os.environ, "GRID_SOURCE": "live", "AGENT_SOURCE": "live", "GRID_DATA_MODE": "public",
                   "GRID_ENGINE_URL": "http://127.0.0.1:8001", "AGENT_ENGINE_URL": "http://127.0.0.1:8002",
                   "PUBLIC_GRID_ENGINE_URL": "http://localhost:8001", "PUBLIC_AGENT_ENGINE_URL": "http://localhost:8002",
                   "PUBLIC_ORCHESTRATOR_URL": "http://localhost:8000", "PORT": "8002"}
    children = []
    logs = ROOT / ".demo-logs"
    logs.mkdir(exist_ok=True)
    handles = []

    def start(name, command, cwd=ROOT):
        handle = (logs / f"{name}.log").open("w", encoding="utf-8")
        handles.append(handle)
        child = subprocess.Popen(command, cwd=cwd, env=environment, stdout=handle, stderr=subprocess.STDOUT,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        children.append(child)
        return child

    def wait_ready(port, process, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Service on port {port} exited; see {logs}")
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(1)
        raise RuntimeError(f"Service on port {port} did not start; see {logs}")

    try:
        agent = start("rust", rust)
        grid = start("grid", [sys.executable, "-m", "uvicorn", "grid_engine.api:app", "--port", "8001"])
        print("Starting Rust and pandapower (first Rust build can take a few minutes)…", flush=True)
        wait_ready(8002, agent, 600)
        wait_ready(8001, grid)
        orchestrator = start("orchestrator", [sys.executable, "-m", "uvicorn", "orchestrator.main:app", "--port", "8000"])
        wait_ready(8000, orchestrator)
        start("dashboard", [node, str(vite), "--host", "127.0.0.1", "--port", "5173", "--strictPort"], ROOT / "dashboard")
        print(f"Demo: http://localhost:5173 | Logs: {logs} | Ctrl+C stops services", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise RuntimeError(f"A service exited; see {logs}")
    except KeyboardInterrupt:
        print("Stopping demo…")
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        for handle in handles:
            handle.close()


if __name__ == "__main__":
    main()
