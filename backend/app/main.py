from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import pathlib
import logging

from app.db.database import create_tables
from app.api.routes import face_analysis, users, wardrobe, recommendations, situation
from app.api.routes.auth import router as auth_router
from app.api.routes.products import router as products_router
from app.api.routes.product_feedback import router_feedback as products_feedback_router
from app.core.config import get_settings

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")

# Ensure static dir exists before mounting
_static_dir = pathlib.Path("/tmp/styleai")
_static_dir.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="AI-powered fashion recommendation engine",
    lifespan=lifespan,
)

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://styleai.app",
        "https://fashion-tech-frontend.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ──────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(products_router)
app.include_router(products_feedback_router)
app.include_router(face_analysis.router)
app.include_router(users.router)
app.include_router(wardrobe.router)
app.include_router(recommendations.router)
app.include_router(situation.router)

# ─── Static file serving (dev — local images) ─────────────────────────────────
app.mount("/static", StaticFiles(directory="/tmp/styleai"), name="static")

# ─── Health ───────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}


@app.get("/")
async def root():
    return {"message": "StyleAI API", "docs": "/docs"}
