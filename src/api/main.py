"""NWO Skill Engine — FastAPI application."""
from __future__ import annotations
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from ..models.database import create_tables
from .routes import router

app = FastAPI(
    title="NWO Skill Engine",
    description="Layer 4 — Agent skill files and capability publishing for NWO Robotics.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.on_event("startup")
async def startup():
    try:
        await create_tables()
    except Exception as e:
        print(f"[WARN] DB init: {e}")


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "service": "nwo-skill-engine"}


@app.get("/", tags=["System"])
async def root():
    return {"service": "nwo-skill-engine", "docs": "/docs", "search": "/skills/search"}
