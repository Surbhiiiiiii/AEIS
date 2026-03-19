import asyncio
import traceback
from core.orchestrator import run_enterprise_system

async def cb(x):
    pass

async def test():
    try:
        res = await run_enterprise_system(goal="test", file_content=None, filename=None, broadcast_cb=cb)
        print("SUCCESS")
    except Exception as e:
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
