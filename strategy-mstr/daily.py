"""Daily Strategy MSTR refresh (local). Does not re-download reconstructed history.

1) python 1.download.py          KPIs + same-day snapshot + live overlay on history
2) copy_to_dashboard.py --force  latest / mnav_history / net_reserve_history
3) optional scp of those three JSON files to VPS public/json (no image rebuild)

Snapshots stay in output/json/snapshots/; they are not copied to the dashboard.

Usage:
    python daily.py
    python daily.py --push-vps
    python daily.py --register-task
    python daily.py --register-task --push-vps
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOWNLOAD = HERE / "1.download.py"
COPY = ROOT / "copy_to_dashboard.py"
TASK_NAME = "WuData-strategy-mstr-daily"
VPS_HOST = "root@47.74.6.207"
VPS_DIR = "/root/dashboard/public/json/strategy-mstr"
FILES = ("latest.json", "mnav_history.json", "net_reserve_history.json")


def log(msg: str) -> None:
    print(msg, flush=True)


def ssh_key() -> Path | None:
    key = Path.home() / ".ssh" / "data-dashboard-prod"
    return key if key.is_file() else None


def run(cmd: list[str]) -> int:
    log("$ " + " ".join(cmd))
    return subprocess.run(cmd).returncode


def push_vps() -> int:
    key = ssh_key()
    if key is None:
        log("ERROR: SSH key not found: ~/.ssh/data-dashboard-prod")
        return 1
    src_dir = ROOT.parent / "data-dashboard" / "public" / "json" / "strategy-mstr"
    missing = [name for name in FILES if not (src_dir / name).is_file()]
    if missing:
        log(f"ERROR: missing dashboard JSON: {missing}")
        return 1
    ssh = [
        "ssh",
        "-i",
        str(key),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        VPS_HOST,
        f"mkdir -p {VPS_DIR}",
    ]
    code = run(ssh)
    if code != 0:
        return code
    scp = [
        "scp",
        "-i",
        str(key),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=20",
        *[str(src_dir / name) for name in FILES],
        f"{VPS_HOST}:{VPS_DIR}/",
    ]
    return run(scp)


def register_task(push: bool) -> int:
    del push  # run-daily.cmd always pushes JSON to VPS
    cmd_path = HERE / "run-daily.cmd"
    task_cmd = [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/SC",
        "DAILY",
        "/ST",
        "07:30",
        "/F",
        "/RL",
        "LIMITED",
        "/TR",
        str(cmd_path),
    ]
    code = run(task_cmd)
    if code == 0:
        log(f"registered task {TASK_NAME} daily 07:30 -> {cmd_path}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push-vps", action="store_true", help="scp JSON to VPS after copy")
    parser.add_argument("--register-task", action="store_true", help="create Windows daily task")
    args = parser.parse_args()

    if args.register_task:
        return register_task(args.push_vps)

    py = sys.executable
    code = run([py, str(DOWNLOAD)])
    if code != 0:
        return code
    code = run([py, str(COPY), "--only", "strategy-mstr", "--force"])
    if code != 0:
        return code
    if args.push_vps:
        return push_vps()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
