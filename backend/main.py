from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import warnings
warnings.filterwarnings("ignore")

from routes.data import router as data_router
from routes.indicators import router as indicators_router
from routes.backtest import router as backtest_router
from routes.search import router as search_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(data_router)
app.include_router(indicators_router)
app.include_router(backtest_router)
app.include_router(search_router)
