import asyncio
import os
import sys

# add parent directory to pythonpath
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import run_enterprise_system

async def main():
    try:
        print("Starting test...")
        res = await run_enterprise_system("Analyze general problems", max_iterations=2)
        print("Success:", res.keys())
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
