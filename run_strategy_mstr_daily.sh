#!/bin/bash
# strategy-mstr 每日管道(Strategy/MSTR 的 BTC KPI, 供 /dashboard/treasuries 顶部 KPI 与 mNAV 曲线):
#   strategy-mstr/1.download.py 拉 api.strategy.com 的 bitcoinKpis / mstrKpiData / options / credit
#   → 增量写 output/json/latest.json + snapshots/YYYY-MM-DD.json, 并把当日 mNAV / wipeout
#     叠加到 mnav_history.json / net_reserve_history.json(重建历史是 2.backfill_mnav.py 的一次性产物,
#     日更绝不重拉全历史)
#   → copy_to_dashboard 镜像三份扁平 json 到 dashboard(snapshots 留在 download 侧, 不上前端)
# 只有 4 个 API 请求 + 几百 KB 落盘, 不动重活, 所以刻意不参与 coinglass/misc/tradfi 那把
# .pipeline.lock 全局串行锁: 用自己的锁, 失败与等待都不牵连别的管道。
# 日志:_strategy_mstr_daily.log(已被 /etc/logrotate.d/guard 的 /root/data-download/*.log 覆盖)。
# 由 root crontab 每日 07:00 调用, 对应美东前一日收盘后的 KPI 快照。
set -o pipefail
cd /root/data-download || exit 1

VENV=./.venv/bin/python
LOG=/root/data-download/_strategy_mstr_daily.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }

exec 9>/root/data-download/.strategy_mstr.lock
flock -n 9 || { echo "[$(ts)] 上一轮仍在跑,跳过本次" >> "$LOG"; exit 0; }

rc=0
{
  echo "==================== [$(ts)] strategy-mstr 开始 ===================="

  echo "[$(ts)] 1/2 下载 KPI(增量,不重拉全历史)..."
  if $VENV strategy-mstr/1.download.py; then
    echo "[$(ts)] 2/2 镜像 json → dashboard ..."
    $VENV copy_to_dashboard.py --dest /root/dashboard/public/json --only strategy-mstr --force \
      || { echo "[$(ts)]   [ERROR] copy_to_dashboard 失败"; rc=1; }
  else
    # 下载失败就不要镜像:前端继续读昨天的 json,好过被半截数据覆盖。
    echo "[$(ts)]   [ERROR] 1.download.py 失败,跳过镜像"
    rc=1
  fi

  echo "[$(ts)] 完成 rc=$rc"
} >> "$LOG" 2>&1

exit $rc
