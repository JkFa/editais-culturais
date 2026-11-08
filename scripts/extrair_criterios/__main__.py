import asyncio
import sys

from .executar import principal

sys.exit(asyncio.run(principal()))
