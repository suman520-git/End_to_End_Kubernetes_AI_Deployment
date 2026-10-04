"""Small Kubernetes demo: question -> cache or Ollama -> save history."""
import hashlib
import logging
import os
import socket
import time

import httpx
import psycopg
import redis
from fastapi import FastAPI, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

app = FastAPI(title="Kubernetes AI Demo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ai-demo")

MODEL = os.getenv("MODEL", "qwen2.5:0.5b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
POD_NAME = socket.gethostname()  # Kubernetes gives each pod its own hostname.
cache = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), decode_responses=True,
                    socket_connect_timeout=3, socket_timeout=3)


def database():
    # A short-lived connection keeps the teaching example simple.
    return psycopg.connect(host=os.getenv("PGHOST", "postgres"),
                           dbname=os.getenv("PGDATABASE", "aidemo"),
                           user=os.getenv("PGUSER", "demo"),
                           password=os.environ["PGPASSWORD"],
                           connect_timeout=3, row_factory=dict_row)


class ChatRequest(BaseModel):
    # This one class validates incoming JSON; no custom service classes.
    question: str = Field(min_length=1, max_length=2000)
    use_cache: bool = True


@app.get("/health")
def health():
    # Liveness: the API process is running. Also identifies the serving pod.
    return {"status": "ok", "pod": POD_NAME}


@app.get("/ready")
def ready():
    # Readiness: dependencies and the downloaded model must be available.
    try:
        cache.ping()
        with database() as connection:
            connection.execute("SELECT 1 FROM chats LIMIT 1")
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        response.raise_for_status()
        names = [item["name"] for item in response.json()["models"]]
        if MODEL not in names:
            raise ValueError("Model has not been downloaded")
    except (redis.RedisError, psycopg.Error, httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(503, "Dependencies or model are not ready")
    return {"status": "ready", "model": MODEL}


@app.post("/chat")
def chat(request: ChatRequest):
    started = time.perf_counter()
    question = request.question.strip()
    if not question:
        raise HTTPException(422, "Question cannot be blank")

    # Include model and prompt version so different configurations do not share answers.
    key = "chat:" + hashlib.sha256(f"v1|{MODEL}|{question}".encode()).hexdigest()
    try:
        answer = cache.get(key) if request.use_cache else None
        cached = answer is not None
        if answer is None:
            response = httpx.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": MODEL, "prompt": question, "stream": False,
                      "system": "Give a short, clear answer in plain English.",
                      "options": {"temperature": 0, "num_predict": 150, "num_ctx": 2048}},
                timeout=httpx.Timeout(300, connect=5),
            )
            response.raise_for_status()
            answer = response.json()["response"]
            if request.use_cache:
                cache.setex(key, 3600, answer)  # Reuse this answer for one hour.

        # Both cache hits and newly generated answers are recorded.
        with database() as connection:
            connection.execute(
                "INSERT INTO chats (question, answer, model, cached, pod) VALUES (%s, %s, %s, %s, %s)",
                (question, answer, MODEL, cached, POD_NAME),
            )  # The context manager commits and closes the connection.
    except httpx.TimeoutException:
        logger.warning("ollama_timeout pod=%s", POD_NAME)
        raise HTTPException(504, "Model timed out; try a shorter question")
    except (redis.RedisError, psycopg.Error, httpx.HTTPError, ValueError, KeyError) as error:
        logger.error("dependency_failure type=%s pod=%s", type(error).__name__, POD_NAME)
        raise HTTPException(503, "A dependency failed; check the pod logs")

    elapsed = round(time.perf_counter() - started, 3)
    logger.info("chat cached=%s seconds=%s pod=%s", cached, elapsed, POD_NAME)
    return {"answer": answer, "cached": cached, "seconds": elapsed,
            "pod": POD_NAME, "model": MODEL}


@app.get("/history")
def history():
    try:
        with database() as connection:
            return connection.execute(
                "SELECT * FROM chats ORDER BY id DESC LIMIT 20"
            ).fetchall()
    except psycopg.Error:
        raise HTTPException(503, "PostgreSQL is unavailable")
