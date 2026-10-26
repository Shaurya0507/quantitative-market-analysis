# PostgreSQL benchmark results

## Setup and method

- PostgreSQL 16 in the local Docker Compose database.
- 1,000,300 `stock_prices` rows: 1,000,000 deterministic synthetic rows plus
  the 300 imported AAPL, MSFT, and NVDA rows.
- The synthetic portion contains 200 clearly labeled `SYN...` tickers with
  5,000 generated weekday observations each. The complete price table and
  indexes occupied 133 MB at measurement time.
- Query: stock performance ranking for the inclusive SQL range
  `2026-07-25` through `2026-10-23`.
- `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`, one warm-up followed by seven
  measured executions; report the median.
- Both the baseline and retained rewrite use the same indexes:
  `(ticker, price_date)` primary key and `(price_date, ticker)` secondary
  index. Timings are local, warm-cache measurements and will vary by machine.

## Before and after

| Version | Query plan | Median execution |
|---|---|---:|
| Before | Date index bitmap scan, 13,192 price rows passed into multiple window operations and sorts | 33.389 ms |
| After | Two bounded `(ticker, price_date)` primary-key lookups per stock, then rank 203 returns | 3.861 ms |

The measured latency reduction is **88.44%**:
`(33.389 - 3.861) / 33.389 * 100`.
The old and rewritten ranking queries returned identical ranked rows for all
203 tickers in the selected date range.

The query rewrite uses `LATERAL` lookups to find only the first and last close
for each stock. PostgreSQL's plan changed from a bitmap heap scan and window
operations over 13,192 price observations to 406 bounded index lookups (two
per stock) and ranking 203 results. Both plans reported zero shared disk reads;
the rewrite reduced measured execution time, though its shared-buffer hit
count was higher.

## Index experiment

A covering `(price_date, ticker) INCLUDE (close_price)` index changed the plan
to an index-only scan and lowered buffer hits, but the original window query's
median was **64.752 ms**, slower than its 33.389 ms baseline. That index change
was rejected and is not present in the final schema. This is why the final
improvement is attributed to the measured query rewrite, not to adding an
index.

Raw plans and samples are saved in
[`benchmark_before.json`](./benchmark_before.json),
[`benchmark_rewrite.json`](./benchmark_rewrite.json), and
[`benchmark_covering_index.json`](./benchmark_covering_index.json).

## Reproducing

From the project root, with PostgreSQL running and `.env` configured:

```bash
.venv/bin/python -m scripts.generate_synthetic_data \
  --tickers 200 --observations 5000 --end-date 2026-10-23
.venv/bin/python -m scripts.benchmark_queries \
  --days 90 --runs 7 --end-date 2026-10-23
```

Generation is deterministic and uses upserts. Synthetic stock names and sectors
are explicitly labeled; generated records remain in the local database for
repeated benchmarks and are included in stock listings and performance
rankings.
