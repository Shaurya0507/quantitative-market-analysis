import pytest

from scripts import ingest_stocks


def test_ingestion_runner_continues_after_one_ticker_fails(monkeypatch, capsys):
    requested = []

    def fake_ingest(ticker):
        requested.append(ticker)
        if ticker == "MSFT":
            raise RuntimeError("provider rate limit")
        return 100

    monkeypatch.setattr(ingest_stocks, "ingest_ticker", fake_ingest)
    monkeypatch.setattr(ingest_stocks.time, "sleep", lambda _: None)

    with pytest.raises(SystemExit) as exit_info:
        ingest_stocks.main()

    assert exit_info.value.code == 1
    assert requested == ["AAPL", "MSFT", "NVDA"]
    assert "AAPL: upserted 100 daily price records" in capsys.readouterr().out


def test_ingestion_runner_succeeds_when_all_tickers_succeed(monkeypatch, capsys):
    requested = []

    def fake_ingest(ticker):
        requested.append(ticker)
        return 100

    monkeypatch.setattr(ingest_stocks, "ingest_ticker", fake_ingest)
    monkeypatch.setattr(ingest_stocks.time, "sleep", lambda _: None)

    ingest_stocks.main()

    assert requested == ["AAPL", "MSFT", "NVDA"]
    assert capsys.readouterr().out.count("upserted 100") == 3
