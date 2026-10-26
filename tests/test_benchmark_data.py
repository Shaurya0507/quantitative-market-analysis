from datetime import date

from scripts.generate_synthetic_data import price_record, trading_dates


def test_synthetic_trading_dates_are_ordered_weekdays():
    dates = trading_dates(8, date(2026, 10, 23))

    assert len(dates) == 8
    assert dates == sorted(dates)
    assert all(day.weekday() < 5 for day in dates)
    assert dates[-1] == date(2026, 10, 23)


def test_synthetic_price_records_are_deterministic_and_valid_ohlc():
    first = price_record(0, "SYN000", date(2026, 10, 23), 12)
    second = price_record(0, "SYN000", date(2026, 10, 23), 12)

    assert first == second
    assert first["ticker"] == "SYN000"
    assert first["high_price"] >= max(first["open_price"], first["close_price"])
    assert first["low_price"] <= min(first["open_price"], first["close_price"])
    assert first["volume"] >= 0
