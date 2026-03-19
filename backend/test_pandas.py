import asyncio
from unittest.mock import patch
from core.orchestrator import run_enterprise_system

# create mock csv
csv_data = b"""incident_id,state,priority,duration
INC001,Open,HIGH,24
INC002,Closed,LOW,2
INC003,In Progress,MEDIUM,12
INC004,Resolved,HIGH,48
INC005,New,LOW,1
"""

async def main():
    print("Testing pandas orchestrator...")
    call_count = 0
    def mock_query(prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "Major operational issue: High reopen rate. Root cause: Unknown."
        else:
            return "Major operational issue: High reopen rate. Root cause: Software Bug. Severity: High. Recommended action: Fix."
            
    with patch('core.llm.query_llm', side_effect=mock_query):
        res = await run_enterprise_system("Analyze these incidents", filename="data.csv", file_content=csv_data)
        print("\nMETRICS:", res["metrics"])
        print("\nINCIDENTS:", res["incidents"])
        print("\nTRENDS:", res["trends"]["distribution"])

if __name__ == "__main__":
    asyncio.run(main())
