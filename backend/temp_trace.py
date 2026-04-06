import asyncio
from core.orchestrator import run_enterprise_system

async def dummy_cb(msg):
    pass

async def test():
    try:
        url = "https://mocki.io/v1/86a750b5-2604-47b3-91e2-cc5aaf1c7f2d"
        await run_enterprise_system(goal="analyze", url=url, broadcast_cb=dummy_cb)
        print("Success")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
