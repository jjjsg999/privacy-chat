"""Topic glossary: generic regulatory and finance terms that are public vocabulary, never private on their own.

Used when the setting `topic_names` is "common_public" (the default): a detector finding that lies inside one of
these terms is left readable, in chats and in web search queries, so "Digital Asset Custody" is not masked as an
organisation. Your terms list still wins. "mask_all" restores the old behaviour.

Laws and regulations, standards, ratios, products and activities only: never people, companies or the products of a
named company. The effective list is DEFAULT_TOPICS + `public_topics_added` - `public_topics_removed`. Acronyms (no
lowercase letter) match case-sensitively and as whole words; other terms ignore case.
"""
from public_institutions import effective_list as _effective

MODES = ('common_public', 'mask_all')

DEFAULT_TOPICS = (
    # Digital assets and payments
    'Digital Asset Custody', 'Digital Assets', 'Digital Asset', 'Digital Payment Token', 'Digital Payment Tokens', 'DPT',
    'Digital Token Service Provider', 'Virtual Asset', 'Virtual Assets', 'Virtual Asset Service Provider', 'VASP',
    'Crypto Asset', 'Crypto Assets', 'Stablecoin', 'Stablecoins', 'Single-Currency Stablecoin',
    'Tokenisation', 'Tokenization', 'Tokenised Deposits', 'Asset Tokenisation', 'Central Bank Digital Currency', 'CBDC',
    'Distributed Ledger Technology', 'DLT', 'Decentralised Finance', 'DeFi',
    'Payment Services', 'Major Payment Institution', 'Standard Payment Institution',
    'E-money', 'Electronic Money', 'Cross-Border Payments', 'Real-Time Payments', 'Open Banking', 'Open Finance',
    # Laws and regulations
    'Payment Services Act', 'Securities and Futures Act', 'Financial Services and Markets Act', 'Banking Act',
    'Insurance Act', 'Trust Companies Act', 'Personal Data Protection Act', 'PDPA', 'General Data Protection Regulation',
    'GDPR', 'Markets in Crypto-Assets Regulation', 'MiCA', 'MiFID II', 'EMIR', 'Dodd-Frank Act', 'Volcker Rule',
    'Sarbanes-Oxley Act', 'Digital Operational Resilience Act', 'DORA', 'Consumer Duty', 'Senior Managers Regime',
    'Individual Accountability and Conduct', 'Technology Risk Management Guidelines', 'Outsourcing Guidelines',
        # Financial crime and compliance
    'Anti-Money Laundering', 'AML', 'Countering the Financing of Terrorism', 'CFT', 'Combating the Financing of Terrorism',
    'Know Your Customer', 'KYC', 'Customer Due Diligence', 'CDD', 'Enhanced Due Diligence', 'EDD', 'Travel Rule',
    'Sanctions Screening', 'Suspicious Transaction Report', 'STR', 'Suspicious Activity Report', 'SAR',
    'Politically Exposed Person', 'PEP', 'Beneficial Ownership', 'Ultimate Beneficial Owner', 'UBO', 'Proliferation Financing',
    'Transaction Monitoring',     # Capital, liquidity and risk
    'Basel III', 'Basel IV', 'Basel III Endgame', 'Basel Framework', 'CET1', 'Common Equity Tier 1', 'Tier 1 Capital',
    'Tier 2 Capital', 'Additional Tier 1', 'AT1', 'Capital Adequacy Ratio', 'CAR', 'Capital Conservation Buffer',
    'Countercyclical Capital Buffer', 'Leverage Ratio', 'Liquidity Coverage Ratio', 'LCR', 'Net Stable Funding Ratio',
    'NSFR', 'Risk-Weighted Assets', 'RWA', 'Output Floor', 'Internal Ratings-Based Approach', 'IRB',
    'Standardised Approach',     'Counterparty Credit Risk', 'Credit Valuation Adjustment', 'CVA', 'Expected Credit Loss', 'ECL',
    'Probability of Default', 'Loss Given Default', 'Exposure at Default', 'Stress Testing', 'ICAAP', 'ILAAP',
    'Recovery and Resolution', 'Total Loss-Absorbing Capacity', 'TLAC', 'MREL', 'Large Exposures',
    'Fundamental Review of the Trading Book', 'FRTB', 'Value at Risk', 'VaR',     # Accounting and reporting
    'IFRS 9', 'IFRS 13', 'IFRS 15', 'IFRS 16', 'IFRS 17', 'IFRS S1', 'IFRS S2', 'US GAAP', 'GAAP',     'Hedge Accounting', 'Pillar 3 Disclosures', 'XBRL',
    # Sustainability
    'ESG', 'Environmental, Social and Governance', 'Sustainable Finance', 'Green Bond', 'Green Bonds', 'Green Loan',
    'Sustainability-Linked Loan', 'Sustainability-Linked Bond', 'Social Bond', 'Transition Finance', 'Climate Risk',
    'TCFD', 'Task Force on Climate-related Financial Disclosures', 'TNFD', 'Net Zero', 'Carbon Credits', 'Scope 3 Emissions',
        # Products and activities
    'Trade Finance', 'Supply Chain Finance', 'Letter of Credit', 'Letters of Credit', 'Documentary Collection',
    'Bank Guarantee', 'Invoice Financing', 'Factoring', 'Term Loan', 'Revolving Credit Facility', 'Syndicated Loan',
    'Bridge Loan', 'Project Finance', 'Leveraged Finance',     'Family Office', 'Fund Management',     'Exchange-Traded Fund', 'ETF', 'Real Estate Investment Trust', 'REIT', 'Initial Public Offering', 'IPO',
    'Mergers and Acquisitions', 'M&A', 'Debt Capital Markets', 'Equity Capital Markets', 'Securitisation', 'Securitization',
    'Repurchase Agreement', 'Interest Rate Swap', 'Foreign Exchange', 'FX', 'Structured Products',
    'Remittance', 'Correspondent Banking', 'Treasury Management', 'Cash Management', 'Deposit Insurance',
    'Credit Rating', 'Credit Ratings',     'Regulatory Sandbox',
)


def effective_list(added=(), removed=()):
    """Defaults + added - removed (removal ignores case), de-duplicated, in a stable order."""
    return _effective(added, removed, defaults=DEFAULT_TOPICS)
