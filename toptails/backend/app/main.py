from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.routes import router
from app.db.session import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(title="TopTails API", lifespan=lifespan)
app.include_router(router)
