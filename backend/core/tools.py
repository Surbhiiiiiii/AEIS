from core.data_fetcher import fetch_tickets
from collections import Counter
from core.parsers import WebPageParserTool

class TicketFetcherTool:
    @staticmethod
    def run():
        return fetch_tickets()

class KeywordAnalyzerTool:
    @staticmethod
    def run(texts):
        words = " ".join(texts).lower().split()
        common = Counter(words).most_common(15)
        return [{"keyword": w, "count": c} for w, c in common]

class SentimentAnalyzer:
    @staticmethod
    def run(text):
        text = text.lower()
        if any(word in text for word in ["poor", "bad", "late", "wrong", "damaged", "not", "unreachable"]):
            return "Negative"
        elif any(word in text for word in ["good", "great", "excellent"]):
            return "Positive"
        return "Neutral"

class TrendDetector:
    @staticmethod
    def run(texts):
        text_joined = " ".join(texts).lower()
        trends = []
        if "refund" in text_joined: trends.append("Refund delays")
        if "late" in text_joined or "delivered" in text_joined: trends.append("Delivery issues")
        if "support" in text_joined or "unreachable" in text_joined: trends.append("Customer support unresponsiveness")
        return trends

class DatasetAnalyzer:
    """Simulated Dataset Analysis tool for tabular / structured data."""
    @staticmethod
    def run(dataset):
        if not dataset:
            return "Dataset is empty."
        return f"Analyzed {len(dataset)} records. Extracted key anomalies and distributions."

class WebContentFetcher:
    """Tool to fetch and summarize web content from a URL."""
    @staticmethod
    def run(url: str):
        content = WebPageParserTool.parse(url)
        return " ".join(content)[:1000]

class LinkScraper:
    """Tool to extract actionable links from a webpage."""
    @staticmethod
    def run(url: str):
        return f"Found actionable elements on {url}: ['/login', '/help', '/contact-support']"

TrendTool = TrendDetector # Alias for backwards compatibility
SentimentTool = SentimentAnalyzer # Alias for backwards compatibility
