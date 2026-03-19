from core.tools import TrendDetector

class MonitoringAgent:
    def __init__(self, memory=None):
        self.memory = memory

    def detect_incidents(self, data):
        """Detect operational incidents automatically using trend tooling."""
        trends = TrendDetector.run(data)
        incidents = []
        for t in trends:
            incidents.append(f"Incident Pattern: {t}")
            
        if self.memory and incidents:
            self.memory.add_event("MonitoringAgent", "Detected incidents", {"incidents": incidents})
            
        return incidents
