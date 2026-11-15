"""HTTP surface for the floating window. Thin: all behaviour lives in jobs/store/pipeline."""
from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pathlib import Path

from dataclasses import asdict

import logging
import threading

from . import logsetup, mix, ableton, coach, config, download, keychain, library, listen, llm, models, names, pipeline, rename, similarity
from .providers import PROVIDERS
from .jobs import JobQueue
from .store import Store

log = logging.getLogger("similar_sound.api")


class NewJob(BaseModel):
    url: str


class Timestamp(BaseModel):
    timestamp: str  # "3:04" or seconds


class Turn(BaseModel):
    q: str
    a: str


class Question(BaseModel):
    question: str
    history: list[Turn] = []      # earlier question/answer pairs of this conversation (the window keeps them; the service is stateless)


def _history(turns: list[Turn]) -> tuple:
    out: list[tuple[str, str]] = []
    for t in turns[-llm.MAX_HISTORY_TURNS:]:
        out += [("user", t.q), ("assistant", t.a)]
    return tuple(out)


class Bounce(BaseModel):
    path: str  # your own Ableton export (wav/mp3); analysed but never saved to the library


class ApiKey(BaseModel):
    key: str
    provider: str | None = None      # only needed when the key's prefix doesn't say which company it is
    base_url: str | None = None      # only for "Other (OpenAI-compatible)"
    model: str | None = None


class Name(BaseModel):
    name: str


class TrackRef(BaseModel):
    track_id: str


class Target(BaseModel):
    kind: str          # "track" or "collection"
    id: str | int


def create_app(store: Store | None = None, queue: JobQueue | None = None, listener: listen.Listener | None = None,
               client=None, meter=None, keys=keychain, connect=llm.connect, backfill=rename.backfill) -> FastAPI:
    config.ensure_dirs()
    if store is None:                       # the real service (tests pass their own store and must not write the real log)
        logsetup.setup(config.LOG_PATH)
    store = store or Store(config.DB_PATH)
    queue = queue or JobQueue(store, pipeline.ingest, ableton.is_running,
                              peek=lambda src: download.peek(src, names.known_artists(store)))
    listener = listener or listen.Listener(store)
    meter = meter or mix.MixMeter()
    client = client or (lambda: llm.Client(store))
    comparison: dict = {}   # the latest comparison, so "ask about it" has something to talk about

    def llm_call(fn):
        """Run an LLM-backed call; turn its failures into the sentence the window shows."""
        try:
            return fn(client())
        except llm.LLMError as e:
            log.warning("language model call failed (%s): %s", e.code, e)
            raise HTTPException(503, {"message": str(e), "code": e.code})

    def library_call(fn):
        try:
            return fn()
        except library.LibraryError as e:
            raise HTTPException(400, str(e))
    app = FastAPI(title="Similar_Sound")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def log_failures(request, call_next):
        """Requests that fail are worth a line; the window's constant successful polling is not."""
        response = await call_next(request)
        if response.status_code >= 400:
            log.warning("%s %s -> %s", request.method, request.url.path, response.status_code)
        return response

    @app.on_event("startup")
    def _start():
        log.info("service started (%d songs in the library)", len(store.list()))
        os.nice(10)  # keep audio threads ahead of us
        queue.start()
        # Songs analysed before their raw names were kept get them (and their names fixed) in the background.
        threading.Thread(target=backfill, args=(store,), daemon=True, name="name-backfill").start()

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

    @app.post("/models/unload")
    def unload_models():
        """The window was hidden: hand the analysis models' memory back. Does nothing while an analysis is using them."""
        return {"unloaded": models.unload(), "loaded": models.loaded()}

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

    @app.delete("/tracks/{track_id}")
    def remove_track(track_id: str):
        """Remove a song from the library (and from every collection) so it can be analysed again from scratch."""
        if store.get(track_id) is None:
            raise HTTPException(404, "That song isn't in your library.")
        if queue.active_for(track_id):
            raise HTTPException(409, "That song is being analysed right now. Remove it once it has finished.")
        store.delete_track(track_id)
        queue.forget(track_id)
        return {"id": track_id}

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
        return {"answer": llm_call(lambda g: coach.ask(store, g, track_id, body.question, _history(body.history)))}

    # ---- the API key: any company's. It goes straight into the Keychain and is never returned, logged or
    # kept in a file; the window only ever learns which company and model are connected. ----
    def summary():
        c = keys.get()
        p = PROVIDERS.get(c["provider"]) if c else None
        return {"configured": bool(c and p), "provider": p.id if p else None, "name": p.name if p else None,
                "model": c["model"] if c and p else None}

    @app.get("/settings")
    def settings():
        return summary()

    @app.get("/settings/providers")
    def providers():
        return llm.providers_for_ui()

    @app.post("/settings/llm")
    def save_key(body: ApiKey):
        try:
            creds = connect(body.key, body.provider, body.base_url, body.model)   # free call: proves it works first
            keys.set(asdict(creds))
        except llm.LLMError as e:
            raise HTTPException(400, {"message": str(e), "code": e.code, "providers": llm.providers_for_ui()})
        except RuntimeError as e:
            raise HTTPException(500, {"message": str(e), "code": "error"})
        return summary()

    @app.delete("/settings/llm")
    def remove_key():
        keys.delete()
        return summary()

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

    @app.post("/collections/{cid}/ask")
    def ask_collection(cid: int, body: Question):
        """Chat with the context of every song in a collection."""
        name = library_call(lambda: library.name_of(store, cid))
        docs = library_call(lambda: library.docs(store, cid))
        if not docs:
            raise HTTPException(400, "There are no songs in that collection yet.")
        return {"answer": llm_call(lambda g: coach.ask_collection(store, g, name, docs, body.question, _history(body.history)))}

    @app.post("/collections/{cid}/tracks")
    def add_to_collection(cid: int, body: TrackRef):
        library_call(lambda: library.add(store, cid, body.track_id))
        return {"id": cid}

    @app.delete("/collections/{cid}/tracks/{track_id}")
    def remove_from_collection(cid: int, track_id: str):
        library.remove(store, cid, track_id)
        return {"id": cid}

    # ---- the live spectrum of the project (Mix tab) ----
    @app.get("/mix/stream")
    def mix_stream():
        """Server-sent events, about 30 a second, for as long as the window keeps the connection open."""
        def events():
            frames = meter.frames()
            try:
                for f in frames:
                    yield f"data: {json.dumps(f, separators=(',', ':'))}\n\n"
            finally:
                frames.close()                 # the window went away: stop the audio helper
        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

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
            if listener.vectors:       # how it *feels*, from the vibe vectors, alongside the measured differences
                result["feel"] = similarity.closeness(store, listener.vectors, [d["id"] for d in refs],
                                                      {d["id"]: d.get("title") or d["id"] for d in refs})
        except ValueError as e:
            raise HTTPException(400, str(e))
        comparison.update(result)
        return result

    @app.post("/compare/ask")
    def compare_ask(body: Question):
        if not comparison or listener.project is None:
            raise HTTPException(409, "Compare your project with something first.")
        return {"answer": llm_call(lambda g: coach.ask_about_comparison(g, listener.project, dict(comparison), body.question, _history(body.history)))}

    return app