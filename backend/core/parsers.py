"""
Improved Data Ingestion Parsers for the Enterprise AI Platform.
- WebPageParserTool: BeautifulSoup-based HTML extraction
- FileParser: CSV, JSON, Excel, PDF, TXT with pandas
- build_analysis_summary: Pre-LLM structured summary builder
"""
import csv
import json
import io
import re
import urllib.request
from datetime import datetime

import pandas as pd

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    from html.parser import HTMLParser

try:
    import pdfplumber
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False


# ─── HTML Parser ─────────────────────────────────────────────────────────────

if not BS4_AVAILABLE:
    class _FallbackHTMLParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []
        def handle_data(self, data):
            if data.strip():
                self.text.append(data.strip())


class WebPageParserTool:
    """Fetches a URL and extracts clean, meaningful text using BeautifulSoup."""

    @staticmethod
    def parse(url: str) -> list[str]:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')
        except Exception as e:
            return [f"Failed to fetch {url}: {str(e)}"]

        if BS4_AVAILABLE:
            soup = BeautifulSoup(html, "html.parser")
            # Remove noise tags
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
                tag.decompose()
            paragraphs = []
            for elem in soup.find_all(["p", "h1", "h2", "h3", "h4", "li", "td", "th"]):
                text = elem.get_text(separator=" ", strip=True)
                if len(text) > 30:
                    paragraphs.append(text)
            return paragraphs[:80] if paragraphs else [soup.get_text(separator=" ")[:3000]]
        else:
            parser = _FallbackHTMLParser()
            parser.feed(html)
            return parser.text[:80]

    @staticmethod
    def fetch_summary(url: str) -> str:
        """Returns a pre-processed summary string suitable for the LLM."""
        chunks = WebPageParserTool.parse(url)
        if not chunks or (len(chunks) == 1 and chunks[0].startswith("Failed")):
            return chunks[0] if chunks else "No content retrieved."
        text = " ".join(chunks)
        # Trim to 3000 chars for LLM
        return text[:3000]


# ─── File Parser ─────────────────────────────────────────────────────────────

class FileParser:

    @staticmethod
    def parse_csv(content: bytes) -> dict:
        try:
            df = pd.read_csv(io.BytesIO(content))
            df = df.dropna(how="all")  # drop fully-empty rows
            return FileParser._analyze_dataframe(df)
        except Exception as e:
            return {"type": "error", "message": f"Failed to parse CSV: {str(e)}", "data": []}

    @staticmethod
    def parse_excel(content: bytes) -> dict:
        try:
            df = pd.read_excel(io.BytesIO(content))
            df = df.dropna(how="all")
            return FileParser._analyze_dataframe(df)
        except Exception as e:
            return {"type": "error", "message": f"Failed to parse Excel: {str(e)}", "data": []}

    @staticmethod
    def parse_json(content: bytes) -> dict:
        text_content = content.decode("utf-8", errors="ignore")
        try:
            try:
                df = pd.read_json(io.StringIO(text_content))
                df = df.dropna(how="all")
                return FileParser._analyze_dataframe(df)
            except Exception:
                pass
            parsed = json.loads(text_content)
            data = []
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        val = item.get("message") or item.get("text") or item.get("description") or str(item)
                        data.append(str(val))
                    else:
                        data.append(str(item))
            elif isinstance(parsed, dict):
                val = parsed.get("message") or parsed.get("text") or parsed.get("description") or str(parsed)
                data.append(str(val))
            return {"type": "text", "data": data, "stats": {"total_rows": len(data), "columns": []}}
        except json.JSONDecodeError:
            return {"type": "text", "data": [], "stats": {}}

    @staticmethod
    def parse_txt(content: bytes) -> dict:
        text_content = content.decode("utf-8", errors="ignore")
        data = [line.strip() for line in text_content.split("\n") if line.strip()]
        return {"type": "text", "data": data, "stats": {"total_rows": len(data), "columns": []}}

    @staticmethod
    def parse_pdf(content: bytes) -> dict:
        if PDF_AVAILABLE:
            try:
                text_parts = []
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text()
                        if t:
                            text_parts.append(t.strip())
                full_text = "\n".join(text_parts)
                lines = [l.strip() for l in full_text.split("\n") if l.strip()]
                return {"type": "text", "data": lines, "stats": {"total_rows": len(lines), "columns": []}}
            except Exception as e:
                pass
        # Fallback: regex extraction
        text_content = content.decode("ascii", errors="ignore")
        words = re.findall(r'[a-zA-Z0-9\s\.,;:!?]+', text_content)
        cleaned = " ".join([w.strip() for w in words if len(w.strip()) > 3])
        return {"type": "text", "data": [cleaned], "stats": {"total_rows": 1, "columns": []}}

    @staticmethod
    def _analyze_dataframe(df: pd.DataFrame) -> dict:
        columns = [c.lower() for c in df.columns]

        # ── Incident Log Detection ───────────────────────────────────────────
        incident_signals = {"incident_id", "incident", "priority", "state", "incident_state",
                             "severity", "category", "department", "assigned_to", "resolution"}
        if any(sig in columns for sig in incident_signals):
            dataset_type = "incident_logs"

            id_col      = next((c for c in df.columns if any(k in c.lower() for k in ["id", "incident"])), df.columns[0])
            state_col   = next((c for c in df.columns if any(k in c.lower() for k in ["state", "status"])), None)
            priority_col = next((c for c in df.columns if any(k in c.lower() for k in ["prior", "sev", "severity"])), None)
            category_col = next((c for c in df.columns if any(k in c.lower() for k in ["categ", "type", "issue"])), None)
            dept_col    = next((c for c in df.columns if any(k in c.lower() for k in ["dept", "department", "team"])), None)
            duration_col = next((c for c in df.columns if any(k in c.lower() for k in ["dur", "time", "resolve"])), None)

            stats = {
                "total_rows": len(df),
                "unique_incidents": int(df[id_col].nunique()) if id_col else len(df),
                "columns": list(df.columns),
                "states": df[state_col].value_counts().to_dict() if state_col else {},
                "priorities": df[priority_col].value_counts().to_dict() if priority_col else {},
                "categories": df[category_col].value_counts().head(10).to_dict() if category_col else {},
                "departments": df[dept_col].value_counts().head(10).to_dict() if dept_col else {},
                "missing_values": int(df.isnull().sum().sum()),
            }

            if duration_col and pd.api.types.is_numeric_dtype(df[duration_col]):
                stats["average_duration"] = float(df[duration_col].mean())
                stats["max_duration"] = float(df[duration_col].max())

            ui_rows = []
            for _, row in df.head(50).iterrows():
                ui_rows.append({
                    "id": str(row[id_col]) if id_col else "UNKNOWN",
                    "state": str(row[state_col]) if state_col else "Unknown",
                    "priority": str(row[priority_col]).upper() if priority_col else "LOW",
                    "duration": str(row[duration_col]) if duration_col else "--",
                    "category": str(row[category_col]) if category_col else "--",
                    "department": str(row[dept_col]) if dept_col else "--",
                })

            return {"type": dataset_type, "stats": stats, "data": ui_rows}

        else:
            # ── Generic Dataset ──────────────────────────────────────────────
            preview = df.head(10).to_dict(orient="records")
            numeric_summary = {}
            for col in df.select_dtypes(include="number").columns:
                numeric_summary[col] = {
                    "mean": round(float(df[col].mean()), 2),
                    "min": round(float(df[col].min()), 2),
                    "max": round(float(df[col].max()), 2),
                    "missing": int(df[col].isnull().sum())
                }
            return {
                "type": "generic_data",
                "stats": {
                    "total_rows": len(df),
                    "columns": list(df.columns),
                    "missing_values": int(df.isnull().sum().sum()),
                    "numeric_summary": numeric_summary
                },
                "data": preview
            }

    @staticmethod
    def parse(file_name: str, content: bytes) -> dict:
        if not content:
            return {"type": "empty", "data": [], "stats": {}}
        ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
        if ext == 'csv':
            return FileParser.parse_csv(content)
        elif ext == 'json':
            return FileParser.parse_json(content)
        elif ext in ('xlsx', 'xls'):
            return FileParser.parse_excel(content)
        elif ext == 'pdf':
            return FileParser.parse_pdf(content)
        else:
            return FileParser.parse_txt(content)


# ─── Pre-LLM Summary Builder ─────────────────────────────────────────────────

def build_analysis_summary(parsed_data: dict, goal: str = "") -> str:
    """
    Convert parsed data into a structured text summary for the LLM.
    Never sends raw rows — only computed statistics and patterns.
    """
    if not parsed_data:
        return "No dataset provided."

    dtype = parsed_data.get("type", "unknown")
    stats = parsed_data.get("stats", {})
    lines = []

    if goal:
        lines.append(f"ANALYSIS GOAL: {goal}\n")

    if dtype == "error":
        return f"Data ingestion error: {parsed_data.get('message', 'Unknown error')}"

    if dtype == "empty":
        return "Empty dataset — no data to analyze."

    if dtype == "incident_logs":
        lines.append("=== INCIDENT LOG DATASET SUMMARY ===")
        lines.append(f"Total Records  : {stats.get('total_rows', 0)}")
        lines.append(f"Unique Incidents: {stats.get('unique_incidents', 0)}")
        lines.append(f"Missing Values : {stats.get('missing_values', 0)}")

        priorities = stats.get("priorities", {})
        if priorities:
            lines.append("\nSEVERITY / PRIORITY DISTRIBUTION:")
            for k, v in sorted(priorities.items(), key=lambda x: -x[1])[:8]:
                lines.append(f"  {k}: {v} incidents")

        states = stats.get("states", {})
        if states:
            lines.append("\nINCIDENT STATE DISTRIBUTION:")
            for k, v in sorted(states.items(), key=lambda x: -x[1])[:6]:
                lines.append(f"  {k}: {v}")

        categories = stats.get("categories", {})
        if categories:
            lines.append("\nTOP ISSUE CATEGORIES:")
            for k, v in list(categories.items())[:8]:
                lines.append(f"  {k}: {v} occurrences")

        departments = stats.get("departments", {})
        if departments:
            lines.append("\nDEPARTMENT IMPACT:")
            for k, v in list(departments.items())[:6]:
                lines.append(f"  {k}: {v} incidents")

        avg_dur = stats.get("average_duration")
        if avg_dur is not None:
            lines.append(f"\nAverage Resolution Time: {round(avg_dur, 2)}")
            lines.append(f"Max Resolution Time    : {stats.get('max_duration', 'N/A')}")

    elif dtype == "generic_data":
        lines.append("=== GENERIC DATASET SUMMARY ===")
        lines.append(f"Total Rows  : {stats.get('total_rows', 0)}")
        lines.append(f"Columns     : {', '.join(str(c) for c in stats.get('columns', []))}")
        lines.append(f"Missing Vals: {stats.get('missing_values', 0)}")
        numeric = stats.get("numeric_summary", {})
        if numeric:
            lines.append("\nNUMERIC COLUMN STATISTICS:")
            for col, s in numeric.items():
                lines.append(f"  {col}: mean={s['mean']}, min={s['min']}, max={s['max']}, missing={s['missing']}")

    elif dtype == "text":
        data = parsed_data.get("data", [])
        lines.append("=== TEXT DOCUMENT SUMMARY ===")
        lines.append(f"Total Lines / Paragraphs: {len(data)}")
        if data:
            lines.append("\nKEY CONTENT EXCERPT:")
            lines.append("\n".join(data[:15]))

    else:
        # URL content or other
        data = parsed_data.get("data", [])
        if isinstance(data, list):
            lines.append("=== WEB CONTENT SUMMARY ===")
            lines.append(f"Extracted {len(data)} text segments.")
            lines.append("\nCONTENT PREVIEW:")
            lines.append("\n".join(str(d) for d in data[:15]))
        else:
            lines.append(str(data)[:2000])

    return "\n".join(lines)
