"""Seed symbol universe for the dynamic symbol repository.

This is initial DATA (not a scanning limit): the repository merges it with
runtime-discovered symbols persisted in the memory store. Any symbol a user
searches is validated against live market data and added to the universe,
so coverage extends to the full US/TASE listing space on demand.

Records: (symbol, name, market). TASE symbols carry yfinance's .TA suffix.
"""

US_SEED: tuple[tuple[str, str], ...] = (
    # Mega/large caps
    ("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corp."), ("NVDA", "NVIDIA Corp."),
    ("AMZN", "Amazon.com Inc."), ("GOOGL", "Alphabet Inc. Class A"), ("GOOG", "Alphabet Inc. Class C"),
    ("META", "Meta Platforms Inc."), ("TSLA", "Tesla Inc."), ("AVGO", "Broadcom Inc."),
    ("BRK-B", "Berkshire Hathaway Class B"), ("JPM", "JPMorgan Chase & Co."), ("V", "Visa Inc."),
    ("UNH", "UnitedHealth Group"), ("XOM", "Exxon Mobil Corp."), ("LLY", "Eli Lilly and Co."),
    ("WMT", "Walmart Inc."), ("MA", "Mastercard Inc."), ("PG", "Procter & Gamble"),
    ("JNJ", "Johnson & Johnson"), ("HD", "Home Depot Inc."), ("ORCL", "Oracle Corp."),
    ("COST", "Costco Wholesale"), ("ABBV", "AbbVie Inc."), ("MRK", "Merck & Co."),
    ("CVX", "Chevron Corp."), ("KO", "Coca-Cola Co."), ("PEP", "PepsiCo Inc."),
    ("BAC", "Bank of America"), ("ADBE", "Adobe Inc."), ("CRM", "Salesforce Inc."),
    ("NFLX", "Netflix Inc."), ("AMD", "Advanced Micro Devices"), ("TMO", "Thermo Fisher Scientific"),
    ("CSCO", "Cisco Systems"), ("ACN", "Accenture plc"), ("MCD", "McDonald's Corp."),
    ("ABT", "Abbott Laboratories"), ("LIN", "Linde plc"), ("DHR", "Danaher Corp."),
    ("INTC", "Intel Corp."), ("INTU", "Intuit Inc."), ("TXN", "Texas Instruments"),
    ("PM", "Philip Morris International"), ("IBM", "IBM Corp."), ("QCOM", "Qualcomm Inc."),
    ("GE", "GE Aerospace"), ("CAT", "Caterpillar Inc."), ("NKE", "Nike Inc."),
    ("VZ", "Verizon Communications"), ("AMGN", "Amgen Inc."), ("PFE", "Pfizer Inc."),
    ("NOW", "ServiceNow Inc."), ("HON", "Honeywell International"), ("UNP", "Union Pacific"),
    ("COP", "ConocoPhillips"), ("SPGI", "S&P Global Inc."), ("UPS", "United Parcel Service"),
    ("T", "AT&T Inc."), ("RTX", "RTX Corp."), ("LOW", "Lowe's Companies"),
    ("BLK", "BlackRock Inc."), ("GS", "Goldman Sachs Group"), ("BA", "Boeing Co."),
    ("ISRG", "Intuitive Surgical"), ("MS", "Morgan Stanley"), ("AXP", "American Express"),
    ("DE", "Deere & Co."), ("BKNG", "Booking Holdings"), ("SYK", "Stryker Corp."),
    ("AMAT", "Applied Materials"), ("ADI", "Analog Devices"), ("TJX", "TJX Companies"),
    ("GILD", "Gilead Sciences"), ("LMT", "Lockheed Martin"), ("VRTX", "Vertex Pharmaceuticals"),
    ("C", "Citigroup Inc."), ("ADP", "Automatic Data Processing"), ("SCHW", "Charles Schwab"),
    ("MO", "Altria Group"), ("REGN", "Regeneron Pharmaceuticals"), ("ETN", "Eaton Corp."),
    ("BSX", "Boston Scientific"), ("ZTS", "Zoetis Inc."), ("CI", "Cigna Group"),
    ("SO", "Southern Co."), ("DUK", "Duke Energy"), ("BMY", "Bristol-Myers Squibb"),
    ("CME", "CME Group"), ("PANW", "Palo Alto Networks"), ("KLAC", "KLA Corp."),
    ("SNPS", "Synopsys Inc."), ("ICE", "Intercontinental Exchange"), ("CDNS", "Cadence Design Systems"),
    ("MU", "Micron Technology"), ("SBUX", "Starbucks Corp."), ("CL", "Colgate-Palmolive"),
    ("ANET", "Arista Networks"), ("ORLY", "O'Reilly Automotive"), ("WM", "Waste Management"),
    ("MCK", "McKesson Corp."), ("CRWD", "CrowdStrike Holdings"), ("MMC", "Marsh & McLennan"),
    # High-beta / retail favorites
    ("PLTR", "Palantir Technologies"), ("COIN", "Coinbase Global"), ("HOOD", "Robinhood Markets"),
    ("MSTR", "MicroStrategy Inc."), ("SMCI", "Super Micro Computer"), ("RIVN", "Rivian Automotive"),
    ("LCID", "Lucid Group"), ("SOFI", "SoFi Technologies"), ("SNOW", "Snowflake Inc."),
    ("UBER", "Uber Technologies"), ("ABNB", "Airbnb Inc."), ("DKNG", "DraftKings Inc."),
    ("MRNA", "Moderna Inc."), ("PYPL", "PayPal Holdings"), ("SHOP", "Shopify Inc."),
    ("ROKU", "Roku Inc."), ("NIO", "NIO Inc."), ("MARA", "MARA Holdings"),
    ("RIOT", "Riot Platforms"), ("CVNA", "Carvana Co."), ("AFRM", "Affirm Holdings"),
    ("RBLX", "Roblox Corp."), ("U", "Unity Software"), ("DDOG", "Datadog Inc."),
    ("NET", "Cloudflare Inc."), ("ZS", "Zscaler Inc."), ("MDB", "MongoDB Inc."),
    ("TEAM", "Atlassian Corp."), ("ARM", "Arm Holdings"), ("CELH", "Celsius Holdings"),
    # ETFs
    ("SPY", "SPDR S&P 500 ETF"), ("QQQ", "Invesco QQQ Trust"), ("IWM", "iShares Russell 2000 ETF"),
    ("DIA", "SPDR Dow Jones Industrial ETF"), ("VTI", "Vanguard Total Stock Market ETF"),
    ("VOO", "Vanguard S&P 500 ETF"), ("XLF", "Financial Select Sector SPDR"),
    ("XLE", "Energy Select Sector SPDR"), ("XLK", "Technology Select Sector SPDR"),
    ("GLD", "SPDR Gold Shares"), ("SLV", "iShares Silver Trust"),
    ("TLT", "iShares 20+ Year Treasury ETF"), ("ARKK", "ARK Innovation ETF"),
    ("SMH", "VanEck Semiconductor ETF"), ("EEM", "iShares MSCI Emerging Markets ETF"),
)

TASE_SEED: tuple[tuple[str, str], ...] = (
    ("TEVA.TA", "Teva Pharmaceutical Industries"),
    ("NICE.TA", "NICE Ltd."),
    ("ESLT.TA", "Elbit Systems"),
    ("ICL.TA", "ICL Group"),
    ("LUMI.TA", "Bank Leumi"),
    ("POLI.TA", "Bank Hapoalim"),
    ("DSCT.TA", "Israel Discount Bank"),
    ("MZTF.TA", "Mizrahi Tefahot Bank"),
    ("FIBI.TA", "First International Bank of Israel"),
    ("BEZQ.TA", "Bezeq Israeli Telecommunication"),
    ("ELAL.TA", "El Al Israel Airlines"),
    ("HARL.TA", "Harel Insurance Investments"),
    ("PHOE.TA", "Phoenix Financial"),
    ("CLIS.TA", "Clal Insurance Enterprises"),
    ("MGDL.TA", "Migdal Insurance"),
    ("AZRG.TA", "Azrieli Group"),
    ("MLSR.TA", "Melisron Ltd."),
    ("BIG.TA", "BIG Shopping Centers"),
    ("AMOT.TA", "Amot Investments"),
    ("ALHE.TA", "Alony Hetz Properties"),
    ("TSEM.TA", "Tower Semiconductor"),
    ("NVMI.TA", "Nova Ltd."),
    ("CAMT.TA", "Camtek Ltd."),
    ("ENLT.TA", "Enlight Renewable Energy"),
    ("DLEKG.TA", "Delek Group"),
    ("SPNS.TA", "Sapiens International"),
    ("MTRX.TA", "Matrix IT"),
    ("FORTY.TA", "Formula Systems"),
    ("STRS.TA", "Strauss Group"),
    ("SAE.TA", "Shufersal Ltd."),
    ("NWMD.TA", "NewMed Energy"),
    ("ORMP.TA", "Oramed Pharmaceuticals"),
)


def seed_records() -> list[dict]:
    records = [{"symbol": s, "name": n, "market": "US"} for s, n in US_SEED]
    records += [{"symbol": s, "name": n, "market": "TASE"} for s, n in TASE_SEED]
    return records
