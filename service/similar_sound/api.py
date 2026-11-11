"""HTTP surface for the floating window. Thin: all behaviour lives in jobs/store/pipeline."""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import ableton, coach, config, pipeline
from .llm import Gemini
from .jobs import JobQueue
from .store import Store


class NewJob(BaseModel):
    url: str


class Timestamp(BaseModel):
    timestamp: str  # "3:04" or seconds


class Question(BaseModel):
    question: str


class Bounce(BaseModel):
    path: str  # your own Ableton export (wav/mp3); analysed but never saved to the library


def create_app(store: Store | None = None, queue: JobQueue | None = None) -> FastAPI:
    config.ensure_dirs()
    store = store or Store(config.DB_PATH)
    queue = queue or JobQueue(store, pipeline.ingest, ableton.is_running)
    app = FastAPI(title="Similar_Sound")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.on_event("startup")
    def _start():
        os.nice(10)  # keep audio threads ahead of us
        queue.start()

    @app.post("/jobs")
    def add_job(body: NewJob):
        return {"id": queue.enqueue(body.url.strip())}

    @app.post("/jobs/{job_id}/timestamp")
    def give_timestamp(job_id: int, body: Timestamp):
        try:
            queue.provide_timestamp(job_id, body.timestamp)
        except KeyError:
            raise HTTPException(404, "no job waiting for a timestamp with that id")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": job_id, "status": "pending"}

    @app.delete("/jobs/{job_id}")
    def dismiss_job(job_id: int):
        try:
            queue.dismiss(job_id)
        except KeyError:
            raise HTTPException(404, "only failed or waiting-for-timestamp jobs can be dismissed")
        return {"id": job_id}

    @app.get("/status")
    def status():
        return queue.status()

    @app.post("/pause")
    def pause():
        queue.pause()
        return {"state": queue.state()}

    @app.post("/resume")
    def resume():
        queue.resume()
        return {"state": queue.state()}

    @app.get("/tracks")
    def tracks():
        return store.list()

    @app.get("/tracks/{track_id}")
    def track(track_id: str):
        doc = store.get(track_id)
        if not doc:
            raise HTTPException(404)
        return doc

    @app.get("/tracks/{track_id}/similar")
    def similar(track_id: str, kind: str = "groove", k: int = 5):
        v = store.vector(track_id, kind)
        if v is None:
            raise HTTPException(404)
        return [{"id": i, "score": round(s, 4), "title": (store.get(i) or {}).get("title", "")}
                for i, s in store.nearest(v, kind, k, exclude=track_id)]

    @app.post("/tracks/{track_id}/ask")
    def ask(track_id: str, body: Question):
        try:
            return {"answer": coach.ask(store, Gemini(), track_id, body.question)}
        except KeyError:
            raise HTTPException(404)
        except RuntimeError as e:  # no API key configured
            raise HTTPException(503, str(e))

    @app.post("/compare")
    def compare(body: Bounce):
        # User-initiated and heavy; runs inline rather than through the paused-while-Ableton queue.
        rec = pipeline.ingest(body.path)
        try:
            return coach.compare(store, Gemini(), rec.doc, rec.vectors)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(503, str(e))

    return app


