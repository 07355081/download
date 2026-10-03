-- 最近两个完整 UTC 日。成交和兑换日志只扫这两天；建池日志仍从 2026-06-30 起，否则旧池的成交会对不上 launchpad。
-- 刷量地址只根据这两天的行为标记，和全历史查询的标记可能不同。
WITH token_map AS (
    SELECT token_address, launchpad
    FROM query_7979183
    WHERE launchpad <> 'other'
),
clanker_wash AS (
    SELECT wallet
    FROM query_8002883
),
wpools AS (
    SELECT
        varbinary_substring(l.data, 45, 20) AS pool,
        varbinary_substring(l.topic1, 13, 20) AS token0,
        m.launchpad
    FROM robinhood.logs l
    JOIN token_map m
        ON m.token_address = CASE
            WHEN varbinary_substring(l.topic1, 13, 20) = 0x0bd7d308f8e1639fab988df18a8011f41eacad73
            THEN varbinary_substring(l.topic2, 13, 20)
            ELSE varbinary_substring(l.topic1, 13, 20)
        END
    WHERE l.topic0 = 0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118
      AND l.block_date >= DATE '2026-06-30'
      AND (varbinary_substring(l.topic1, 13, 20) = 0x0bd7d308f8e1639fab988df18a8011f41eacad73
        OR varbinary_substring(l.topic2, 13, 20) = 0x0bd7d308f8e1639fab988df18a8011f41eacad73)
),
uniswap_trades AS (
    SELECT
        block_time,
        block_date,
        tx_hash,
        tx_from,
        taker,
        token_bought_address,
        token_sold_address,
        amount_usd,
        project,
        version,
        project_contract_address
    FROM dex.trades
    WHERE blockchain = 'robinhood'
      AND block_date >= CURRENT_DATE - INTERVAL '2' DAY
      AND block_date < CURRENT_DATE
),
baseline_trades AS (
    SELECT
        block_time,
        block_date,
        tx_hash,
        tx_from,
        taker,
        token_bought_address,
        token_sold_address,
        amount_usd,
        'baseline' AS project,
        '' AS version,
        CAST(NULL AS varbinary) AS project_contract_address
    FROM query_7986129
    WHERE block_date >= CURRENT_DATE - INTERVAL '2' DAY
      AND block_date < CURRENT_DATE
),
all_trades AS (
    SELECT * FROM uniswap_trades
    UNION ALL
    SELECT * FROM baseline_trades
),
wallet_stats AS (
    SELECT
        tx_from AS wallet,
        COUNT(*) / CAST(COUNT(DISTINCT tx_hash) AS DOUBLE) AS legs_per_tx,
        COUNT(DISTINCT block_date) AS days_active,
        COUNT(DISTINCT DATE_TRUNC('hour', block_time)) / CAST(COUNT(DISTINCT block_date) AS DOUBLE) AS avg_hours_per_day
    FROM all_trades
    GROUP BY 1
),
flagged AS (
    SELECT wallet
    FROM wallet_stats
    WHERE legs_per_tx > 5
       OR (avg_hours_per_day > 18 AND days_active >= 3)
),
v3_hourly AS (
    SELECT
        l.block_date,
        DATE_TRUNC('hour', l.block_time) AS hr,
        w.launchpad,
        t."from" AS wallet,
        SUM(
            ABS(
                CASE WHEN w.token0 = 0x0bd7d308f8e1639fab988df18a8011f41eacad73
                    THEN CAST(varbinary_to_int256(varbinary_substring(l.data, 1, 32)) AS double)
                    ELSE CAST(varbinary_to_int256(varbinary_substring(l.data, 33, 32)) AS double)
                END
            ) / 1e18
        ) AS weth_vol,
        COUNT(*) AS trades
    FROM robinhood.logs l
    JOIN wpools w ON w.pool = l.contract_address
    JOIN robinhood.transactions t
        ON t.hash = l.tx_hash
        AND t.block_date = l.block_date
    LEFT JOIN flagged fl ON fl.wallet = t."from"
    LEFT JOIN clanker_wash cw ON cw.wallet = t."from"
    WHERE l.topic0 = 0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67
      AND l.block_date >= CURRENT_DATE - INTERVAL '2' DAY
      AND l.block_date < CURRENT_DATE
      AND fl.wallet IS NULL
      AND cw.wallet IS NULL
      AND t."from" <> 0x1925f52cea3bb3e1b4958dad50346b3c34a98b44
    GROUP BY 1, 2, 3, 4
),
v3_rows AS (
    SELECT
        h.block_date AS day,
        h.launchpad,
        h.wallet,
        SUM(h.weth_vol * pr.price) AS volume_usd,
        SUM(h.trades) AS trades
    FROM v3_hourly h
    JOIN prices.hour pr
        ON pr.blockchain = 'ethereum'
        AND pr.contract_address = 0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2
        AND pr.timestamp = h.hr
        AND pr.timestamp >= CURRENT_DATE - INTERVAL '2' DAY
    GROUP BY 1, 2, 3
),
dex_rows AS (
    SELECT
        tr.block_date AS day,
        COALESCE(mb.launchpad, ms.launchpad) AS launchpad,
        tr.tx_from AS wallet,
        SUM(tr.amount_usd) AS volume_usd,
        COUNT(*) AS trades
    FROM all_trades tr
    LEFT JOIN token_map mb ON tr.token_bought_address = mb.token_address
    LEFT JOIN token_map ms ON tr.token_sold_address = ms.token_address
    LEFT JOIN wpools wp
        ON wp.pool = tr.project_contract_address
        AND tr.project = 'uniswap'
        AND tr.version = '3'
    LEFT JOIN flagged fl ON fl.wallet = tr.tx_from
    LEFT JOIN clanker_wash cw ON cw.wallet = tr.tx_from
    WHERE wp.pool IS NULL
      AND fl.wallet IS NULL
      AND cw.wallet IS NULL
      AND tr.taker <> 0x1925f52cea3bb3e1b4958dad50346b3c34a98b44
      AND tr.tx_from <> 0x1925f52cea3bb3e1b4958dad50346b3c34a98b44
      AND COALESCE(mb.launchpad, ms.launchpad) IS NOT NULL
    GROUP BY 1, 2, 3
),
combined AS (
    SELECT day, launchpad, wallet, volume_usd, trades FROM v3_rows
    UNION ALL
    SELECT day, launchpad, wallet, volume_usd, trades FROM dex_rows
)
SELECT
    day,
    launchpad,
    SUM(volume_usd) AS volume_usd,
    SUM(trades) AS trades,
    COUNT(DISTINCT wallet) AS wallets
FROM combined
GROUP BY 1, 2
ORDER BY 1, 3 DESC