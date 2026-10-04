# Walk through the code with the team

Open `app.py` beside this guide. The program follows one straightforward request flow. Functions are used only for endpoints and opening a database connection. The single Pydantic class describes the incoming JSON.

## 1. Imports and setup

| Code | Meaning |
|---|---|
| `hashlib` | Turns a model name + question into a fixed-length cache key |
| `logging` | Writes operational messages visible through `kubectl logs` |
| `os` | Reads settings and the password injected by Kubernetes |
| `socket` | Gets the pod hostname to demonstrate load distribution |
| `time.perf_counter()` | Measures elapsed time for one request |
| `httpx` | Sends HTTP requests from FastAPI to Ollama |
| `psycopg` | Executes PostgreSQL SQL statements |
| `redis` | Reads and writes cached answers |
| `FastAPI` | Creates API routes and automatic Swagger documentation |
| `HTTPException` | Returns an intentional HTTP error response |
| `BaseModel, Field` | Validate the request's question and cache option |

`app = FastAPI(...)` creates the API. `MODEL` and `OLLAMA_URL` come from environment variables. Kubernetes injects these in `05-api.yaml`.

`http://ollama:11434` works because Kubernetes DNS resolves the Service named `ollama` in the same namespace. Similarly, `redis` and `postgres` refer to their Services. These names are not Windows hostnames.

`cache = redis.Redis(...)` configures a reusable Redis client. `decode_responses=True` returns text instead of raw bytes. Three-second timeouts prevent cache calls from waiting indefinitely.

## 2. database()

This function opens a PostgreSQL connection using the host, database, user and password from environment variables. `PGPASSWORD` comes from a Kubernetes Secret, not a password written in Python.

`row_factory=dict_row` makes query results dictionaries, which FastAPI can return as JSON.

`with database() as connection:` opens a connection for a short unit of work. The context manager commits successful work, rolls back on errors, and closes the connection. A production service would usually use a connection pool; keeping connections explicit makes this demo easier to read.

## 3. ChatRequest

Example incoming JSON:

```json
{"question":"What is Kubernetes?","use_cache":true}
```

`question: str` requires text. `Field(min_length=1, max_length=2000)` rejects missing, empty or oversized questions. `use_cache` defaults to `True` when omitted. A separate strip check rejects questions made only of spaces.

## 4. /health and /ready

`/health` returns the API process status and pod name. Kubernetes uses it as a liveness probe. It does not contact the database or the model.

`/ready` checks Redis, the database table, and Ollama's list of installed models. Only when they work and the chosen model is present does it return HTTP 200. Kubernetes excludes an unready API pod from the Service's ready endpoints.

The distinction matters: a live API process may still be unable to answer questions because the model has not been downloaded.

## 5. /chat — trace a first request

Suppose the question is `What is Kubernetes?` and Redis has no answer yet.

1. `started = time.perf_counter()` starts the timer.
2. `question = request.question.strip()` removes whitespace at the beginning and end.
3. `if not question` rejects an all-whitespace input with HTTP 422.
4. `f"v1|{MODEL}|{question}"` combines the prompt version, model and question.
5. `hashlib.sha256(...).hexdigest()` produces a compact, deterministic cache key. This is a hash, **not an embedding** and not encryption of the stored question.
6. `cache.get(key)` looks up the answer. On a miss, it returns `None`.
7. `cached = answer is not None` becomes `False`.
8. `httpx.post(...)` sends the question to Ollama's `/api/generate` route.
9. `stream=False` requests one complete JSON response instead of a stream of partial answers.
10. The `system` message asks for a concise answer. `num_predict=150` limits generated tokens; `num_ctx=2048` limits context size for this demo.
11. `response.raise_for_status()` converts a failing HTTP status into an exception.
12. `answer = response.json()["response"]` extracts the model's text.
13. `cache.setex(key, 3600, answer)` saves the answer with a one-hour expiry.
14. `connection.execute(...)` inserts the question, answer, model, cache flag and API pod name into `chats`.
15. `%s` placeholders bind values safely; do not build SQL by joining the question into a SQL string.
16. Leaving the database `with` block commits the row.
17. The code calculates elapsed seconds, writes a log line, and returns JSON to the caller.

The database creates the ID and timestamp automatically. The `seconds` field measures API processing including database work, but excludes the client's network time.

## 6. Trace the repeated request

The identical trimmed question and model produce the same key. Redis returns text instead of `None`. `cached` becomes `True` and the entire Ollama call is skipped.

A new row is still inserted into PostgreSQL because it is a new interaction. The response normally arrives faster and includes the API pod that handled this request.

If `use_cache=False`, the code skips cache reads and writes. It calls Ollama and records the result in PostgreSQL. Use this path when demonstrating real CPU activity. It does not delete a previously cached answer.

## 7. Errors

An Ollama HTTP timeout returns 504. Redis, PostgreSQL or other Ollama failures return 503 with an operational log entry. Input validation returns 422.

A successful `/chat` response requires a successful database write. The cache may already have been populated before a database failure, so a failed request can leave a cached answer. This simple MVP does not implement cross-service transactions or retry/idempotency logic.

## 8. /history

`SELECT * FROM chats ORDER BY id DESC LIMIT 20` fetches the last 20 stored interactions, newest first. FastAPI serializes dictionaries and timestamps to JSON. This endpoint displays history; it does not add history to the LLM prompt.

## 9. Dockerfile

| Instruction | Meaning |
|---|---|
| `FROM python:3.11-slim` | Start with a small Python runtime image |
| `WORKDIR /app` | Run subsequent commands in `/app` |
| `COPY requirements.txt .` | Copy the dependency list first |
| `RUN pip install ...` | Install dependencies inside the image |
| `COPY app.py .` | Add our one application file |
| `EXPOSE 8000` | Document the container's listening port; this alone does not publish it |
| `CMD [...]` | Start Uvicorn listening on every container network interface |

Dependencies are copied first so Docker can reuse that layer when only application code changes.

## 10. Kubernetes YAML concepts

| Field | Meaning in the demo |
|---|---|
| `kind: Deployment` | Maintain the desired number of running pods |
| `replicas: 1` | Start with one pod |
| `selector` and pod `labels` | Connect a Deployment or Service to matching pods |
| `kind: Service` | Give a workload a stable internal address |
| `containerPort` | The port used by a process inside a container |
| Service `port` / `targetPort` | The Service's port and the destination container port |
| `resources.requests` | Capacity used for scheduling |
| `resources.limits` | CPU/memory ceiling for the container |
| `readinessProbe` | Decide whether to send traffic to this pod |
| `livenessProbe` | Detect an unhealthy API process for restart |
| `secretKeyRef` | Read the database password from a Secret |
| `persistentVolumeClaim` | Attach the requested persistent disk |
| `volumeMounts.mountPath` | Where that disk appears inside the container |
| `strategy: Recreate` | Stop the old singleton database/model pod before starting its replacement |
| `kind: Ingress` | Declare HTTP routing to the API Service |
| Traefik RBAC | Allow the controller to discover routing resources |

The PostgreSQL Deployment is kept at one replica and uses one volume. Do not scale it to three with the API command: database replication is a separate design. The same applies to this single Ollama model server.
