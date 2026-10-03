# Data Lake 拉取 + 健康检查

VPS 下载 → parquet → outbox → 本机 rsync 拉取 → DuckDB 只读视图（`tradfi/lake.py`）→
量化代码。本目录收纳的是**本机侧**的拉取脚本和覆盖率检查，以及 **VPS 侧脚本的只读镜像**（供审查/版本控制，不会自动部署）。

## 端到端链路

```
VPS (root@47.74.6.207)                          本机 (E:\)
──────────────────────                          ──────────
crontab 0 9 * * * (CST)
  run_tradfi_daily.sh
    ├─ tradfi/download.py          (下载 → tradfi/output/json/<module>/*.json)
    ├─ tradfi/to_parquet.py        (JSON → parquet, 原子写 .staging/ + os.replace)
    │    → /root/outbox/<module>/<EXCHANGE>/*.parquet
    └─ copy_to_dashboard.py        (best-effort, 供网页, 与本链路无关)

                                    Windows 计划任务「DataLake Pull Outbox」每 30 分钟
                                      pull_outbox.sh
                                        rsync --remove-source-files
                                        VPS:/root/outbox/  →  E:\data-lake\
                                        (排除 .staging/，拉完清 VPS 空目录)

                                    Windows 计划任务「DataLake Coverage Check」每天 10:30
                                      check_lake_coverage.py
                                        DuckDB 读 E:\data-lake\tradfi-*/**/*.parquet
                                        → 覆盖率/时效性 → 日志 + 退出码

                                    量化代码
                                      data-download/tradfi/lake.py::connect()/daily_panel_sql()
```

## 目录内容

| 文件 | 作用 | 部署位置 |
|------|------|----------|
| `pull_outbox.sh` | 本机拉取脚本（真源） | 由 Windows 计划任务直接执行 `E:\Code\...\pull_outbox.sh`，无需再复制 |
| `check_lake_coverage.py` | 每日覆盖率/时效性检查（真源） | 同上，计划任务直接执行仓库路径 |
| `register_pull_task.ps1` | 注册/更新「DataLake Pull Outbox」计划任务 | 改完 `pull_outbox.sh` 逻辑或调度后手动跑一次 |
| `register_coverage_task.ps1` | 注册/更新「DataLake Coverage Check」计划任务 | 同上 |
| `vps/run_tradfi_daily.sh` | VPS 每日调度脚本的**只读镜像** | 真源在 VPS `/root/data-download/run_tradfi_daily.sh`；改这里不会生效，需要 SSH 上去改 |
| `vps/to_parquet.py` | VPS JSON→parquet 派生脚本的**只读镜像** | 真源在 VPS `/root/data-download/tradfi/to_parquet.py`，同上 |

## 运行时状态（不入 git）

`E:\data-pipeline\`：

- `.pull.lock` —— flock 并发锁
- `logs\pull_YYYYMMDD.log` —— 每日拉取日志
- `logs\coverage_YYYYMMDD.log` —— 每日覆盖率检查日志

## 手动排障

```powershell
# 立刻跑一次拉取
schtasks /run /tn "\DataLake Pull Outbox"

# 立刻跑一次覆盖率检查（也可直接跑脚本看 stdout）
schtasks /run /tn "\DataLake Coverage Check"
wsl -d Ubuntu -u root python3 /mnt/e/Code/data-download/data-lake/check_lake_coverage.py

# 看两个任务最近一次是否成功（0=成功）
schtasks /query /tn "\DataLake Pull Outbox" /fo LIST /v
schtasks /query /tn "\DataLake Coverage Check" /fo LIST /v

# 看今天的日志
type E:\data-pipeline\logs\pull_20260714.log
type E:\data-pipeline\logs\coverage_20260714.log
```

## outbox 契约（新增 VPS 下载模块时必须遵守）

`pull_outbox.sh` 本身不认识任何具体模块，它只是无脑同步 `/root/outbox/` 整棵树（排除
`.staging/`）。**任何新模块想被本机自动拉到，只需要遵守这个契约，不需要改
`pull_outbox.sh`**：

1. 最终文件路径：`/root/outbox/<module>/...`（`<module>` 建议跟本机 `data-lake` 下的目录名一致，比如 `coinglass-oi`）。
2. **原子写**：先写到 `/root/outbox/.staging/<uuid 或临时名>`，写完后用 `os.replace()`
   /`mv` 同文件系统内原子改名到最终路径。`outbox` 下除 `.staging/` 外出现的任何文件，
   必须是**写完的完整文件**——本机拉取没有 `.done` 标记机制，全靠这条原子性契约。
3. 拉完即删（`--remove-source-files`），VPS 侧不需要自己清理已拉走的文件，
   只需要保证空目录会被后续任务的 `find ... -empty -delete` 清掉（已内置在 `pull_outbox.sh` 里，对所有模块通用）。
4. 格式不限：parquet 是当前约定（配合 `tradfi/lake.py` 的 DuckDB 视图),但只要是普通文件，
   遵守 1/2/3 条，`pull_outbox.sh` 一样能拉。

参考实现：`vps/to_parquet.py`（把 `output/json/<module>/*.json` 逐个转成
`outbox/<module>/<EXCHANGE>/*.parquet`，附带 `.staging/` + `os.replace()` 原子写）。
