"""HTTP surface for the floating window. Thin: all behaviour lives in jobs/store/pipeline."""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from pathlib import Path

from . import ableton, coach, config, keychain, library, listen, llm, pipeline
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


class GeminiKey(BaseModel):
    key: str


class Name(BaseModel):
    name: str


class TrackRef(BaseModel):
    track_id: str


class Target(BaseModel):
    kind: str          # "track" or "collection"
    id: str | int


def create_app(store: Store | None = None, queue: JobQueue | None = None, listener: listen.Listener | None = None,
               gemini=None, keys=keychain, check_key=llm.check_key) -> FastAPI:
    config.ensure_dirs()
    store = store or Store(config.DB_PATH)
    queue = queue or JobQueue(store, pipeline.ingest, ableton.is_running)
    listener = listener or listen.Listener(store)
    gemini = gemini or (lambda: llm.Gemini(store))
    comparison: dict = {}   # the latest comparison, so "ask about it" has something to talk about

    def llm_call(fn):
        """Run an LLM-backed call; turn its failures into the sentence the window shows."""
        try:
            return fn(gemini())
        except llm.LLMError as e:
            code = "no_key" if isinstance(e, llm.NoKey) else "credits" if isinstance(e, llm.OutOfCredits) else "error"
            raise HTTPException(503, {"message": str(e), "code": code})

    def library_call(fn):
        try:
            return fn()
        except library.LibraryError as e:
            raise HTTPException(400, str(e))
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
        if store.get(track_id) is None:
            raise HTTPException(404)
        return {"answer": llm_call(lambda g: coach.ask(store, g, track_id, body.question))}

    # ---- Gemini key: goes straight into the Keychain, is never returned, logged or stored in a file ----
    @app.get("/settings")
    def settings():
        return {"gemini_key": keys.exists()}

    @app.post("/settings/gemini-key")
    def save_key(body: GeminiKey):
        try:
            check_key(body.key.strip())            # free call: proves the key works before it is saved
            keys.set(body.key)
        except llm.LLMError as e:
            raise HTTPException(400, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(500, str(e))
        return {"gemini_key": True}

    # ---- collections ----
    @app.get("/collections")
    def collections():
        return library.list_all(store)

    @app.post("/collections")
    def new_collection(body: Name):
        return library_call(lambda: library.create(store, body.name))

    @app.patch("/collections/{cid}")
    def rename_collection(cid: int, body: Name):
        library_call(lambda: library.rename(store, cid, body.name))
        return {"id": cid}

    @app.delete("/collections/{cid}")
    def delete_collection(cid: int):
        library_call(lambda: library.delete(store, cid))
        return {"id": cid}

    @app.post("/collections/{cid}/tracks")
    def add_to_collection(cid: int, body: TrackRef):
        library_call(lambda: library.add(store, cid, body.track_id))
        return {"id": cid}

    @app.delete("/collections/{cid}/tracks/{track_id}")
    def remove_from_collection(cid: int, track_id: str):
        library.remove(store, cid, track_id)
        return {"id": cid}

    # ---- hear the project, compare it ----
    @app.post("/listen/start")
    def listen_start():
        comparison.clear()
        listener.start()
        return listener.status()

    @app.post("/listen/cancel")
    def listen_cancel():
        listener.cancel()
        comparison.clear()
        return listener.status()

    @app.get("/listen")
    def listen_status():
        return listener.status()

    @app.post("/project/file")
    def project_from_file(body: Bounce):
        """An exported bounce instead of live listening."""
        comparison.clear()
        try:
            listener.load_file(Path(body.path).expanduser())
        except Exception as e:                      # noqa: BLE001 - ProjectError or unreadable file
            raise HTTPException(400, str(e) or "I couldn't read that file.")
        return listener.status()

    @app.post("/compare")
    def compare(body: Target):
        if listener.project is None:
            raise HTTPException(409, "Play something in Ableton first, then pick what to compare it with.")
        try:
            if body.kind == "track":
                doc = store.get(str(body.id))
                if doc is None:
                    raise HTTPException(404, "That song isn't in your library.")
                refs, name, is_collection = [doc], doc.get("title") or "that song", False
            elif body.kind == "collection":
                refs = library_call(lambda: library.docs(store, int(body.id)))
                name, is_collection = library_call(lambda: library.name_of(store, int(body.id))), True
            else:
                raise HTTPException(400, "Compare against a song or a collection.")
            result = coach.compare_project(listener.project, refs, name, is_collection)
        except ValueError as e:
            raise HTTPException(400, str(e))
        comparison.update(result)
        return result

    @app.post("/compare/ask")
    def compare_ask(body: Question):
        if not comparison or listener.project is None:
            raise HTTPException(409, "Compare your project with something first.")
        return {"answer": llm_call(lambda g: coach.ask_about_comparison(g, listener.project, dict(comparison), body.question))}

    return app