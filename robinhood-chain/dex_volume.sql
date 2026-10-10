-- 最近两个完整 UTC 日的 Robinhood Chain 全链 DEX 成交额。
-- 直接汇总 dex.trades，不再读别人上传的 dataset_robinhood_daily。
-- 历史行由下载脚本按 day 合并保留。
-- 查询 8894975 在第一次成功执行前，Dune 上保存的是从 2026-09-23 起的补洞语句；
-- 补洞写入本地文件后，下载脚本会把 Dune 上的 SQL 改回本文件。
SELECT
    CAST(block_date AS VARCHAR) AS day,
    SUM(amount_usd) AS dex_volume_usd
FROM dex.trades
WHERE blockchain = 'robinhood'
  AND block_date >= CURRENT_DATE - INTERVAL '2' DAY
  AND block_date < CURRENT_DATE
GROUP BY 1
ORDER BY 1
