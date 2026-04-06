"""
tools.py
--------
Real data processing tools for the Enterprise AI Platform.

Removed (were fake/simulated):
  - SentimentAnalyzer  : keyword-based fake sentiment
  - LinkScraper        : returned hardcoded placeholder links
  - DatasetAnalyzer    : returned a fake "Analyzed N records" string

Real tools retained / upgraded:
  - TicketFetcherTool  : queries MongoDB incidents collection
  - KeywordAnalyzerTool: word-frequency counter (used for keyword listing only)
  - TrendDetector      : NOW uses pandas frequency analysis on real data
  - WebContentFetcher  : real URL scraping via BeautifulSoup
"""
from core.data_fetcher import fetch_tickets
from collections import Counter
from core.parsers import WebPageParserTool

import pandas as pd


class TicketFetcherTool:
    @staticmethod
    def run() -> list[dict]:
        """Fetch real incidents from MongoDB. Returns [] if none."""
        return fetch_tickets()


class KeywordAnalyzerTool:
    """Simple word-frequency counter — used for surfacing top terms, not AI analysis."""
    # Words to exclude from keyword extraction
    _STOP_WORDS = {
        "the", "a", "an", "is", "are", "was", "of", "in", "at", "by", "to",
        "and", "or", "not", "no", "for", "on", "with", "this", "that", "it",
        "its", "be", "has", "have", "had", "do", "did", "but", "as", "from",
        "if", "which", "who", "what", "when", "where", "how", "all", "been",
        "nan", "none", "null", "unknown", "true", "false"
    }

    @staticmethod
    def run(texts: list[str]) -> list[dict]:
        """Return top 15 meaningful words with their frequencies."""
        words = " ".join(texts).lower().split()
        filtered = [w.strip(".,;:!?\"'") for w in words
                    if len(w) > 2 and w not in KeywordAnalyzerTool._STOP_WORDS]
        common = Counter(filtered).most_common(15)
        return [{"keyword": w, "count": c} for w, c in common]


class TrendDetector:
    """
    Statistical trend detection using pandas.

    Analyses real term/category distributions from the provided texts and
    identifies statistically elevated patterns — no keyword substring hacks.
    """

    @staticmethod
    def run(texts: list[str]) -> list[str]:
        """
        Detect trends from a list of text strings using frequency analysis.
        Returns up to 5 trend statements backed by actual counts.
        """
        if not texts:
            return []

        # Build a pandas Series from all tokens
        all_tokens = []
        for t in texts:
            tokens = str(t).lower().split()
            all_tokens.extend([tok.strip(".,;:!?\"'") for tok in tokens if len(tok) > 3])

        if not all_tokens:
            return []

        series = pd.Series(all_tokens)
        # Drop common stop words
        stop = {
            "that", "this", "with", "from", "have", "been", "were", "they",
            "their", "which", "when", "will", "more", "than", "into",
            "some", "also", "after", "about", "would", "there", "other"
        }
        series = series[~series.isin(stop)]

        counts = series.value_counts()
        if counts.empty:
            return []

        total = counts.sum()
        mean_freq = counts.mean()
        std_freq = counts.std() if len(counts) > 1 else 0

        trends = []
        # Elevated terms: appear at significantly above-average frequency
        threshold = mean_freq + (std_freq * 1.5 if std_freq > 0 else mean_freq * 0.5)
        elevated = counts[counts >= max(threshold, 2)].head(5)

        for term, cnt in elevated.items():
            pct = round((cnt / total) * 100, 1)
            trends.append(f"Elevated term '{term}': {cnt} occurrences ({pct}% of corpus)")

        if not trends:
            # Fallback: report the single top term if no statistically elevated terms
            top_term, top_cnt = counts.index[0], counts.iloc[0]
            pct = round((top_cnt / total) * 100, 1)
            trends.append(f"Most frequent term: '{top_term}' ({top_cnt} occurrences, {pct}%)")

        return trends[:5]


class WebContentFetcher:
    """Fetches and summarises real web content from a URL using BeautifulSoup."""

    @staticmethod
    def run(url: str) -> str:
        content = WebPageParserTool.parse(url)
        return " ".join(content)[:3000]


# Backwards-compatibility alias (TrendDetector replaces TrendTool)
TrendTool = TrendDetector
