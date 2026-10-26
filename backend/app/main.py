import re
from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Path, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import Connection, create_engine, text

from backend.app.anomalies import ANOMALY_VERSION, INDICATOR_SQL, RULES, process_ticker_anomalies
from backend.app.jobs import enqueue_ingestion, get_job, get_ticker_status
from backend.app.modeling import MODEL_RULES, MODEL_VERSION, process_ticker_models
from backend.app.refresh import schedule_market_refreshes
from backend.app.session_lifecycle import register_dashboard_session
from backend.app.settings import get_database_url, get_supported_stocks

TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")

engine = create_engine(get_database_url(), pool_pre_ping=True)
app = FastAPI(
    title="Stock Market Analytics API",
    description="Daily historical stock prices and PostgreSQL-powered analytics.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class Stock(BaseModel):
    ticker: str
    company_name: str
    sector: str | None
    exchange: str | None = None
    data_source: str | None = None
    latest_available_date: date | None = None
    last_successful_ingestion: datetime | None = None
    ingestion_status: str = "COMPLETED"


class StockSearchResult(BaseModel):
    ticker: str
    company_name: str
    sector: str
    exchange: str
    is_loaded: bool
    latest_available_date: date | None
    ingestion_status: str
    data_source: str | None
    historical_data_available: bool


class LoadStockRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10, pattern=r"^[A-Za-z][A-Za-z0-9.-]{0,9}$")


class MarketRefreshRequest(BaseModel):
    ticker: str | None = Field(
        default=None,
        min_length=1,
        max_length=10,
        pattern=r"^[A-Za-z][A-Za-z0-9.-]{0,9}$",
    )


class DashboardSessionRequest(BaseModel):
    session_id: UUID


class PriceRecord(BaseModel):
    ticker: str
    price_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int


class MovingAverageRecord(BaseModel):
    ticker: str
    price_date: date
    close_price: Decimal
    moving_average_7: Decimal


class VolatilityResult(BaseModel):
    ticker: str
    start_date: date
    end_date: date
    volatility_pct: Decimal | None


class TopPerformer(BaseModel):
    performance_rank: int
    ticker: str
    company_name: str
    period_return_pct: Decimal


class Anomaly(BaseModel):
    ticker: str
    company_name: str
    price_date: date
    daily_return: float
    return_z_score: float | None
    volume_ratio: float | None
    rolling_volatility: float | None
    intraday_range: float | None
    severity: str
    explanation: str
    triggered_indicators: list[str]
    detection_method: str
    detection_details: list[dict[str, Any]]
    detection_date: date
    model_version: str


class CompanyDirectoryItem(StockSearchResult):
    pass


class CompanyDirectoryPage(BaseModel):
    total: int
    limit: int
    offset: int
    sectors: list[str]
    items: list[CompanyDirectoryItem]


def get_connection() -> Generator[Connection, None, None]:
    with engine.connect() as connection:
        yield connection


ConnectionDependency = Annotated[Connection, Depends(get_connection)]
TickerPath = Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9.-]{0,9}$")]
StartDate = Annotated[date, Query(description="First inclusive trading date")]
EndDate = Annotated[date, Query(description="Last inclusive trading date")]


def normalize_ticker(ticker: str) -> str:
    normalized = ticker.upper()
    if not TICKER_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="Invalid ticker symbol")
    return normalized


def validate_date_range(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise HTTPException(
            status_code=422,
            detail="start_date must be on or before end_date",
        )


def require_real_stock(connection: Connection, ticker: str) -> str:
    normalized = normalize_ticker(ticker)
    found = connection.execute(
        text(
            """
            SELECT 1 FROM stocks
            WHERE ticker = :ticker
              AND is_synthetic = FALSE
              AND EXISTS (
                  SELECT 1 FROM stock_prices p WHERE p.ticker = stocks.ticker
              )
            """
        ),
        {"ticker": normalized},
    ).scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail="Real stock data not found")
    return normalized


@app.get("/anomalies/summary", tags=["anomalies"])
def anomaly_summary(connection: ConnectionDependency) -> dict:
    row = connection.execute(
        text(
            """
            SELECT
                (SELECT COUNT(*)
                 FROM stocks s
                 WHERE s.is_synthetic = FALSE
                   AND EXISTS (
                       SELECT 1 FROM stock_prices p WHERE p.ticker = s.ticker
                   )) AS monitored_stocks,
                COUNT(a.id) FILTER (WHERE a.detection_version = :version) AS total_anomalies,
                COUNT(a.id) FILTER (
                    WHERE a.detection_version = :version AND a.severity = 'HIGH'
                ) AS high_severity_anomalies,
                (SELECT MAX(p.price_date)
                 FROM stock_prices p
                 JOIN stocks latest_stock ON latest_stock.ticker = p.ticker
                 WHERE latest_stock.is_synthetic = FALSE) AS latest_data_date
            FROM anomaly_results a
            JOIN stocks s ON s.ticker = a.ticker
            WHERE s.is_synthetic = FALSE
            """
        ),
        {"version": ANOMALY_VERSION},
    ).mappings().one()
    return dict(row)


@app.get("/anomalies", response_model=list[Anomaly], tags=["anomalies"])
def list_anomalies(
    connection: ConnectionDependency,
    ticker: Annotated[str | None, Query(max_length=10)] = None,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    severity: Annotated[str | None, Query(pattern=r"^(LOW|MEDIUM|HIGH)$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    if ticker:
        ticker = normalize_ticker(ticker)
    rows = connection.execute(
        text(
            """
            SELECT a.ticker, s.company_name, a.price_date, a.daily_return,
                   a.return_z_score, a.volume_ratio, a.rolling_volatility,
                   a.intraday_range, a.severity, a.explanation,
                   a.triggered_indicators, a.detection_method,
                   a.detection_details, a.price_date AS detection_date,
                   a.detection_version AS model_version
            FROM anomaly_results a
            JOIN stocks s ON s.ticker = a.ticker
            WHERE s.is_synthetic = FALSE
              AND a.detection_version = :version
              AND (CAST(:ticker AS VARCHAR(10)) IS NULL OR a.ticker = :ticker)
              AND (CAST(:start_date AS DATE) IS NULL OR a.price_date >= :start_date)
              AND (CAST(:end_date AS DATE) IS NULL OR a.price_date <= :end_date)
              AND (CAST(:severity AS VARCHAR(10)) IS NULL OR a.severity = :severity)
            ORDER BY
                CASE a.severity WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,
                a.price_date DESC,
                a.ticker
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "ticker": ticker,
            "start_date": start_date,
            "end_date": end_date,
            "severity": severity,
            "version": ANOMALY_VERSION,
            "limit": limit,
            "offset": offset,
        },
    ).mappings()
    return [dict(row) for row in rows]


@app.get("/anomalies/{ticker}/latest", tags=["anomalies"])
def latest_ticker_anomaly(ticker: TickerPath, connection: ConnectionDependency) -> dict:
    normalized = normalize_ticker(ticker)
    row = connection.execute(
        text(
            """
            SELECT a.ticker, s.company_name, a.price_date, a.daily_return,
                   a.return_z_score, a.volume_ratio, a.rolling_volatility,
                   a.intraday_range, a.severity, a.explanation,
                   a.triggered_indicators, a.detection_method,
                   a.detection_details, a.price_date AS detection_date,
                   a.detection_version AS model_version
            FROM anomaly_results a
            JOIN stocks s ON s.ticker = a.ticker
            WHERE a.ticker = :ticker AND s.is_synthetic = FALSE
              AND a.detection_version = :version
            ORDER BY a.price_date DESC
            LIMIT 1
            """
        ),
        {"ticker": normalized, "version": ANOMALY_VERSION},
    ).mappings().one_or_none()
    return {"ticker": normalized, "anomaly": dict(row) if row else None}


@app.get("/anomalies/{ticker}", response_model=list[Anomaly], tags=["anomalies"])
def ticker_anomalies(
    ticker: TickerPath,
    connection: ConnectionDependency,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict]:
    normalized = normalize_ticker(ticker)
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    rows = connection.execute(
        text(
            """
            SELECT a.ticker, s.company_name, a.price_date, a.daily_return,
                   a.return_z_score, a.volume_ratio, a.rolling_volatility,
                   a.intraday_range, a.severity, a.explanation,
                   a.triggered_indicators, a.detection_method,
                   a.detection_details, a.price_date AS detection_date,
                   a.detection_version AS model_version
            FROM anomaly_results a
            JOIN stocks s ON s.ticker = a.ticker
            WHERE a.ticker = :ticker AND s.is_synthetic = FALSE
              AND a.detection_version = :version
              AND (CAST(:start_date AS DATE) IS NULL OR a.price_date >= :start_date)
              AND (CAST(:end_date AS DATE) IS NULL OR a.price_date <= :end_date)
            ORDER BY a.price_date DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "ticker": normalized,
            "version": ANOMALY_VERSION,
            "start_date": start_date,
            "end_date": end_date,
            "limit": limit,
            "offset": offset,
        },
    ).mappings()
    return [dict(row) for row in rows]


@app.post("/anomalies/{ticker}/reprocess", tags=["anomalies"])
def reprocess_ticker_anomalies(ticker: TickerPath) -> dict:
    normalized = normalize_ticker(ticker)
    count = process_ticker_anomalies(normalized)
    return {
        "ticker": normalized,
        "detection_version": ANOMALY_VERSION,
        "anomalies_stored": count,
    }


@app.get("/stocks", response_model=list[Stock], tags=["stocks"])
def list_stocks(connection: ConnectionDependency) -> list[dict]:
    rows = connection.execute(
        text(
            """
            SELECT ticker, company_name, sector, exchange, data_source,
                   latest_available_date, last_successful_ingestion,
                   ingestion_status
            FROM stocks
            WHERE is_synthetic = FALSE
              AND EXISTS (
                  SELECT 1 FROM stock_prices p
                  WHERE p.ticker = stocks.ticker
              )
            ORDER BY ticker
            """
        )
    )
    return [dict(row) for row in rows.mappings()]


@app.post("/dashboard/session", tags=["session"])
def start_dashboard_session(request: DashboardSessionRequest) -> dict[str, Any]:
    cache_reset, deleted_prices = register_dashboard_session(engine, request.session_id)
    return {
        "session_id": str(request.session_id),
        "cache_reset": cache_reset,
        "deleted_price_records": deleted_prices,
    }


@app.get("/stocks/search", response_model=list[StockSearchResult], tags=["stocks"])
def search_stocks(
    connection: ConnectionDependency,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    sector: Annotated[str | None, Query(max_length=100)] = None,
) -> list[dict]:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=422, detail="Search query cannot be blank")
    catalog = get_supported_stocks()
    matching = [
        stock for stock in catalog.values()
        if query.casefold() in stock["ticker"].casefold()
        or query.casefold() in stock["company_name"].casefold()
        or query.casefold() in stock["sector"].casefold()
    ]
    if sector:
        matching = [
            stock for stock in matching
            if stock["sector"].casefold() == sector.casefold()
        ]
    matching = sorted(matching, key=lambda stock: stock["ticker"])
    total = len(matching)
    matching = matching[offset : offset + limit]
    if not matching:
        return []
    loaded = connection.execute(
        text(
            """
            SELECT ticker, latest_available_date, ingestion_status, data_source
            FROM stocks
            WHERE is_synthetic = FALSE
              AND ticker = ANY(:tickers)
              AND EXISTS (
                  SELECT 1 FROM stock_prices p
                  WHERE p.ticker = stocks.ticker
              )
            """
        ),
        {"tickers": [stock["ticker"] for stock in matching]},
    ).mappings()
    loaded_by_ticker = {row["ticker"]: dict(row) for row in loaded}
    return [
        {
            **stock,
            "is_loaded": stock["ticker"] in loaded_by_ticker,
            "latest_available_date": loaded_by_ticker.get(stock["ticker"], {}).get(
                "latest_available_date"
            ),
            "ingestion_status": loaded_by_ticker.get(stock["ticker"], {}).get(
                "ingestion_status", "NOT_LOADED"
            ),
            "data_source": loaded_by_ticker.get(stock["ticker"], {}).get(
                "data_source", "alphavantage"
            ),
            "historical_data_available": stock["ticker"] in loaded_by_ticker,
        }
        for stock in matching
    ]


@app.get("/stocks/directory", response_model=CompanyDirectoryPage, tags=["stocks"])
def company_directory(
    connection: ConnectionDependency,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sector: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    catalog = sorted(get_supported_stocks().values(), key=lambda stock: stock["ticker"])
    sectors = sorted({stock["sector"] for stock in catalog})
    filtered = catalog
    if q and q.strip():
        query = q.strip().casefold()
        filtered = [
            stock for stock in filtered
            if query in stock["ticker"].casefold()
            or query in stock["company_name"].casefold()
        ]
    if sector:
        filtered = [
            stock for stock in filtered
            if stock["sector"].casefold() == sector.casefold()
        ]
    total = len(filtered)
    page = filtered[offset : offset + limit]
    if page:
        loaded = connection.execute(
            text(
                """
                SELECT ticker, latest_available_date, ingestion_status, data_source
                FROM stocks
                WHERE is_synthetic = FALSE
                  AND ticker = ANY(:tickers)
                  AND EXISTS (
                      SELECT 1 FROM stock_prices p
                      WHERE p.ticker = stocks.ticker
                  )
                """
            ),
            {"tickers": [stock["ticker"] for stock in page]},
        ).mappings()
        loaded_by_ticker = {row["ticker"]: dict(row) for row in loaded}
    else:
        loaded_by_ticker = {}
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "sectors": sectors,
        "items": [
            {
                **stock,
                "is_loaded": stock["ticker"] in loaded_by_ticker,
                "latest_available_date": loaded_by_ticker.get(
                    stock["ticker"], {}
                ).get("latest_available_date"),
                "ingestion_status": loaded_by_ticker.get(
                    stock["ticker"], {}
                ).get("ingestion_status", "NOT_LOADED"),
                "data_source": loaded_by_ticker.get(
                    stock["ticker"], {}
                ).get("data_source", "alphavantage"),
                "historical_data_available": stock["ticker"] in loaded_by_ticker,
            }
            for stock in page
        ],
    }


@app.post("/stocks/load", status_code=status.HTTP_202_ACCEPTED, tags=["ingestion"])
def load_stock(request: LoadStockRequest) -> dict:
    ticker = request.ticker.upper()
    job, created = enqueue_ingestion(ticker)
    if job is None:
        raise HTTPException(status_code=503, detail="Unable to create ingestion job")
    if job["status"] == "CACHED":
        return {
            "ticker": ticker,
            "job_id": None,
            "status": "CACHED",
            "message": f"{ticker} has recent historical data in PostgreSQL.",
        }
    message = (
        f"Retrieving historical data for {ticker}."
        if created
        else f"{ticker} already has an active ingestion job."
    )
    return {**job, "message": message}


@app.post("/market/refresh", tags=["ingestion"])
def refresh_market_data(request: MarketRefreshRequest) -> dict:
    selected_ticker = normalize_ticker(request.ticker) if request.ticker else None
    return schedule_market_refreshes(selected_ticker, engine=engine)


@app.get("/health", tags=["health"])
def health_check(connection: ConnectionDependency) -> dict[str, str]:
    connection.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/ingestion/jobs/{job_id}", tags=["ingestion"])
def ingestion_job_status(job_id: UUID) -> dict:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Ingestion job not found")
    return job


@app.get("/stocks/{ticker}/status", tags=["ingestion"])
def stock_ingestion_status(ticker: TickerPath) -> dict:
    normalized_ticker = normalize_ticker(ticker)
    result = get_ticker_status(normalized_ticker)
    if result is None:
        raise HTTPException(status_code=404, detail="Ticker status not found")
    return result


@app.get("/stocks/{ticker}", response_model=Stock, tags=["stocks"])
def get_stock(ticker: TickerPath, connection: ConnectionDependency) -> dict:
    normalized_ticker = normalize_ticker(ticker)
    row = connection.execute(
        text(
            """
            SELECT ticker, company_name, sector, exchange, data_source,
                   latest_available_date, last_successful_ingestion,
                   ingestion_status
            FROM stocks
            WHERE ticker = :ticker AND is_synthetic = FALSE
              AND EXISTS (
                  SELECT 1 FROM stock_prices p
                  WHERE p.ticker = stocks.ticker
              )
            """
        ),
        {"ticker": normalized_ticker},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return dict(row)


@app.get(
    "/stocks/{ticker}/history",
    response_model=list[PriceRecord],
    tags=["analytics"],
)
def get_history(
    ticker: TickerPath,
    connection: ConnectionDependency,
    start_date: StartDate,
    end_date: EndDate,
) -> list[dict]:
    validate_date_range(start_date, end_date)
    normalized_ticker = require_real_stock(connection, ticker)
    rows = connection.execute(
        text(
            """
            SELECT ticker, price_date, open_price, high_price, low_price,
                   close_price, volume
            FROM stock_prices
            WHERE ticker = :ticker
              AND price_date BETWEEN :start_date AND :end_date
            ORDER BY price_date
            """
        ),
        {
            "ticker": normalized_ticker,
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings()
    records = [dict(row) for row in rows]
    if not records:
        raise HTTPException(status_code=404, detail="No price history found")
    return records


@app.get(
    "/stocks/{ticker}/moving-average",
    response_model=list[MovingAverageRecord],
    tags=["analytics"],
)
def get_moving_average(
    ticker: TickerPath,
    connection: ConnectionDependency,
    start_date: StartDate,
    end_date: EndDate,
) -> list[dict]:
    validate_date_range(start_date, end_date)
    normalized_ticker = require_real_stock(connection, ticker)
    rows = connection.execute(
        text(
            """
            WITH price_window AS (
                (
                    SELECT ticker, price_date, close_price
                    FROM stock_prices
                    WHERE ticker = :ticker
                      AND price_date < :start_date
                    ORDER BY price_date DESC
                    LIMIT 6
                )
                UNION ALL
                (
                    SELECT ticker, price_date, close_price
                    FROM stock_prices
                    WHERE ticker = :ticker
                      AND price_date BETWEEN :start_date AND :end_date
                )
            ),
            moving_averages AS (
                SELECT
                    ticker,
                    price_date,
                    close_price,
                    AVG(close_price) OVER (
                        PARTITION BY ticker
                        ORDER BY price_date
                        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                    ) AS moving_average_7
                FROM price_window
            )
            SELECT ticker, price_date, close_price, moving_average_7
            FROM moving_averages
            WHERE price_date BETWEEN :start_date AND :end_date
            ORDER BY price_date
            """
        ),
        {
            "ticker": normalized_ticker,
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings()
    records = [dict(row) for row in rows]
    if not records:
        raise HTTPException(status_code=404, detail="No price history found")
    return records


@app.get(
    "/stocks/{ticker}/volatility",
    response_model=VolatilityResult,
    tags=["analytics"],
)
def get_volatility(
    ticker: TickerPath,
    connection: ConnectionDependency,
    start_date: StartDate,
    end_date: EndDate,
) -> dict:
    validate_date_range(start_date, end_date)
    normalized_ticker = require_real_stock(connection, ticker)
    result = connection.execute(
        text(
            """
            WITH price_window AS (
                (
                    SELECT ticker, price_date, close_price
                    FROM stock_prices
                    WHERE ticker = :ticker
                      AND price_date < :start_date
                    ORDER BY price_date DESC
                    LIMIT 1
                )
                UNION ALL
                (
                    SELECT ticker, price_date, close_price
                    FROM stock_prices
                    WHERE ticker = :ticker
                      AND price_date BETWEEN :start_date AND :end_date
                )
            ),
            daily_returns AS (
                SELECT
                    ticker,
                    price_date,
                    (
                        close_price / NULLIF(
                            LAG(close_price) OVER (
                                PARTITION BY ticker
                                ORDER BY price_date
                            ),
                            0
                        ) - 1
                    ) * 100 AS daily_return_pct
                FROM price_window
            )
            SELECT ticker, STDDEV_SAMP(daily_return_pct) AS volatility_pct
            FROM daily_returns
            WHERE price_date BETWEEN :start_date AND :end_date
            GROUP BY ticker
            """
        ),
        {
            "ticker": normalized_ticker,
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings().one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="No price history found")
    return {
        "ticker": normalized_ticker,
        "start_date": start_date,
        "end_date": end_date,
        "volatility_pct": result["volatility_pct"],
    }


@app.get(
    "/market/top-performers",
    response_model=list[TopPerformer],
    tags=["analytics"],
)
def get_top_performers(
    connection: ConnectionDependency,
    start_date: StartDate,
    end_date: EndDate,
) -> list[dict]:
    validate_date_range(start_date, end_date)
    rows = connection.execute(
        text(
            """
            WITH stock_returns AS (
                SELECT
                    s.ticker,
                    s.company_name,
                    (last_price.close_price / NULLIF(first_price.close_price, 0) - 1) * 100
                        AS period_return_pct
                FROM stocks AS s
                CROSS JOIN LATERAL (
                    SELECT close_price, price_date
                    FROM stock_prices AS p
                    WHERE p.ticker = s.ticker
                      AND p.price_date BETWEEN :start_date AND :end_date
                    ORDER BY p.price_date
                    LIMIT 1
                ) AS first_price
                CROSS JOIN LATERAL (
                    SELECT close_price, price_date
                    FROM stock_prices AS p
                    WHERE p.ticker = s.ticker
                      AND p.price_date BETWEEN :start_date AND :end_date
                    ORDER BY p.price_date DESC
                    LIMIT 1
                ) AS last_price
                WHERE (:include_synthetic OR s.is_synthetic = FALSE)
                  AND last_price.price_date > first_price.price_date
            ),
            ranked_stocks AS (
                SELECT
                    ticker,
                    company_name,
                    period_return_pct,
                    RANK() OVER (ORDER BY period_return_pct DESC)
                        AS performance_rank
                FROM stock_returns
            )
            SELECT
                performance_rank,
                ticker,
                company_name,
                period_return_pct
            FROM ranked_stocks AS r
            ORDER BY performance_rank, ticker
            LIMIT 20
            """
        ),
        {
            "start_date": start_date,
            "end_date": end_date,
            "include_synthetic": False,
        },
    ).mappings()
    performers = [dict(row) for row in rows]
    if not performers:
        raise HTTPException(status_code=404, detail="No performance data found")
    return performers


@app.get("/stocks/{ticker}/indicators", tags=["anomalies"])
def stock_indicators(
    ticker: TickerPath,
    connection: ConnectionDependency,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> list[dict]:
    normalized = normalize_ticker(ticker)
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    stock_exists = connection.execute(
        text("SELECT 1 FROM stocks WHERE ticker = :ticker AND is_synthetic = FALSE"),
        {"ticker": normalized},
    ).scalar_one_or_none()
    if stock_exists is None:
        raise HTTPException(status_code=404, detail="Real stock not found")
    rows = connection.execute(
        text(
            f"""
            WITH indicators AS ({INDICATOR_SQL.rstrip().rstrip(';')})
            SELECT i.ticker, i.price_date, i.daily_return, i.return_z_score,
                   i.volume, i.volume_ratio, i.historical_volume_mean,
                   i.volume_observations, i.volume_history_start,
                   i.rolling_volatility, i.volatility_expansion,
                   i.historical_volatility_mean,
                   i.historical_volatility_observations,
                   i.intraday_range, a.severity, a.explanation,
                   COALESCE(a.triggered_indicators, '[]'::JSONB) AS triggered_indicators,
                   COALESCE(a.detection_details, '[]'::JSONB) AS detection_details,
                   a.detection_method, a.detection_version AS model_version,
                   (a.id IS NOT NULL) AS is_anomaly
            FROM indicators i
            LEFT JOIN anomaly_results a
              ON a.ticker = i.ticker
             AND a.price_date = i.price_date
             AND a.detection_version = :version
            WHERE (CAST(:start_date AS DATE) IS NULL OR i.price_date >= :start_date)
              AND (CAST(:end_date AS DATE) IS NULL OR i.price_date <= :end_date)
            ORDER BY i.price_date
            """
        ),
        {
            "ticker": normalized,
            "return_history_days": RULES["return_history_days"],
            "volume_history_days": RULES["volume_history_days"],
            "volatility_days": RULES["volatility_days"],
            "volatility_baseline_days": RULES["volatility_baseline_days"],
            "version": ANOMALY_VERSION,
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings()
    return [dict(row) for row in rows]


@app.get("/stocks/{ticker}/models", tags=["anomalies"])
def stock_model_results(
    ticker: TickerPath,
    connection: ConnectionDependency,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
) -> dict:
    normalized = normalize_ticker(ticker)
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    stock_exists = connection.execute(
        text("SELECT 1 FROM stocks WHERE ticker = :ticker AND is_synthetic = FALSE"),
        {"ticker": normalized},
    ).scalar_one_or_none()
    if stock_exists is None:
        raise HTTPException(status_code=404, detail="Real stock not found")

    runs = connection.execute(
        text(
            """
            SELECT DISTINCT ON (method)
                   run_id, method, model_version, status, latest_price_date,
                   training_start, training_end, scoring_start, scoring_end,
                   training_observations, evaluation_observations, parameters,
                   message, processed_at
            FROM anomaly_model_runs
            WHERE ticker = :ticker AND model_version = :model_version
            ORDER BY method, processed_at DESC
            """
        ),
        {"ticker": normalized, "model_version": MODEL_VERSION},
    ).mappings().all()
    results = connection.execute(
        text(
            """
            SELECT price_date, method, model_version, anomaly_score, threshold,
                   classification, training_start, training_end, parameters,
                   features, scored_at
            FROM anomaly_model_results
            WHERE ticker = :ticker AND model_version = :model_version
              AND (CAST(:start_date AS DATE) IS NULL OR price_date >= :start_date)
              AND (CAST(:end_date AS DATE) IS NULL OR price_date <= :end_date)
            ORDER BY price_date, method
            """
        ),
        {
            "ticker": normalized,
            "model_version": MODEL_VERSION,
            "start_date": start_date,
            "end_date": end_date,
        },
    ).mappings().all()
    results_by_method: dict[str, list[dict]] = {}
    for result in results:
        results_by_method.setdefault(result["method"], []).append(dict(result))
    runs_by_method = {run["method"]: dict(run) for run in runs}
    methods = []
    for method in ("rolling_z_score", "relative_volume", "local_outlier_factor"):
        run = runs_by_method.get(method)
        methods.append(
            {
                "method": method,
                "settings": MODEL_RULES[method],
                "run": run,
                "results": results_by_method.get(method, []),
                "status": run["status"] if run else "NOT_RUN",
                "message": (
                    run["message"]
                    if run
                    else "Model has not been processed for the latest data."
                ),
            }
        )
    return {"ticker": normalized, "methods": methods}


@app.post("/stocks/{ticker}/models/reprocess", tags=["anomalies"])
def reprocess_stock_models(ticker: TickerPath) -> dict:
    normalized = normalize_ticker(ticker)
    result = process_ticker_models(normalized, force=True)
    if result["status"] == "NO_DATA":
        raise HTTPException(status_code=404, detail="Real stock data not found")
    return result
