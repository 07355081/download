-- 最近两个完整 UTC 日的 UNI 销毁。只扫这两天的 evms.logs，避免每天重放 2025-12-27 以来的全历史。
-- 累计列在这两天内单独计算；面板按日汇总 total_uni_burned，历史行由下载脚本按 day 合并保留。
WITH base AS (
    SELECT
        block_date,
        blockchain,
        tx_hash,
        contract_address
    FROM evms.logs
    WHERE block_date >= CURRENT_DATE - INTERVAL '2' DAY
      AND block_date < CURRENT_DATE
      AND blockchain IN (
            'ethereum', 'unichain', 'worldchain', 'soneium',
            'celo', 'zora', 'x_layer', 'arbitrum', 'optimism', 'base',
            'polygon', 'bnb', 'robinhood'
        )
      AND (
            (
                topic0 = 0x0143172ff1dd87f3691e870b3fb5616db820278d26d2d16c3e03330a240a6c38
                AND contract_address IN (
                    0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721,
                    0xe0A780E9105aC10Ee304448224Eb4A2b11A77eeB,
                    0x455e844D286631566cF98D6cb2996149734618C6,
                    0xc9CC50A75cE2a5f88fa77B43e3b050480c731b6e,
                    0x2758FbaA228D7d3c41dD139F47dab1a27bF9bc25,
                    0x2f98eD4D04e633169FbC941BFCc54E785853b143,
                    0xe122E231cb52aea99690963Fd73E91e33E97468f,
                    0xB8018422bcE25D82E70cB98FdA96a4f502D89427,
                    0x94460443Ca27FFC1baeCa61165fde18346C91AbD,
                    0xFf77c0ED0B6b13A20446969107E5867abc46f53a,
                    0xa59FfbB55D91Fc32b44A06F0b9cc6036a4afbcE2,
                    0x7A8F74C2585F84C781F951B7F2FF21337D5B630B
                )
            )
            OR (
                topic0 = 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
                AND tx_hash = 0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e
            )
        )
), daily_prices AS (
    SELECT
        timestamp AS block_date,
        price
    FROM prices.day
    WHERE blockchain = 'ethereum'
      AND contract_address = 0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984
      AND timestamp >= CURRENT_DATE - INTERVAL '2' DAY
      AND timestamp < CURRENT_DATE
), tx AS (
    SELECT
        block_date,
        blockchain,
        CASE
            WHEN tx_hash = 0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e
                THEN 'Proposal Burn'
            WHEN contract_address = 0xe0A780E9105aC10Ee304448224Eb4A2b11A77eeB
                THEN 'Unichain Sequencer Burn'
            WHEN contract_address IN (
                    0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721,
                    0x455e844D286631566cF98D6cb2996149734618C6,
                    0xc9CC50A75cE2a5f88fa77B43e3b050480c731b6e,
                    0x2758FbaA228D7d3c41dD139F47dab1a27bF9bc25,
                    0x2f98eD4D04e633169FbC941BFCc54E785853b143,
                    0xe122E231cb52aea99690963Fd73E91e33E97468f,
                    0xB8018422bcE25D82E70cB98FdA96a4f502D89427,
                    0x94460443Ca27FFC1baeCa61165fde18346C91AbD,
                    0xFf77c0ED0B6b13A20446969107E5867abc46f53a,
                    0xa59FfbB55D91Fc32b44A06F0b9cc6036a4afbcE2,
                    0x7A8F74C2585F84C781F951B7F2FF21337D5B630B
            )
                THEN 'Protocol Fee Burn'
            ELSE 'Unlabeled'
        END AS label,
        CASE
            WHEN tx_hash = 0x091f0083242a777d55821c1189e568d6d033d9da501b75087dc736fa143d2c1e
                THEN 100000000
            WHEN contract_address = 0x0D5Cd355e2aBEB8fb1552F56c965B867346d6721 OR blockchain = 'bnb'
                THEN 4000
            ELSE 2000
        END AS uni_burned
    FROM base
)
SELECT
    t.block_date AS day,
    t.blockchain,
    t.label,
    SUM(t.uni_burned) AS total_uni_burned,
    SUM(ROUND(t.uni_burned * p.price, 2)) AS total_uni_burned_usd,
    SUM(t.uni_burned) AS uni_burned_by_chain,
    COUNT(*) AS burn_ct
FROM tx t
LEFT JOIN daily_prices p ON p.block_date = t.block_date
GROUP BY 1, 2, 3
