"""Public institutions that are public knowledge and not identifying on their own.

Used when the setting `institution_names` is "common_public" (the default): a detector finding that lies
inside one of these names is left readable, in chats and in web search queries. Your terms list still wins.
"mask_all" restores the old behaviour (detectors decide alone).

The effective list is DEFAULT_INSTITUTIONS + the setting `public_institutions_added` - `public_institutions_removed`.
Matching is whole-word. Acronyms (names with no lowercase letter, such as MAS or SEC) match case-sensitively, so
"sec" in ordinary text is not treated as the regulator; full names match ignoring case.
"""
import re

# Regulators, central banks, standard setters, exchanges and rating agencies. Edit to taste, or use the Settings page.
DEFAULT_INSTITUTIONS = (
    # Regulators and central banks
    'MAS', 'Monetary Authority of Singapore', 'HKMA', 'Hong Kong Monetary Authority', 'SFC',
    'Securities and Futures Commission', 'SEC', 'Securities and Exchange Commission', 'CFTC', 'FINRA',
    'Federal Reserve', 'OCC', 'FDIC', 'FCA', 'Financial Conduct Authority', 'PRA', 'Prudential Regulation Authority',
    'Bank of England', 'ECB', 'European Central Bank', 'EBA', 'European Banking Authority', 'ESMA', 'Bank of Japan',
    'People\'s Bank of China', 'PBOC', 'Reserve Bank of Australia', 'APRA', 'ASIC', 'Bank of Canada', 'Swiss National Bank',
    'FINMA', 'BaFin', 'Reserve Bank of India', 'SEBI', 'IMF', 'International Monetary Fund', 'World Bank',
    # Standard setters
    'BIS', 'Bank for International Settlements', 'Basel Committee', 'BCBS', 'Financial Stability Board', 'FSB',
    'FATF', 'Financial Action Task Force', 'IOSCO', 'IFRS Foundation', 'IASB', 'FASB',
    # Exchanges
    'SGX', 'Singapore Exchange', 'HKEX', 'Hong Kong Exchanges and Clearing', 'NYSE', 'New York Stock Exchange', 'Nasdaq',
    'London Stock Exchange', 'LSE', 'Euronext', 'Deutsche Börse', 'Japan Exchange Group', 'Shanghai Stock Exchange',
    'Shenzhen Stock Exchange', 'CME Group', 'ASX',
    # Rating agencies
    'Moody\'s', 'Moody\'s Investors Service', 'S&P', 'S&P Global', 'S&P Global Ratings', 'Standard & Poor\'s',
    'Fitch', 'Fitch Ratings',
)

MODES = ('common_public', 'mask_all')


def _clean(names):
    return [' '.join(str(name).split()) for name in names or () if isinstance(name, str) and str(name).strip()]


def effective_list(added=(), removed=(), defaults=DEFAULT_INSTITUTIONS):
    """Defaults + added - removed, de-duplicated (removal ignores case), in a stable order."""
    gone = {name.casefold() for name in _clean(removed)}
    result, seen = [], set()
    for name in list(defaults) + _clean(added):
        key = name if _is_acronym(name) else name.casefold()
        if name.casefold() in gone or key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def _is_acronym(name):
    return not any(ch.islower() for ch in name)


def pattern(names, bound_l, bound_r, ignore_case=False):
    """One regex for the list: acronyms case-sensitive (unless ignore_case), full names ignoring case. None if empty."""
    names = sorted(_clean(names), key=len, reverse=True)
    if not names:
        return None
    alternatives = [re.escape(n) if _is_acronym(n) and not ignore_case else '(?i:' + re.escape(n) + ')' for n in names]
    return re.compile(bound_l + '(?:' + '|'.join(alternatives) + ')' + bound_r)
