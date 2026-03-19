import asyncio
from unittest.mock import patch
from core.orchestrator import run_enterprise_system

async def main():
    print("Testing orchestrator with mock LLM...")
    
    # We will mock query_llm to return something that causes Critic to give "Needs improvement"
    # first, and then "Good" the second time.
    # Critic checks for "root cause", "severity", "recommend"
    
    call_count = 0
    def mock_query(prompt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "Major operational issue: Refund delays. Root cause: Unknown."
        else:
            return "Major operational issue: Refund delays. Root cause: Unknown. Severity: High. Recommended action: Fix it."
            
    with patch('core.llm.query_llm', side_effect=mock_query):
        res = await run_enterprise_system("Analyze our refund policy delays", url="http://example.com/refund-policy")
        print("\nMETRICS:", res["metrics"])
        print("\nLOGS:")
        for log in res["logs"]:
            print(log)
        print("\nINCIDENTS:", len(res["incidents"]))

if __name__ == "__main__":
    asyncio.run(main())
