"""Client for SEC EDGAR's public filing APIs.

Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces

This is a clean-room implementation written directly from SEC's published API
documentation (and by inspecting live, public EDGAR responses), not from any
existing EDGAR client in this codebase family. Every public function is
defensive: providers in this stack return ``{"error": ...}`` payloads instead
of raising, matching the house convention already used by ``mango.core.fred``
and ``mango.providers.cftc``.

No API key is required for any EDGAR endpoint used here. What IS required is
a descriptive ``User-Agent`` header (a name plus contact info) on every
request — SEC's fair-access policy blocks the default/absent User-Agent most
HTTP clients send with a flat 403, regardless of which endpoint is hit. See
``USER_AGENT`` below.

Four public functions, each covering a different EDGAR data surface:

- ``get_financials``       — annual figures from a company's XBRL facts (10-K).
- ``get_filings``          — a company's recent filings list (submissions API).
- ``get_insider_transactions`` — Form 4 (beneficial ownership) transactions.
- ``get_13f_holdings``     — an institutional manager's latest 13F-HR holdings.

The latter two have no XBRL/JSON representation on EDGAR — Form 4 and 13F
information tables are only published as filing-specific XML documents, so
those two functions locate and parse that XML directly (see
``_parse_form4_xml`` / ``_parse_13f_info_table``).
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from mango.core import cache
from mango.core.limiter import RateLimiter
from mango.core.logging import get_logger
from mango.core.redact import redact_text

log = get_logger("edgar")

# --- API configuration ------------------------------------------------------

SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"
XBRL_CONCEPT_BASE_URL = "https://data.sec.gov/api/xbrl/companyconcept"
ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC requires every request identify the requester by name and contact
# method (email, URL, etc.) under its fair-access policy; a blank or generic
# User-Agent (including the default one httpx sends) draws an HTTP 403 on
# every endpoint below, not just the documented ones. Read from the
# environment so a real deployment supplies real contact details — the
# hardcoded fallback still names the tool but will start drawing 403s under
# sustained/production load since it carries no working contact address.
DEFAULT_USER_AGENT = "TerminalQ-Mango/1.0 (research tool; contact: set SEC_USER_AGENT)"
USER_AGENT: str = os.environ.get("SEC_USER_AGENT", DEFAULT_USER_AGENT)

# SEC's documented fair-access limit is "no more than 10 requests per second
# to any URL". Set at half that (5/sec = 300/min) so bursts from concurrent
# callers sharing this one process-wide limiter don't tip the *measured* rate
# over SEC's line even though the limiter's own accounting stays under it.
SEC_REQUESTS_PER_SECOND = 5
_limiter = RateLimiter(SEC_REQUESTS_PER_SECOND * 60)

REQUEST_TIMEOUT_SECONDS = 15.0

# --- Cache TTLs --------------------------------------------------------------
# The ticker->CIK map is republished by SEC roughly daily and changes rarely
# (new listings, ticker changes) — a full day's TTL avoids re-downloading a
# multi-megabyte file on every call while staying fresh enough for same-day
# new listings.
TICKER_MAP_CACHE_TTL_SECONDS = 86400
# A company's filing list only changes when it files something new — hours,
# not minutes, typically separate one filing from the next for most issuers.
SUBMISSIONS_CACHE_TTL_SECONDS = 21600
# XBRL facts are restated only on amendment (rare), so this can be cached
# generously without serving stale data in practice.
FACTS_CACHE_TTL_SECONDS = 21600
# Once filed, a specific filing's own document (a Form 4 XML, a 13F
# information table) never changes — it is an immutable historical record —
# so it is safe to cache for a long time.
FILING_DOCUMENT_CACHE_TTL_SECONDS = 604800  # 7 days

# --- Institution registry (13F filers) ---------------------------------------
# Friendly key -> zero-padded CIK. Verified 2026-08-07 against live EDGAR
# submissions data (`https://data.sec.gov/submissions/CIK##########.json`)
# for each CIK below — each returned the expected filer name. The display
# name shown to callers is *not* hardcoded here; it is read live from that
# same submissions document (see ``_company_name``), so it always matches
# SEC's own current record rather than a name baked in at write time.
INSTITUTION_CIK_MAP: dict[str, str] = {
    "berkshire": "0001067983",
    "bridgewater": "0001350694",
    "scion": "0001649339",
    "ark": "0001697748",
    "pershing_square": "0001336528",
}

# --- Financial statement tag maps (XBRL us-gaap concepts) --------------------
# Each metric lists candidate tags in priority order; different filers use
# different (semantically equivalent) tags for the same line item, most
# commonly for revenue. The first tag that has any annual (10-K) data wins.
STATEMENT_TAGS: dict[str, dict[str, tuple[str, ...]]] = {
    "income": {
        "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        "gross_profit": ("GrossProfit",),
        "operating_income": ("OperatingIncomeLoss",),
        "net_income": ("NetIncomeLoss",),
        "eps_diluted": ("EarningsPerShareDiluted",),
    },
    "balance": {
        "total_assets": ("Assets",),
        "total_liabilities": ("Liabilities",),
        "stockholders_equity": ("StockholdersEquity",),
        "cash_and_equivalents": (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
        ),
        "long_term_debt": ("LongTermDebtNoncurrent",),
    },
    "cash": {
        "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
        "investing_cash_flow": ("NetCashProvidedByUsedInInvestingActivities",),
        "financing_cash_flow": ("NetCashProvidedByUsedInFinancingActivities",),
        "capital_expenditures": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    },
}


class EdgarRequestError(Exception):
    """Internal signal that an EDGAR HTTP call failed.

    Always caught at the boundary of each public function and converted to
    the standard ``{"error": ...}`` payload — never allowed to propagate out
    of this module.
    """


# --- low-level HTTP -----------------------------------------------------


def _request_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT}


async def _request_json(client: httpx.AsyncClient, url: str, params: dict | None = None) -> Any:
    """GET a URL and parse it as JSON, or raise EdgarRequestError."""
    text = await _request_text(client, url, params)
    try:
        import json

        return json.loads(text)
    except ValueError as exc:
        raise EdgarRequestError(f"Malformed JSON from {url}: {exc}") from exc


async def _request_text(client: httpx.AsyncClient, url: str, params: dict | None = None) -> str:
    """GET a URL and return its raw response body, or raise EdgarRequestError.

    The 403 case is special-cased with an explanatory message: on EDGAR that
    status overwhelmingly means "no/bad User-Agent" rather than "resource
    forbidden", and a caller staring at a bare "HTTP 403" has no way to know
    that without already knowing this API's quirks.
    """
    await _limiter.acquire()
    try:
        response = await client.get(url, params=params, headers=_request_headers(), timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.TimeoutException as exc:
        raise EdgarRequestError(f"Request to {url} timed out") from exc
    except httpx.RequestError as exc:
        raise EdgarRequestError(f"Connection to {url} failed: {redact_text(str(exc))}") from exc

    if response.status_code == 403:
        raise EdgarRequestError(
            "SEC EDGAR returned 403 Forbidden. This endpoint requires a descriptive "
            "User-Agent header (name + contact info) per SEC's fair-access policy — "
            "set the SEC_USER_AGENT environment variable to a real identifying value "
            "(e.g. 'YourApp/1.0 (contact@yourdomain.com)')."
        )

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise EdgarRequestError(f"HTTP {exc.response.status_code} from {url}") from exc

    return response.text


def _strip_namespaces(root: ET.Element) -> ET.Element:
    """Drop XML namespace prefixes from every tag in-place, then return root.

    SEC's 13F information-table and cover-page XML documents declare a
    default namespace; Form 4 ownership documents do not. Stripping
    namespaces unconditionally lets every caller use plain, unprefixed
    ``.find()``/``.findtext()`` paths regardless of which document it is.
    """
    for element in root.iter():
        if "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
    return root


# --- ticker -> CIK resolution ---------------------------------------------


def _zero_pad_cik(cik: int | str) -> str:
    """CIKs must be zero-padded to 10 digits for the submissions/XBRL endpoints."""
    return str(cik).zfill(10)


async def _load_ticker_map(client: httpx.AsyncClient) -> dict[str, str]:
    cache_key = "edgar_ticker_cik_map"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    payload = await _request_json(client, TICKER_MAP_URL)
    # Payload shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "..."}, ...}
    mapping = {
        str(row["ticker"]).upper(): _zero_pad_cik(row["cik_str"])
        for row in payload.values()
        if isinstance(row, dict) and "ticker" in row and "cik_str" in row
    }
    cache.set(cache_key, mapping, TICKER_MAP_CACHE_TTL_SECONDS)
    return mapping


async def _resolve_cik(client: httpx.AsyncClient, symbol: str) -> str | None:
    mapping = await _load_ticker_map(client)
    return mapping.get(symbol.upper())


# --- submissions (filing list + company name) -------------------------------


async def _get_submissions(client: httpx.AsyncClient, cik10: str) -> dict:
    cache_key = f"edgar_submissions_{cik10}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    url = f"{SUBMISSIONS_BASE_URL}/CIK{cik10}.json"
    payload = await _request_json(client, url)
    cache.set(cache_key, payload, SUBMISSIONS_CACHE_TTL_SECONDS)
    return payload


async def _company_name(client: httpx.AsyncClient, cik10: str) -> str:
    submissions = await _get_submissions(client, cik10)
    return submissions.get("name", "")


def _filing_document_url(cik10: str, accession_number: str, document: str) -> str:
    cik_int = str(int(cik10))
    accession_nodash = accession_number.replace("-", "")
    return f"{ARCHIVES_BASE_URL}/{cik_int}/{accession_nodash}/{document}"


def _filing_index_url(cik10: str, accession_number: str) -> str:
    cik_int = str(int(cik10))
    accession_nodash = accession_number.replace("-", "")
    return f"{ARCHIVES_BASE_URL}/{cik_int}/{accession_nodash}/index.json"


# =============================================================================
# get_financials
# =============================================================================


async def _fetch_concept_series(client: httpx.AsyncClient, cik10: str, tag: str) -> dict[int, dict]:
    """Fetch one XBRL us-gaap concept, keyed by fiscal year, annual (10-K) only.

    Returns ``{fiscal_year: {"val", "end", "form", "filed"}}``. When a filer
    amends a 10-K, more than one row can share a fiscal year; the row with
    the latest ``filed`` date wins so amendments supersede originals.
    """
    cache_key = f"edgar_concept_{cik10}_{tag}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {int(fy): entry for fy, entry in cached.items()}

    url = f"{XBRL_CONCEPT_BASE_URL}/CIK{cik10}/us-gaap/{tag}.json"
    payload = await _request_json(client, url)

    units = payload.get("units", {}) if isinstance(payload, dict) else {}
    # Prefer whichever unit key has the most datapoints (USD for dollar
    # tags, USD/shares for EPS, etc.) instead of hardcoding one unit string.
    unit_key = max(units, key=lambda k: len(units[k])) if units else None

    by_fiscal_year: dict[int, dict] = {}
    if unit_key is not None:
        for row in units[unit_key]:
            if row.get("fp") != "FY" or not str(row.get("form", "")).startswith("10-K"):
                continue
            fiscal_year = row.get("fy")
            if fiscal_year is None:
                continue
            existing = by_fiscal_year.get(fiscal_year)
            if existing is None or str(row.get("filed", "")) >= str(existing.get("filed", "")):
                by_fiscal_year[fiscal_year] = {
                    "val": row.get("val"),
                    "end": row.get("end"),
                    "form": row.get("form"),
                    "filed": row.get("filed"),
                }

    cache.set(cache_key, by_fiscal_year, FACTS_CACHE_TTL_SECONDS)
    return by_fiscal_year


async def _fetch_metric_series(client: httpx.AsyncClient, cik10: str, tags: tuple[str, ...]) -> dict[int, dict]:
    """Try each candidate tag in order; return the first with any data.

    A 404 for one tag (a filer that never reported that concept) is a
    completely normal outcome, not a failure — it is swallowed here so one
    missing tag never fails the whole financials call.
    """
    for tag in tags:
        try:
            series = await _fetch_concept_series(client, cik10, tag)
        except EdgarRequestError as exc:
            log.debug("EDGAR concept %s unavailable for CIK %s: %s", tag, cik10, exc)
            continue
        if series:
            return series
    return {}


async def get_financials(symbol: str, statement: str = "income", periods: int = 4) -> dict:
    """Annual financial-statement figures for ``symbol``, from SEC XBRL facts.

    ``statement`` is one of "income", "balance", "cash". Returns up to
    ``periods`` most recent fiscal years, newest first::

        {
          "symbol": "AAPL",
          "company_name": "...",
          "statement": "income",
          "periods": [
            {"fiscal_year": 2025, "end_date": "2025-09-27", "revenue": ..., ...},
            ...
          ],
          "source": "edgar",
        }

    No saved real-world payload existed to derive this shape from (unlike
    ``get_insider_transactions``/``get_13f_holdings``), so it is designed to
    be consistent with them: same ``symbol``/``company_name``/``source``
    envelope, a flat list of period dicts. Never raises.
    """
    statement_key = statement.lower()
    tag_map = STATEMENT_TAGS.get(statement_key)
    if tag_map is None:
        return {
            "error": f"Unknown statement '{statement}'. Valid options: {', '.join(sorted(STATEMENT_TAGS))}",
            "symbol": symbol,
            "source": "edgar",
        }

    async with httpx.AsyncClient() as client:
        try:
            cik = await _resolve_cik(client, symbol)
            if cik is None:
                return {
                    "error": f"Unknown ticker '{symbol}' — not found in SEC's ticker map",
                    "symbol": symbol,
                    "source": "edgar",
                }

            company_name = await _company_name(client, cik)

            metrics_by_key: dict[str, dict[int, dict]] = {}
            for metric_key, tags in tag_map.items():
                metrics_by_key[metric_key] = await _fetch_metric_series(client, cik, tags)
        except EdgarRequestError as exc:
            log.warning("EDGAR get_financials failed for %s: %s", symbol, exc)
            return {"error": str(exc), "symbol": symbol, "source": "edgar"}

    non_empty = {k: v for k, v in metrics_by_key.items() if v}
    if not non_empty:
        return {
            "error": f"No annual XBRL data found for {symbol} ({statement_key})",
            "symbol": symbol,
            "source": "edgar",
        }

    # Anchor the list of fiscal years on whichever metric has the broadest
    # coverage — different tags can have gaps for the same filer.
    primary_key = max(non_empty, key=lambda k: len(non_empty[k]))
    fiscal_years = sorted(non_empty[primary_key].keys(), reverse=True)[:periods]

    result_periods = []
    for fiscal_year in fiscal_years:
        period_entry: dict[str, Any] = {"fiscal_year": fiscal_year}
        end_date = None
        for metric_key, series in metrics_by_key.items():
            entry = series.get(fiscal_year)
            period_entry[metric_key] = entry["val"] if entry else None
            if entry is not None and end_date is None:
                end_date = entry["end"]
        period_entry["end_date"] = end_date
        result_periods.append(period_entry)

    return {
        "symbol": symbol.upper(),
        "company_name": company_name,
        "statement": statement_key,
        "periods": result_periods,
        "source": "edgar",
    }


# =============================================================================
# get_filings
# =============================================================================


async def get_filings(symbol: str, filing_type: str = "", limit: int = 10) -> dict:
    """A company's recent SEC filings, newest first.

    ``filing_type`` filters to an exact form match (case-insensitive, e.g.
    "10-K", "8-K", "4"); empty returns all forms. Returns::

        {
          "symbol": "AAPL",
          "company_name": "...",
          "cik": "0000320193",
          "filings": [
            {"form": "10-K", "filing_date": "...", "report_date": "...",
             "accession_number": "...", "primary_document": "...", "url": "..."},
            ...
          ],
          "source": "edgar",
        }

    Never raises.
    """
    async with httpx.AsyncClient() as client:
        try:
            cik = await _resolve_cik(client, symbol)
            if cik is None:
                return {
                    "error": f"Unknown ticker '{symbol}' — not found in SEC's ticker map",
                    "symbol": symbol,
                    "source": "edgar",
                }
            submissions = await _get_submissions(client, cik)
        except EdgarRequestError as exc:
            log.warning("EDGAR get_filings failed for %s: %s", symbol, exc)
            return {"error": str(exc), "symbol": symbol, "source": "edgar"}

    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])

    type_filter = filing_type.strip().upper()
    filings: list[dict] = []
    for i, form in enumerate(forms):
        if type_filter and form.upper() != type_filter:
            continue

        accession = accession_numbers[i] if i < len(accession_numbers) else ""
        primary_document = primary_documents[i] if i < len(primary_documents) else ""
        report_date = report_dates[i] if i < len(report_dates) else ""

        filings.append(
            {
                "form": form,
                "filing_date": filing_dates[i] if i < len(filing_dates) else None,
                "report_date": report_date or None,
                "accession_number": accession,
                "primary_document": primary_document,
                "url": _filing_document_url(cik, accession, primary_document)
                if accession and primary_document
                else None,
            }
        )
        if len(filings) >= limit:
            break

    return {
        "symbol": symbol.upper(),
        "company_name": submissions.get("name", ""),
        "cik": cik,
        "filings": filings,
        "source": "edgar",
    }


# =============================================================================
# get_insider_transactions (Form 4)
# =============================================================================

# Form 4's transactionAcquiredDisposedCode is the authoritative buy/sell
# signal — "A" means the reporting owner acquired the securities, "D" means
# disposed. (The transactionCode letter, e.g. "P"/"S"/"G"/"A", denotes *why*
# — open-market purchase, sale, gift, award — not direction; a gift is coded
# "G" but is still a disposal, and mapping off acquired/disposed rather than
# the reason code is what makes that come out right.)
_ACQUIRED_DISPOSED_TO_DIRECTION = {"A": "buy", "D": "sell"}


def _form4_transaction_rows(root: ET.Element) -> list[ET.Element]:
    """Every *Transaction row (non-derivative and derivative), in document order.

    Deliberately excludes *Holding rows (``nonDerivativeHolding`` /
    ``derivativeHolding``) — those report a position the owner already held,
    not a transaction that happened, and mixing them in would silently
    inflate the transaction list with non-events.
    """
    rows: list[ET.Element] = []
    non_derivative_table = root.find("nonDerivativeTable")
    if non_derivative_table is not None:
        rows.extend(non_derivative_table.findall("nonDerivativeTransaction"))
    derivative_table = root.find("derivativeTable")
    if derivative_table is not None:
        rows.extend(derivative_table.findall("derivativeTransaction"))
    return rows


def _form4_owner_and_title(root: ET.Element) -> tuple[str, str]:
    owner_elem = root.find("reportingOwner/reportingOwnerId/rptOwnerName")
    owner = owner_elem.text.strip() if owner_elem is not None and owner_elem.text else ""
    title_elem = root.find("reportingOwner/reportingOwnerRelationship/officerTitle")
    title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
    return owner, title


def _parse_float(text: str | None) -> float:
    if text is None:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_form4_xml(xml_text: str) -> dict:
    """Parse a Form 4 ownership XML document into transaction rows.

    Returns ``{"transactions": [...], "issuer_symbol": "..."}``. A row whose
    price the filer left blank (common for gifts/awards, which have no
    market price) parses to ``0.0`` and is passed through as-is — that zero
    reflects what the filing actually says, not a computed or assumed value,
    so it is never backfilled or estimated here.
    """
    root = _strip_namespaces(ET.fromstring(xml_text))

    owner, title = _form4_owner_and_title(root)
    issuer_symbol_elem = root.find("issuer/issuerTradingSymbol")
    issuer_symbol = issuer_symbol_elem.text.strip() if issuer_symbol_elem is not None and issuer_symbol_elem.text else ""

    transactions = []
    for row in _form4_transaction_rows(root):
        date_elem = row.find("transactionDate/value")
        code_elem = row.find("transactionCoding/transactionCode")
        shares_elem = row.find("transactionAmounts/transactionShares/value")
        price_elem = row.find("transactionAmounts/transactionPricePerShare/value")
        direction_elem = row.find("transactionAmounts/transactionAcquiredDisposedCode/value")

        shares = _parse_float(shares_elem.text if shares_elem is not None else None)
        price = _parse_float(price_elem.text if price_elem is not None else None)
        direction_code = direction_elem.text.strip() if direction_elem is not None and direction_elem.text else ""
        transaction_code = code_elem.text.strip() if code_elem is not None and code_elem.text else ""

        transactions.append(
            {
                "date": date_elem.text.strip() if date_elem is not None and date_elem.text else None,
                "owner": owner,
                "title": title,
                "transaction_type": _ACQUIRED_DISPOSED_TO_DIRECTION.get(direction_code, transaction_code.lower()),
                "shares": shares,
                "price": price,
                "value": round(shares * price, 2),
            }
        )

    return {"transactions": transactions, "issuer_symbol": issuer_symbol}


async def _fetch_form4_transactions(client: httpx.AsyncClient, cik10: str, accession: str, primary_document: str) -> list[dict]:
    """Fetch and parse one Form 4 filing's raw ownership XML.

    ``primaryDocument`` in the submissions feed points at the human-readable,
    XSLT-rendered copy (e.g. ``xslF345X06/wk-form4_....xml`` — an HTML
    document despite the ``.xml`` extension). The machine-readable source XML
    sits at the same accession root under just the basename (verified 2026-08-07
    against live EDGAR filings), so that prefix is stripped before fetching.
    """
    raw_document = primary_document.rsplit("/", 1)[-1]
    cache_key = f"edgar_form4_{cik10}_{accession}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached.get("transactions", [])

    url = _filing_document_url(cik10, accession, raw_document)
    xml_text = await _request_text(client, url)
    try:
        parsed = _parse_form4_xml(xml_text)
    except ET.ParseError as exc:
        log.warning("EDGAR Form 4 XML unparseable for accession %s: %s", accession, exc)
        return []

    cache.set(cache_key, parsed, FILING_DOCUMENT_CACHE_TTL_SECONDS)
    return parsed["transactions"]


async def get_insider_transactions(symbol: str, limit: int = 20) -> dict:
    """Recent Form 4 (insider) transactions for ``symbol``, newest filings first.

    Walks the company's Form 4 filings (newest first) and accumulates
    transactions across as many filings as needed to reach ``limit``, since
    one filing can carry zero (holdings-only) to several transaction rows.
    Returns::

        {
          "symbol": "NVDA",
          "company_name": "NVIDIA CORP",
          "transactions": [
            {"date", "owner", "title", "transaction_type", "shares", "price", "value"},
            ...
          ],
          "summary": {"total_buys", "total_sells", "net_shares", "net_value"},
          "source": "edgar",
        }

    Shape derived from a genuine saved payload
    (``insider_transactions_NVDA`` in a TerminalQ FR run's raw audit trail).
    Never raises.
    """
    async with httpx.AsyncClient() as client:
        try:
            cik = await _resolve_cik(client, symbol)
            if cik is None:
                return {
                    "error": f"Unknown ticker '{symbol}' — not found in SEC's ticker map",
                    "symbol": symbol,
                    "source": "edgar",
                }
            submissions = await _get_submissions(client, cik)
            company_name = submissions.get("name", "")

            recent = submissions.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accession_numbers = recent.get("accessionNumber", [])
            primary_documents = recent.get("primaryDocument", [])

            transactions: list[dict] = []
            for i, form in enumerate(forms):
                if form != "4":
                    continue
                accession = accession_numbers[i] if i < len(accession_numbers) else ""
                primary_document = primary_documents[i] if i < len(primary_documents) else ""
                if not accession or not primary_document:
                    continue

                filing_transactions = await _fetch_form4_transactions(client, cik, accession, primary_document)
                transactions.extend(filing_transactions)
                if len(transactions) >= limit:
                    break
        except EdgarRequestError as exc:
            log.warning("EDGAR get_insider_transactions failed for %s: %s", symbol, exc)
            return {"error": str(exc), "symbol": symbol, "source": "edgar"}

    transactions = transactions[:limit]

    total_buys = sum(t["shares"] for t in transactions if t["transaction_type"] == "buy")
    total_sells = sum(t["shares"] for t in transactions if t["transaction_type"] == "sell")
    net_value = sum(
        t["value"] if t["transaction_type"] == "buy" else -t["value"]
        for t in transactions
        if t["transaction_type"] in ("buy", "sell")
    )

    return {
        "symbol": symbol.upper(),
        "company_name": company_name,
        "transactions": transactions,
        "summary": {
            "total_buys": total_buys,
            "total_sells": total_sells,
            "net_shares": total_buys - total_sells,
            "net_value": round(net_value, 2),
        },
        "source": "edgar",
    }


# =============================================================================
# get_13f_holdings
# =============================================================================


def _pick_info_table_document(index_items: list[dict]) -> str | None:
    """Identify the 13F information-table XML among a filing's documents.

    A 13F-HR filing publishes (at minimum) two XML documents: a small cover
    page (conventionally named ``primary_doc.xml``, a few KB) and the actual
    holdings table (an arbitrary filer-chosen filename, typically tens of
    KB). There is no fixed filename for the latter, so it is identified by
    exclusion + size: every ``.xml`` file that is not the primary/cover doc,
    largest first.
    """
    candidates = [
        item
        for item in index_items
        if str(item.get("name", "")).lower().endswith(".xml") and "primary_doc" not in str(item.get("name", "")).lower()
    ]
    if not candidates:
        return None

    def _size(item: dict) -> int:
        try:
            return int(item.get("size") or 0)
        except (TypeError, ValueError):
            return 0

    candidates.sort(key=_size, reverse=True)
    return candidates[0]["name"]


def _parse_13f_cover_page(xml_text: str) -> dict:
    """Extract the total portfolio value from a 13F cover page (primary_doc.xml).

    ``tableValueTotal`` is the filing's own declared total across every
    holding row — used as the denominator for each holding's
    percent-of-portfolio share, rather than summing only the (possibly
    truncated-by-``limit``) rows returned to the caller.
    """
    root = _strip_namespaces(ET.fromstring(xml_text))
    total_elem = root.find("formData/summaryPage/tableValueTotal")
    total = _parse_float(total_elem.text if total_elem is not None else None)
    return {"table_value_total": total}


def _parse_13f_info_table(xml_text: str) -> list[dict]:
    """Parse a 13F information-table XML into raw (issuer, cusip, value, shares) rows.

    ``value`` is passed through exactly as SEC reports it — post the SEC's
    rule change (effective 2023) filers report this field in whole dollars,
    not the historical thousands-of-dollars convention the field name
    ``value_thousands_usd`` still carries for shape-compatibility with the
    existing downstream contract. This function does not attempt any
    thousands<->dollars conversion; it is not this module's place to correct
    a naming legacy it did not create, and doing so silently would make the
    number wrong relative to every existing caller of that field.
    """
    root = _strip_namespaces(ET.fromstring(xml_text))
    rows = []
    for info_table in root.findall(".//infoTable"):
        issuer_elem = info_table.find("nameOfIssuer")
        cusip_elem = info_table.find("cusip")
        value_elem = info_table.find("value")
        shares_elem = info_table.find("shrsOrPrnAmt/sshPrnamt")
        rows.append(
            {
                "issuer": issuer_elem.text.strip() if issuer_elem is not None and issuer_elem.text else "",
                "cusip": cusip_elem.text.strip() if cusip_elem is not None and cusip_elem.text else "",
                "value_thousands_usd": _parse_float(value_elem.text if value_elem is not None else None),
                "shares": _parse_float(shares_elem.text if shares_elem is not None else None),
            }
        )
    return rows


async def get_13f_holdings(institution: str, limit: int = 20) -> dict:
    """An institutional manager's holdings from its most recent 13F-HR filing.

    ``institution`` is a friendly key from ``INSTITUTION_CIK_MAP`` (berkshire,
    bridgewater, scion, ark, pershing_square). Returns::

        {
          "institution": "berkshire",
          "institution_name": "BERKSHIRE HATHAWAY INC",
          "cik": "0001067983",
          "report_date": "2026-05-15",
          "holdings": [
            {"issuer", "cusip", "value_thousands_usd", "shares", "pct_of_portfolio"},
            ...
          ],
          "source": "edgar",
        }

    ``report_date`` is the filing's *filing* date, not the holdings period
    end date — matching the shape of a genuine saved payload
    (``13f_holdings_berkshire`` in a TerminalQ FR run's raw audit trail),
    which carries the filing date under that key. Holdings are sorted by
    value, largest first, and truncated to ``limit``; ``pct_of_portfolio`` is
    computed against the filing's own declared *total* portfolio value, so it
    stays correct even when ``limit`` truncates the list. Never raises.
    """
    institution_key = institution.lower()
    cik = INSTITUTION_CIK_MAP.get(institution_key)
    if cik is None:
        return {
            "error": f"Unknown institution '{institution}'. Valid options: {', '.join(sorted(INSTITUTION_CIK_MAP))}",
            "source": "edgar",
        }

    async with httpx.AsyncClient() as client:
        try:
            submissions = await _get_submissions(client, cik)
            institution_name = submissions.get("name", "")

            recent = submissions.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            filing_dates = recent.get("filingDate", [])
            accession_numbers = recent.get("accessionNumber", [])

            latest_index = next((i for i, form in enumerate(forms) if form == "13F-HR"), None)
            if latest_index is None:
                return {
                    "error": f"No 13F-HR filing found for '{institution}'",
                    "institution": institution_key,
                    "source": "edgar",
                }

            accession = accession_numbers[latest_index]
            filing_date = filing_dates[latest_index] if latest_index < len(filing_dates) else None

            index_payload = await _request_json(client, _filing_index_url(cik, accession))
            index_items = index_payload.get("directory", {}).get("item", [])

            cover_doc = next(
                (item["name"] for item in index_items if "primary_doc" in str(item.get("name", "")).lower()),
                "primary_doc.xml",
            )
            info_table_doc = _pick_info_table_document(index_items)
            if info_table_doc is None:
                return {
                    "error": f"No information-table document found in 13F-HR filing {accession}",
                    "institution": institution_key,
                    "source": "edgar",
                }

            cover_xml = await _request_text(client, _filing_document_url(cik, accession, cover_doc))
            info_table_xml = await _request_text(client, _filing_document_url(cik, accession, info_table_doc))
        except EdgarRequestError as exc:
            log.warning("EDGAR get_13f_holdings failed for %s: %s", institution, exc)
            return {"error": str(exc), "institution": institution_key, "source": "edgar"}

    try:
        table_value_total = _parse_13f_cover_page(cover_xml)["table_value_total"]
        raw_holdings = _parse_13f_info_table(info_table_xml)
    except ET.ParseError as exc:
        return {
            "error": f"13F XML unparseable for accession {accession}: {exc}",
            "institution": institution_key,
            "source": "edgar",
        }

    raw_holdings.sort(key=lambda h: h["value_thousands_usd"], reverse=True)
    holdings = []
    for holding in raw_holdings[:limit]:
        pct = round(holding["value_thousands_usd"] / table_value_total * 100, 2) if table_value_total else None
        holdings.append({**holding, "pct_of_portfolio": pct})

    return {
        "institution": institution_key,
        "institution_name": institution_name,
        "cik": cik,
        "report_date": filing_date,
        "holdings": holdings,
        "source": "edgar",
    }
