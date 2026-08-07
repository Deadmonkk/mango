"""Tests for mango.providers.edgar — the SEC EDGAR API client.

All HTTP is faked (httpx.AsyncClient is monkeypatched); no test may touch the
network. AAA structure throughout.
"""

import json as _json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mango.providers import edgar

# --- helpers -----------------------------------------------------------


def _mock_response(text: str = "", json_data=None, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400 and status_code != 403:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


def _mock_client(handlers: list[tuple[str, MagicMock]]) -> AsyncMock:
    """An AsyncMock httpx.AsyncClient whose .get() dispatches by URL substring.

    Handlers are checked in order; the first substring match wins. Mirrors
    the dispatch pattern already used in tests/test_mango_fred.py.
    """

    async def _get(url, **kwargs):
        for substring, response in handlers:
            if substring in url:
                return response
        raise AssertionError(f"unexpected URL in test: {url}")

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _ticker_map_json() -> str:
    payload = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    }
    return _json.dumps(payload)


def _submissions_json(
    name: str = "NVIDIA CORP",
    forms: list[str] | None = None,
    filing_dates: list[str] | None = None,
    report_dates: list[str] | None = None,
    accession_numbers: list[str] | None = None,
    primary_documents: list[str] | None = None,
) -> str:
    payload = {
        "cik": "1045810",
        "name": name,
        "filings": {
            "recent": {
                "form": forms or [],
                "filingDate": filing_dates or [],
                "reportDate": report_dates or [],
                "accessionNumber": accession_numbers or [],
                "primaryDocument": primary_documents or [],
            }
        },
    }
    return _json.dumps(payload)


def _concept_json(tag: str, unit: str, rows: list[dict]) -> str:
    return _json.dumps({"cik": 1045810, "taxonomy": "us-gaap", "tag": tag, "units": {unit: rows}})


FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <issuer>
        <issuerCik>0001045810</issuerCik>
        <issuerName>NVIDIA CORP</issuerName>
        <issuerTradingSymbol>NVDA</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001197647</rptOwnerCik>
            <rptOwnerName>COXE TENCH</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>1</isDirector>
            <isOfficer>0</isOfficer>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionDate><value>2026-07-01</value></transactionDate>
            <transactionCoding>
                <transactionCode>G</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares><value>500000</value></transactionShares>
                <transactionPricePerShare><value>0</value></transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
            </transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeHolding>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>57378</value></sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeHolding>
    </nonDerivativeTable>
    <derivativeTable></derivativeTable>
</ownershipDocument>"""


FORM4_XML_OFFICER_BUY = """<?xml version="1.0"?>
<ownershipDocument>
    <issuer>
        <issuerTradingSymbol>NVDA</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerName>KRESS COLETTE</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isOfficer>1</isOfficer>
            <officerTitle>Executive VP and CFO</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionDate><value>2026-06-25</value></transactionDate>
            <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
            <transactionAmounts>
                <transactionShares><value>1211</value></transactionShares>
                <transactionPricePerShare><value>150.25</value></transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
            </transactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>"""


THIRTEENF_COVER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler">
  <formData>
    <summaryPage>
      <tableValueTotal>100000000</tableValueTotal>
    </summaryPage>
  </formData>
</edgarSubmission>"""


THIRTEENF_INFOTABLE_XML = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>AMERICAN EXPRESS CO</nameOfIssuer>
    <cusip>025816109</cusip>
    <value>70000000</value>
    <shrsOrPrnAmt><sshPrnamt>149061045</sshPrnamt></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>COCA COLA CO</nameOfIssuer>
    <cusip>191216100</cusip>
    <value>30000000</value>
    <shrsOrPrnAmt><sshPrnamt>282722729</sshPrnamt></shrsOrPrnAmt>
  </infoTable>
</informationTable>"""


THIRTEENF_INDEX_JSON = _json.dumps(
    {
        "directory": {
            "item": [
                {"name": "primary_doc.xml", "size": "5555"},
                {"name": "53405.xml", "size": "45259"},
            ]
        }
    }
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Redirect the shared file cache to a throwaway dir for every test."""
    from mango.core import cache

    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)


# --- ticker -> CIK resolution ------------------------------------------


async def test_resolve_cik_maps_known_ticker_zero_padded():
    # Arrange
    client = _mock_client([("company_tickers.json", _mock_response(text=_ticker_map_json()))])

    # Act
    cik = await edgar._resolve_cik(client, "AAPL")

    # Assert
    assert cik == "0000320193"


async def test_resolve_cik_is_case_insensitive():
    # Arrange
    client = _mock_client([("company_tickers.json", _mock_response(text=_ticker_map_json()))])

    # Act
    cik = await edgar._resolve_cik(client, "nvda")

    # Assert
    assert cik == "0001045810"


async def test_resolve_cik_returns_none_for_unknown_ticker():
    # Arrange
    client = _mock_client([("company_tickers.json", _mock_response(text=_ticker_map_json()))])

    # Act
    cik = await edgar._resolve_cik(client, "ZZZZZNOTREAL")

    # Assert
    assert cik is None


def test_zero_pad_cik_pads_int_and_str():
    assert edgar._zero_pad_cik(320193) == "0000320193"
    assert edgar._zero_pad_cik("320193") == "0000320193"
    assert edgar._zero_pad_cik("0000320193") == "0000320193"


# --- get_financials -------------------------------------------------------


async def test_get_financials_unknown_ticker_returns_error_dict():
    # Arrange
    client = _mock_client([("company_tickers.json", _mock_response(text=_ticker_map_json()))])

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_financials("NOTREAL", statement="income")

    # Assert
    assert "error" in result
    assert result["symbol"] == "NOTREAL"
    assert result["source"] == "edgar"


async def test_get_financials_unknown_statement_returns_error_without_network_call():
    # Act
    with patch.object(edgar.httpx, "AsyncClient") as client_cls:
        result = await edgar.get_financials("AAPL", statement="not_a_statement")

    # Assert
    assert "error" in result
    assert result["source"] == "edgar"
    client_cls.assert_not_called()


async def test_get_financials_income_statement_shape():
    # Arrange
    rows = [{"end": "2025-09-27", "val": 391000000, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01"}]
    handlers = [
        ("company_tickers.json", _mock_response(text=_ticker_map_json())),
        ("submissions/CIK0000320193.json", _mock_response(text=_submissions_json(name="Apple Inc."))),
        ("us-gaap/Revenues.json", _mock_response(text=_concept_json("Revenues", "USD", rows))),
        ("us-gaap/GrossProfit.json", _mock_response(text=_concept_json("GrossProfit", "USD", rows))),
        ("us-gaap/OperatingIncomeLoss.json", _mock_response(text=_concept_json("OperatingIncomeLoss", "USD", rows))),
        ("us-gaap/NetIncomeLoss.json", _mock_response(text=_concept_json("NetIncomeLoss", "USD", rows))),
        (
            "us-gaap/EarningsPerShareDiluted.json",
            _mock_response(text=_concept_json("EarningsPerShareDiluted", "USD/shares", rows)),
        ),
    ]
    client = _mock_client(handlers)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_financials("AAPL", statement="income", periods=4)

    # Assert
    assert result["symbol"] == "AAPL"
    assert result["company_name"] == "Apple Inc."
    assert result["statement"] == "income"
    assert result["source"] == "edgar"
    assert len(result["periods"]) == 1
    period = result["periods"][0]
    assert period["fiscal_year"] == 2025
    assert period["end_date"] == "2025-09-27"
    assert period["revenue"] == 391000000
    assert period["net_income"] == 391000000


async def test_get_financials_revenue_falls_back_to_alternate_tag():
    # Arrange: primary "Revenues" tag 404s; fallback tag has data.
    rows = [{"end": "2025-12-31", "val": 500, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-01"}]
    handlers = [
        ("company_tickers.json", _mock_response(text=_ticker_map_json())),
        ("submissions/CIK0000320193.json", _mock_response(text=_submissions_json(name="Apple Inc."))),
        ("us-gaap/Revenues.json", _mock_response(text="", status_code=404)),
        (
            "us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax.json",
            _mock_response(text=_concept_json("RevenueFromContractWithCustomerExcludingAssessedTax", "USD", rows)),
        ),
        ("us-gaap/GrossProfit.json", _mock_response(text="", status_code=404)),
        ("us-gaap/OperatingIncomeLoss.json", _mock_response(text="", status_code=404)),
        ("us-gaap/NetIncomeLoss.json", _mock_response(text="", status_code=404)),
        ("us-gaap/EarningsPerShareDiluted.json", _mock_response(text="", status_code=404)),
    ]
    client = _mock_client(handlers)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_financials("AAPL", statement="income")

    # Assert
    assert result["periods"][0]["revenue"] == 500


async def test_get_financials_no_annual_data_returns_error_dict():
    # Arrange: every concept tag 404s.
    handlers = [
        ("company_tickers.json", _mock_response(text=_ticker_map_json())),
        ("submissions/CIK0000320193.json", _mock_response(text=_submissions_json(name="Apple Inc."))),
    ] + [(f"us-gaap/{tag}.json", _mock_response(text="", status_code=404)) for tag in edgar.STATEMENT_TAGS["income"]["revenue"]] + [
        ("us-gaap/GrossProfit.json", _mock_response(text="", status_code=404)),
        ("us-gaap/OperatingIncomeLoss.json", _mock_response(text="", status_code=404)),
        ("us-gaap/NetIncomeLoss.json", _mock_response(text="", status_code=404)),
        ("us-gaap/EarningsPerShareDiluted.json", _mock_response(text="", status_code=404)),
    ]
    client = _mock_client(handlers)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_financials("AAPL", statement="income")

    # Assert
    assert "error" in result
    assert result["source"] == "edgar"


# --- get_filings -----------------------------------------------------------


async def test_get_filings_success_shape_filtered_by_type():
    # Arrange
    submissions = _submissions_json(
        name="NVIDIA CORP",
        forms=["10-K", "8-K", "10-K"],
        filing_dates=["2026-02-20", "2026-01-10", "2025-02-21"],
        report_dates=["2025-12-31", "", "2024-12-31"],
        accession_numbers=["0001-26-000001", "0001-26-000002", "0001-25-000003"],
        primary_documents=["nvda10k.htm", "nvda8k.htm", "nvda10k_prior.htm"],
    )
    handlers = [
        ("company_tickers.json", _mock_response(text=_ticker_map_json())),
        ("submissions/CIK0001045810.json", _mock_response(text=submissions)),
    ]
    client = _mock_client(handlers)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_filings("NVDA", filing_type="10-K", limit=10)

    # Assert
    assert result["symbol"] == "NVDA"
    assert result["company_name"] == "NVIDIA CORP"
    assert result["cik"] == "0001045810"
    assert len(result["filings"]) == 2
    assert all(f["form"] == "10-K" for f in result["filings"])
    assert result["filings"][0]["report_date"] == "2025-12-31"
    assert result["filings"][0]["url"].endswith("/nvda10k.htm")


async def test_get_filings_unknown_ticker_returns_error_dict():
    # Arrange
    client = _mock_client([("company_tickers.json", _mock_response(text=_ticker_map_json()))])

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_filings("NOTREAL")

    # Assert
    assert "error" in result
    assert result["source"] == "edgar"


# --- get_insider_transactions -----------------------------------------


async def test_get_insider_transactions_success_shape():
    # Arrange
    submissions = _submissions_json(
        name="NVIDIA CORP",
        forms=["4"],
        filing_dates=["2026-07-06"],
        accession_numbers=["0001197647-26-000005"],
        primary_documents=["xslF345X06/wk-form4_test.xml"],
    )
    handlers = [
        ("company_tickers.json", _mock_response(text=_ticker_map_json())),
        ("submissions/CIK0001045810.json", _mock_response(text=submissions)),
        ("wk-form4_test.xml", _mock_response(text=FORM4_XML)),
    ]
    client = _mock_client(handlers)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_insider_transactions("NVDA", limit=20)

    # Assert
    assert result["symbol"] == "NVDA"
    assert result["company_name"] == "NVIDIA CORP"
    assert result["source"] == "edgar"
    assert len(result["transactions"]) == 1
    tx = result["transactions"][0]
    assert tx["date"] == "2026-07-01"
    assert tx["owner"] == "COXE TENCH"
    assert tx["title"] == ""
    assert tx["transaction_type"] == "sell"
    assert tx["shares"] == 500000.0
    assert "summary" in result
    assert result["summary"]["total_sells"] == 500000.0


async def test_get_insider_transactions_excludes_holding_rows():
    # Arrange: FORM4_XML contains one nonDerivativeHolding row that must NOT
    # appear in transactions (it's a position, not a transaction).
    submissions = _submissions_json(
        forms=["4"],
        filing_dates=["2026-07-06"],
        accession_numbers=["0001197647-26-000005"],
        primary_documents=["xslF345X06/wk-form4_test.xml"],
    )
    handlers = [
        ("company_tickers.json", _mock_response(text=_ticker_map_json())),
        ("submissions/CIK0001045810.json", _mock_response(text=submissions)),
        ("wk-form4_test.xml", _mock_response(text=FORM4_XML)),
    ]
    client = _mock_client(handlers)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_insider_transactions("NVDA")

    # Assert
    assert len(result["transactions"]) == 1  # not 2 — the holding row is excluded


async def test_get_insider_transactions_zero_price_passes_through_unfilled():
    """A gift/award with no stated price parses to 0.0, never backfilled."""
    # Arrange
    submissions = _submissions_json(
        forms=["4"],
        filing_dates=["2026-07-06"],
        accession_numbers=["0001197647-26-000005"],
        primary_documents=["xslF345X06/wk-form4_test.xml"],
    )
    handlers = [
        ("company_tickers.json", _mock_response(text=_ticker_map_json())),
        ("submissions/CIK0001045810.json", _mock_response(text=submissions)),
        ("wk-form4_test.xml", _mock_response(text=FORM4_XML)),
    ]
    client = _mock_client(handlers)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_insider_transactions("NVDA")

    # Assert
    tx = result["transactions"][0]
    assert tx["price"] == 0.0
    assert tx["value"] == 0.0


async def test_get_insider_transactions_officer_title_and_buy_direction():
    # Arrange
    submissions = _submissions_json(
        forms=["4"],
        filing_dates=["2026-06-26"],
        accession_numbers=["0001197647-26-000004"],
        primary_documents=["xslF345X06/wk-form4_officer.xml"],
    )
    handlers = [
        ("company_tickers.json", _mock_response(text=_ticker_map_json())),
        ("submissions/CIK0001045810.json", _mock_response(text=submissions)),
        ("wk-form4_officer.xml", _mock_response(text=FORM4_XML_OFFICER_BUY)),
    ]
    client = _mock_client(handlers)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_insider_transactions("NVDA")

    # Assert
    tx = result["transactions"][0]
    assert tx["owner"] == "KRESS COLETTE"
    assert tx["title"] == "Executive VP and CFO"
    assert tx["transaction_type"] == "buy"
    assert tx["price"] == 150.25
    assert tx["value"] == round(1211 * 150.25, 2)


async def test_get_insider_transactions_unknown_ticker_returns_error_dict():
    # Arrange
    client = _mock_client([("company_tickers.json", _mock_response(text=_ticker_map_json()))])

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_insider_transactions("NOTREAL")

    # Assert
    assert "error" in result
    assert result["source"] == "edgar"


# --- get_13f_holdings -------------------------------------------------


async def test_get_13f_holdings_success_shape():
    # Arrange
    submissions = _submissions_json(
        name="BERKSHIRE HATHAWAY INC",
        forms=["13F-HR"],
        filing_dates=["2026-05-15"],
        accession_numbers=["0001193125-26-226661"],
        primary_documents=["xslForm13F_X02/primary_doc.xml"],
    )
    handlers = [
        ("submissions/CIK0001067983.json", _mock_response(text=submissions)),
        ("000119312526226661/index.json", _mock_response(text=THIRTEENF_INDEX_JSON)),
        ("primary_doc.xml", _mock_response(text=THIRTEENF_COVER_XML)),
        ("53405.xml", _mock_response(text=THIRTEENF_INFOTABLE_XML)),
    ]
    client = _mock_client(handlers)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_13f_holdings("berkshire", limit=20)

    # Assert
    assert result["institution"] == "berkshire"
    assert result["institution_name"] == "BERKSHIRE HATHAWAY INC"
    assert result["cik"] == "0001067983"
    assert result["report_date"] == "2026-05-15"  # filing date, not period end
    assert result["source"] == "edgar"
    assert len(result["holdings"]) == 2
    # sorted by value descending
    assert result["holdings"][0]["issuer"] == "AMERICAN EXPRESS CO"
    assert result["holdings"][0]["value_thousands_usd"] == 70000000
    assert result["holdings"][0]["shares"] == 149061045.0
    assert result["holdings"][0]["pct_of_portfolio"] == 70.0
    assert result["holdings"][1]["pct_of_portfolio"] == 30.0


async def test_get_13f_holdings_unknown_institution_returns_error_without_network_call():
    # Act
    with patch.object(edgar.httpx, "AsyncClient") as client_cls:
        result = await edgar.get_13f_holdings("some_random_fund")

    # Assert
    assert "error" in result
    assert result["source"] == "edgar"
    client_cls.assert_not_called()


async def test_get_13f_holdings_no_filing_found_returns_error_dict():
    # Arrange
    submissions = _submissions_json(name="BERKSHIRE HATHAWAY INC", forms=["10-K"], filing_dates=["2026-01-01"])
    client = _mock_client([("submissions/CIK0001067983.json", _mock_response(text=submissions))])

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_13f_holdings("berkshire")

    # Assert
    assert "error" in result
    assert result["institution"] == "berkshire"


def test_institution_cik_map_covers_required_keys():
    for key in ("berkshire", "bridgewater", "scion", "ark", "pershing_square"):
        assert key in edgar.INSTITUTION_CIK_MAP
        assert len(edgar.INSTITUTION_CIK_MAP[key]) == 10


# --- error convention: 403 / HTTP errors / malformed JSON -----------------


async def test_403_forbidden_returns_error_dict_explaining_user_agent():
    # Arrange
    client = _mock_client([("company_tickers.json", _mock_response(text="", status_code=403))])

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_financials("AAPL")

    # Assert
    assert "error" in result
    assert "User-Agent" in result["error"]
    assert result["source"] == "edgar"


async def test_http_error_returns_error_dict_not_raises():
    # Arrange
    client = _mock_client([("company_tickers.json", _mock_response(text="", status_code=500))])

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_filings("AAPL")

    # Assert
    assert "error" in result
    assert "500" in result["error"]
    assert result["source"] == "edgar"


async def test_connection_error_returns_error_dict_not_raises():
    # Arrange
    async def _boom(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_boom)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_filings("AAPL")

    # Assert
    assert "error" in result
    assert result["source"] == "edgar"


async def test_malformed_json_returns_error_dict_not_raises():
    # Arrange
    client = _mock_client([("company_tickers.json", _mock_response(text="not valid json{{"))])

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_filings("AAPL")

    # Assert
    assert "error" in result
    assert "Malformed JSON" in result["error"] or "error" in result
    assert result["source"] == "edgar"


async def test_error_message_never_leaks_query_shaped_secrets():
    """Sanity check: redact_text is applied to connection-error text."""
    # Arrange
    async def _boom(url, **kwargs):
        raise httpx.ConnectError("api_key=SUPERSECRETVALUE123 refused")

    client = AsyncMock()
    client.get = AsyncMock(side_effect=_boom)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    # Act
    with patch.object(edgar.httpx, "AsyncClient", return_value=client):
        result = await edgar.get_filings("AAPL")

    # Assert
    assert "SUPERSECRETVALUE123" not in result["error"]
