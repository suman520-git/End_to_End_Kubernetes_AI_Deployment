# Business presentation: suggested 10–15 minute walkthrough

Complete setup and model download before the meeting. Keep Docker Desktop running. Warm up one non-cached answer. Confirm metrics work. Use README commands for execution; these notes explain what to say.

| Time | Show | Say |
|---|---|---|
| 1 minute | README architecture diagram | “The model runs in our local Kubernetes environment. The API connects it to a cache and a durable database.” |
| 2 minutes | `app.py`, then Dockerfile | “The application validates a question, checks Redis, calls Ollama when needed and stores the interaction.” |
| 2 minutes | Swagger `/docs`, POST `/chat` | “This endpoint returns the answer along with processing time and the pod that handled it.” |
| 1 minute | Repeat identical request | “The second request uses the cached answer and avoids new model work.” |
| 1 minute | `/history` | “Both interactions are recorded in PostgreSQL, including cache usage.” |
| 2 minutes | Scale API to 3; repeated `/health` | “One client URL now reaches multiple API workers. They share the cache and database.” |
| 1 minute | API and Ollama logs | “Operations can inspect request timing, cache usage and model-server behavior.” |
| 2 minutes | Cache-bypassed requests + `kubectl top` | “We can observe the CPU and memory consumed by each component.” |
| 1 minute | Restart Ollama; run `ollama list` | “The pod was replaced, but the downloaded model files remain on persistent storage.” |

## Three PowerShell terminals

- A: keep `kubectl port-forward -n ai-demo service/traefik 8080:80` running.
- B: send requests, inspect history, and run scaling/restart commands.
- C: show logs or resource utilization while B sends requests.

## Useful business distinctions

- Running the model locally avoids a hosted LLM API call for each generation. It still consumes compute and requires operational effort.
- This small model demonstrates deployment, not enterprise answer quality.
- API replication demonstrates handling/distributing requests; a single Ollama instance still limits uncached generation throughput.
- One Docker Desktop machine is one failure domain. Multiple pods here do not prove multi-machine disaster resilience.
- A persistent disk preserves files across pod replacement. It is not a backup against disk loss or a cluster reset.
- The demo implements manual scaling. It does not claim automatic scaling.

## If a live demonstration fails

Use the exact error and the README troubleshooting table. Do not present illustrative timings as measured results. If model generation is slow, shorten the question and show the completed warm-up example while waiting.
