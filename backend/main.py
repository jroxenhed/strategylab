from contextlib import asynccontextmanager
from dotenv import load_dotenv
load_dotenv()

import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import warnings
warnings.filterwarnings("ignore")

logging.basicConfig(
    level=os.environ.get("STRATEGYLAB_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Silence uvicorn's per-request access log by default — the frontend polls several
# endpoints every 5s and the log is just noise. Set STRATEGYLAB_HTTP_LOG=1 to re-enable.
if not os.environ.get("STRATEGYLAB_HTTP_LOG"):
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

from routes.data import router as data_router
from routes.indicators import router as indicators_router
from routes.backtest import router as backtest_router
from routes.backtest_macro import router as backtest_macro_router
from routes.backtest_quick import router as backtest_quick_router
from routes.search import router as search_router
from routes.providers import router as providers_router
from routes.trading import router as trading_router
from routes.bots import router as bots_router
from routes.slippage import router as slippage_router
from routes.quote import router as quote_router
from routes.notifications import router as notifications_router
import routes.bots as bots_module
from bot_manager import BotManager


@asynccontextmanager
async def lifespan(app: FastAPI):
    from shared import init_ibkr
    from broker import _trading_providers
    from broker_health import HeartbeatMonitor
    from broker_health_singleton import set_monitor

    await init_ibkr()

    monitor = HeartbeatMonitor(registry=_trading_providers)
    set_monitor(monitor)
    monitor.start()

    manager = BotManager()
    manager.load()
    bots_module.bot_manager = manager
    try:
        yield
    finally:
        await manager.shutdown()
        await monitor.stop()
        from notifications import close_client
        await close_client()
        from shared import get_ibkr_connection
        ib = get_ibkr_connection()
        if ib is not None:
            try:
                ib.disconnect()
                logger.info("IBKR disconnected cleanly on shutdown")
            except Exception as e:
                logger.warning("IBKR disconnect on shutdown failed: %s", e)


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(data_router)
app.include_router(indicators_router)
app.include_router(backtest_router)
app.include_router(backtest_macro_router)
app.include_router(backtest_quick_router)
app.include_router(search_router)
app.include_router(providers_router)
app.include_router(trading_router)
app.include_router(bots_router)
app.include_router(slippage_router)
app.include_router(quote_router)
app.include_router(notifications_router)


@app.get("/api/cache")
def get_cache_info():
    from shared import cache_info
    return cache_info()
