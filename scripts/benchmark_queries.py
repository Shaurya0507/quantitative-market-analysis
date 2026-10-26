import argparse
import json
import re
import statistics
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text

from backend.app.settings import PROJECT_ROOT, get_database_url


def get_ranking_query() -> str:
    query_file = PROJECT_ROOT / "database" / "queries.sql"
    statements = [
        re.sub(r"(?m)^--.*$", "", statement).strip()
        for statement in query_file.read_text(encoding="utf-8").split(";")
        if "SELECT" in statement.upper()
    ]
    if len(statements) < 5:
        raise RuntimeError(f"Expected five analytics queries in {query_file}")
    return statements[4]


def _scan_nodes(plan: dict) -> list[dict]:
    nodes = []
    if "Node Type" in plan:
        nodes.append(
            {
                "node_type": plan["Node Type"],
                "relation": plan.get("Relation Name"),
                "index": plan.get("Index Name"),
                "actual_rows": plan.get("Actual Rows"),
                "shared_hit_blocks": plan.get("Shared Hit Blocks", 0),
                "shared_read_blocks": plan.get("Shared Read Blocks", 0),
            }
        )
    for child in plan.get("Plans", []):
        nodes.extend(_scan_nodes(child))
    return nodes


def benchmark(query: str, start_date: date, end_date: date, runs: int) -> dict:
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "include_synthetic": True,
    }
    statement = text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query}")
    engine = create_engine(get_database_url())
    samples = []
    plan = None
    try:
        with engine.connect() as connection:
            for run in range(runs + 1):
                result = connection.execute(statement, params).scalar_one()
                plan = result[0]
                if run:
                    samples.append(plan["Execution Time"])
    finally:
        engine.dispose()

    if plan is None:
        raise RuntimeError("PostgreSQL did not return an execution plan")
    return {
        "runs": runs,
        "median_execution_ms": round(statistics.median(samples), 3),
        "samples_execution_ms": [round(sample, 3) for sample in samples],
        "plan_nodes": _scan_nodes(plan["Plan"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark stock ranking using EXPLAIN ANALYZE and BUFFERS."
    )
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--label", default="benchmark")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    if args.runs < 1 or args.days < 1:
        parser.error("--runs and --days must be positive")

    start_date = args.end_date - timedelta(days=args.days)
    result = {
        "label": args.label,
        "query": "stock performance ranking",
        "date_range": [start_date.isoformat(), args.end_date.isoformat()],
        **benchmark(get_ranking_query(), start_date, args.end_date, args.runs),
    }
    serialized = json.dumps(result, indent=2)
    if args.output:
        output = args.output
        if not output.is_absolute():
            output = PROJECT_ROOT / output
        output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
