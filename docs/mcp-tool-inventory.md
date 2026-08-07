# MCP tool inventory

The 84 tools the replacement server must expose, with their signatures and
the provider call each makes. Extracted mechanically from the running
server's AST — signatures and names only, no implementation text.

Tool NAMES are kept identical on purpose: they are functional identifiers
referenced by `~/.claude.json` and by the FR/EOD report playbooks. Renaming
them would break those. Docstrings must be written fresh.

```
terminalq_get_quote(symbol: str)
    calls: finnhub.get_quote
terminalq_get_quotes_batch(symbols: str)
    calls: finnhub.get_quotes_batch
terminalq_get_portfolio()
    calls: (composes other tools / pure)
terminalq_get_portfolio_live()
    calls: finnhub.get_quotes_batch
terminalq_get_company_profile(symbol: str)
    calls: finnhub.get_company_profile
terminalq_get_news(symbol: str, days: int = 7)
    calls: finnhub.get_company_news
terminalq_get_rsu_schedule()
    calls: portfolio.load_rsu_schedule
terminalq_get_earnings(symbol: str)
    calls: finnhub.get_earnings
terminalq_get_historical(symbol: str, period: str = '1y', interval: str = '1d')
    calls: historical.get_historical
terminalq_get_dividends(symbol: str, years: int = 5)
    calls: historical.get_dividends
terminalq_get_financials(symbol: str, statement: str = 'income', periods: int = 4)
    calls: edgar.get_financials
terminalq_get_filings(symbol: str, filing_type: str = '', limit: int = 10)
    calls: edgar.get_filings
terminalq_get_insider_transactions(symbol: str, limit: int = 10)
    calls: edgar.get_insider_transactions
terminalq_get_13f_holdings(institution: str, limit: int = 20)
    calls: edgar.get_13f_holdings, institution.lower
terminalq_get_economic_indicator(indicator: str, limit: int = 12)
    calls: fred.get_series
terminalq_get_macro_dashboard()
    calls: fred.get_economic_dashboard
terminalq_get_technicals(symbol: str)
    calls: technical.get_full_technicals
terminalq_screen_stocks(sector: str = '', min_market_cap: float = 0, max_market_cap: float = 0, limit: int = 20)
    calls: screener.screen_stocks
terminalq_chart_price(symbol: str, period: str = '6mo', chart_type: str = 'line')
    calls: charts.candlestick_chart, charts.line_chart
terminalq_chart_comparison(symbols: str, period: str = '1y')
    calls: charts.comparison_chart, historical.get_historical
terminalq_chart_allocation()
    calls: allocation.compute_allocation
terminalq_chart_yield_curve()
    calls: charts.yield_curve_chart, fred.get_series
terminalq_chart_sector_heatmap()
    calls: charts.heatmap, historical.get_historical
terminalq_get_analyst_ratings(symbol: str)
    calls: finnhub.get_analyst_ratings
terminalq_get_watchlist()
    calls: finnhub.get_quotes_batch, portfolio.load_watchlist
terminalq_get_forex(pair: str = '')
    calls: fred.FOREX_SERIES_MAP, fred.get_forex
terminalq_get_crypto(symbol: str)
    calls: coingecko.get_crypto_quote
terminalq_get_crypto_batch(symbols: str)
    calls: coingecko.get_crypto_batch
terminalq_get_economic_calendar(days: int = 7)
    calls: finnhub.get_economic_calendar
terminalq_web_search(query: str, count: int = 5)
    calls: search.web_search
terminalq_get_risk_metrics(period: str = '1y')
    calls: risk.compute_portfolio_risk
terminalq_get_allocation()
    calls: allocation.compute_allocation
terminalq_get_audit_log(date: str = '')
    calls: audit.get_audit_log, audit.get_audit_summary
terminalq_get_usage_stats()
    calls: audit.get_audit_summary
terminalq_get_jolts()
    calls: fred.get_jolts_dashboard
terminalq_get_credit_spreads()
    calls: fred.get_credit_spreads_dashboard
terminalq_get_consumer_health()
    calls: fred.get_consumer_health_dashboard
terminalq_get_fiscal_health()
    calls: fred.get_fiscal_dashboard
terminalq_get_commodities()
    calls: fred.get_commodities_dashboard
terminalq_get_cpi_components()
    calls: fred.get_cpi_components_dashboard
terminalq_get_liquidity()
    calls: fred.get_liquidity_dashboard
terminalq_get_rates_dashboard()
    calls: fred.get_rates_dashboard
terminalq_get_market_overview()
    calls: market_data.get_market_overview
terminalq_get_international_markets()
    calls: market_data.get_international_markets
terminalq_get_style_box()
    calls: market_data.get_style_box
terminalq_get_asset_class_returns()
    calls: market_data.get_asset_class_returns
terminalq_get_stock_fundamentals(symbol: str)
    calls: market_data.get_stock_fundamentals
terminalq_get_crypto_market_overview()
    calls: coingecko.get_crypto_market_overview
terminalq_get_fear_greed(limit: int = 30)
    calls: crypto_analytics.get_fear_greed
terminalq_get_crypto_deep(symbol: str)
    calls: coingecko.get_crypto_deep
terminalq_get_crypto_technicals(symbol: str)
    calls: crypto_analytics.get_crypto_technicals
terminalq_get_btc_onchain()
    calls: crypto_analytics.get_btc_onchain
terminalq_get_crypto_derivatives()
    calls: coingecko.get_crypto_derivatives_dashboard
terminalq_get_crypto_correlations(symbol: str = 'BTC')
    calls: crypto_analytics.get_crypto_correlations
terminalq_get_crypto_dominance()
    calls: coingecko.get_crypto_dominance
terminalq_get_crypto_trending()
    calls: coingecko.get_crypto_trending
terminalq_screen_cryptos(category: str = '', min_market_cap_b: float = 0, max_market_cap_b: float = 0, sort_by: str = 'market_cap_desc', limit: int = 20)
    calls: coingecko.screen_cryptos
terminalq_get_defi_overview()
    calls: defillama.get_defi_overview
terminalq_get_cot_report(market: str)
    calls: cftc.get_cot_report
terminalq_get_correlation_matrix(symbols: str = '')
    calls: correlation.get_cross_asset_correlation_matrix
terminalq_get_stablecoins()
    calls: defillama.get_stablecoins_overview
terminalq_get_fed_path()
    calls: market_data.get_fed_path
terminalq_get_equity_sentiment()
    calls: market_data.get_equity_sentiment
terminalq_get_btc_etf_flows(days: int = 10)
    calls: etf_flows.get_btc_etf_flows
terminalq_get_cycle_position()
    calls: cycle.get_cycle_position
terminalq_get_sector_rotation()
    calls: sectors.get_sector_rotation
terminalq_get_market_valuation()
    calls: valuation.get_market_valuation
terminalq_get_metric_context(indicator: str)
    calls: fred.get_metric_context
terminalq_get_btc_mempool()
    calls: mempool.get_btc_mempool
terminalq_get_climate_risk_watch()
    calls: climate.get_climate_risk_watch
terminalq_get_climate_stress_backtest(period: str = 'el_nino_2015_16')
    calls: climate.get_climate_stress_backtest
terminalq_get_metric_stress_backtest(event: str)
    calls: stress_backtest.get_metric_stress_backtest
terminalq_get_retail_sentiment()
    calls: retail_sentiment.get_retail_sentiment
terminalq_get_prediction_markets(topic: str = 'Fed rate')
    calls: prediction_markets.get_prediction_markets
terminalq_get_dealer_gamma(symbol: str = 'SPY')
    calls: options_flow.get_dealer_gamma
terminalq_get_correlation_regime(symbols: str = '')
    calls: correlation_regime.get_correlation_regime
terminalq_get_rsu_tax_analysis(marginal_rate: float = 0.32, ltcg_rate: float = 0.15)
    calls: rsu_tax.get_rsu_tax_analysis
terminalq_record_snapshot(equity_regime: float | None = None, crypto_regime: float | None = None, btc: float | None = None, eth: float | None = None, fear_greed: float | None = None, spx: float | None = None, vix: float | None = None, ten_year: float | None = None, hy_spread: float | None = None, gold: float | None = None, wti: float | None = None, dxy: float | None = None, stablecoin_supply_b: float | None = None, btc_etf_flow_m: float | None = None, cpi_mom: float | None = None, claims_k: float | None = None, fed_path: str = '', notes: str = '', snapshot_date: str = '')
    calls: history.record_snapshot
terminalq_get_regime_history(forward_days: int = 30)
    calls: regime_history.get_regime_history
terminalq_log_prediction(claim: str, symbol: str, direction: str, horizon_days: int = 30, baseline: float | None = None)
    calls: history.log_prediction
terminalq_grade_predictions()
    calls: prediction_grader.grade_open_predictions
terminalq_load_recent_reports(n: int = 7)
    calls: reports.load_recent_reports
terminalq_get_event_scenarios(days: int = 7)
    calls: event_scenarios.get_event_scenarios
terminalq_speak(text: str, voice_name: str = '')
    calls: voice.speak
```
