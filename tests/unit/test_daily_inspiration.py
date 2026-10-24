"""SRA-1201 — deterministic, local daily inspiration contracts."""

import importlib
import importlib.util
from datetime import date, timedelta


def _inspiration_module():
    spec = importlib.util.find_spec("web.inspiration")
    assert spec is not None, "SRA-1201 must provide web.inspiration"
    return importlib.import_module("web.inspiration")


def _select(day: date) -> dict[str, str]:
    module = _inspiration_module()
    selector = getattr(module, "get_daily_inspiration", None)
    assert callable(selector), "web.inspiration.get_daily_inspiration(date) is required"
    item = selector(day)
    assert isinstance(item, dict)
    return item


def test_daily_inspiration_is_stable_for_the_same_local_date():
    local_day = date(2026, 10, 24)

    assert _select(local_day) == _select(local_day)


def test_adjacent_days_rotate_when_the_catalog_has_alternatives():
    first_day = date(2026, 10, 24)
    second_day = first_day + timedelta(days=1)

    assert _select(first_day)["quote"] != _select(second_day)["quote"]


def test_daily_inspiration_contains_quote_attribution_and_plain_language_meaning():
    item = _select(date(2026, 10, 24))

    assert set(item) >= {"quote", "attribution", "meaning", "provenance"}
    assert len(item["quote"].strip()) >= 12
    assert len(item["attribution"].strip()) >= 2
    assert len(item["meaning"].strip()) >= 30
    assert item["provenance"] in {"public_domain", "original"}


def test_curated_catalog_is_complete_unique_and_network_free():
    module = _inspiration_module()
    catalog = getattr(module, "INSPIRATIONS", None)

    assert isinstance(catalog, tuple)
    assert len(catalog) >= 7, "A weekly-sized catalog avoids repetitive daily visits"
    quotes = []
    for item in catalog:
        assert isinstance(item, dict)
        assert set(item) >= {"quote", "attribution", "meaning", "provenance"}
        assert item["quote"].strip()
        assert item["attribution"].strip()
        assert item["meaning"].strip()
        assert item["provenance"] in {"public_domain", "original"}
        assert "http://" not in item["quote"] and "https://" not in item["quote"]
        quotes.append(item["quote"].strip())
    assert len(quotes) == len(set(quotes))
