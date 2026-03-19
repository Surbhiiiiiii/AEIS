import csv
import json
import io
import urllib.request
import re
from html.parser import HTMLParser
import pandas as pd

class SimpleHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []

    def handle_data(self, data):
        if data.strip():
            self.text.append(data.strip())

class WebPageParserTool:
    @staticmethod
    def parse(url: str) -> list[str]:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')
                parser = SimpleHTMLParser()
                parser.feed(html)
                return parser.text
        except Exception as e:
            return [f"Failed to fetch {url}: {str(e)}"]

class FileParser:
    @staticmethod
    def parse_csv(content: bytes) -> dict:
        try:
            df = pd.read_csv(io.BytesIO(content))
            return FileParser._analyze_dataframe(df)
        except Exception as e:
            return {"type": "error", "message": f"Failed to parse CSV: {str(e)}"}

    @staticmethod
    def parse_json(content: bytes) -> dict:
        text_content = content.decode("utf-8", errors="ignore")
        try:
            # Let pandas try to read json if it's rectangular
            try:
                df = pd.read_json(io.StringIO(text_content))
                return FileParser._analyze_dataframe(df)
            except Exception:
                pass # fallback to old logic
                
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
            return {"type": "text", "data": data}
        except json.JSONDecodeError:
            return {"type": "text", "data": []}

    @staticmethod
    def _analyze_dataframe(df: pd.DataFrame) -> dict:
        columns = [c.lower() for c in df.columns]
        
        # Detect dataset type
        if "incident_id" in columns or "incident" in columns or "priority" in columns or "state" in columns or "incident_state" in columns:
            dataset_type = "incident_logs"
            
            # Map columns resiliently
            id_col = next((c for c in df.columns if "id" in c.lower() or "incident" in c.lower()), df.columns[0])
            state_col = next((c for c in df.columns if "state" in c.lower() or "status" in c.lower()), None)
            priority_col = next((c for c in df.columns if "prior" in c.lower() or "sev" in c.lower()), None)
            duration_col = next((c for c in df.columns if "dur" in c.lower() or "time" in c.lower()), None)
            
            # Compute stats
            stats = {
                "total_rows": len(df),
                "unique_incidents": df[id_col].nunique() if id_col else len(df),
                "states": df[state_col].value_counts().to_dict() if state_col else {},
                "priorities": df[priority_col].value_counts().to_dict() if priority_col else {},
            }
            
            # Simple average if numeric
            if duration_col and pd.api.types.is_numeric_dtype(df[duration_col]):
                stats["average_duration"] = float(df[duration_col].mean())
                
            # Keep top rows for UI data reference (up to 50)
            ui_rows = []
            for _, row in df.head(50).iterrows():
                ui_rows.append({
                    "id": str(row[id_col]) if id_col else "UNKNOWN",
                    "state": str(row[state_col]) if state_col else "Unknown",
                    "priority": str(row[priority_col]).upper() if priority_col else "LOW",
                    "duration": str(row[duration_col]) if duration_col else "--"
                })

            return {"type": dataset_type, "stats": stats, "data": ui_rows}

        else:
            # Generic dataset
            preview = df.head(10).to_dict(orient="records")
            return {
                "type": "generic_data",
                "stats": {"total_rows": len(df), "columns": list(df.columns)},
                "data": preview
            }

    @staticmethod
    def parse_txt(content: bytes) -> dict:
        text_content = content.decode("utf-8", errors="ignore")
        # Split by newlines and drop empty
        data = [line.strip() for line in text_content.split("\n") if line.strip()]
        return {"type": "text", "data": data}

    @staticmethod
    def parse_pdf(content: bytes) -> dict:
        text_content = content.decode("ascii", errors="ignore")
        words = re.findall(r'[a-zA-Z0-9\s\.,;:!?]+', text_content)
        cleaned = " ".join([w.strip() for w in words if len(w.strip()) > 3])
        return {"type": "text", "data": [cleaned]}

    @staticmethod
    def parse(file_name: str, content: bytes) -> dict:
        if not content:
            return {"type": "empty", "data": []}
            
        ext = file_name.split('.')[-1].lower() if '.' in file_name else ''
        
        if ext == 'csv':
            return FileParser.parse_csv(content)
        elif ext == 'json':
            return FileParser.parse_json(content)
        elif ext == 'pdf':
            return FileParser.parse_pdf(content)
        else: # Default to txt fallback
            return FileParser.parse_txt(content)
