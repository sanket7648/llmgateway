# Decisions

**What I built**
I built a minimal, asynchronous LLM gateway using Python and FastAPI. It proxies chat completion requests to the Gemini API while enforcing strict virtual key authentication and budget caps. It utilizes a cloud-hosted PostgreSQL database (Neon) for persistent usage tracking, implements a robust fallback policy for API limits, and enforces a strict fail-fast startup if environment secrets are missing.

**Moving Parts & Request Lifecycle**
1. **Startup**: FastAPI's `lifespan` event runs `init_db()` to safely ensure SQL tables exist via `CREATE TABLE IF NOT EXISTS`.
2. **Client** sends a `POST /v1/chat/completions` request with a virtual key in the `Authorization` header.
3. **Gateway** intercepts the request and queries the PostgreSQL `keys` table to authenticate the key and check its `spend_usd` against its `budget_usd`.
4. **Provider Call**: The gateway forwards the request to Google's OpenAI-compatible endpoint using `httpx`.
5. **Usage Logging**: The response is parsed for token usage. The gateway synchronously calculates the simulated cost, updates the key's total spend, and logs the request details in the `logs` table.
6. **Response**: The provider's JSON is returned to the client.

*Rough Diagram:*
`Client -> FastAPI Gateway -> PostgreSQL (Auth/Budget Check) -> Gemini API -> PostgreSQL (Update Spend/Log) -> Client`

**Most Important Decisions**
1. **Stack Choice (FastAPI + PostgreSQL)**: 
   * *Options:* Node.js/Express, Python/Flask, Python/FastAPI. 
   * *Picked:* FastAPI + PostgreSQL. 
   * *Tradeoff:* FastAPI handles async HTTP requests natively, which is critical for proxying LLMs. I initially used SQLite, but migrated to PostgreSQL (Neon) to ensure data persistence across ephemeral cloud deployments and support horizontal scaling.
2. **Strict Secret Enforcement**:
   * *Options:* Provide default dummy strings for local dev, or fail hard.
   * *Picked:* Hard application crash on startup if `DATABASE_URL` or `GEMINI_API_KEY` are missing.
   * *Tradeoff:* Makes local setup slightly more tedious, but guarantees zero accidental exposure or silent failures in production.
3. **Application Lifecycle (Lifespan Events)**:
   * *Options:* Run DB initialization at the module level vs FastAPI lifespan.
   * *Picked:* FastAPI `@asynccontextmanager` lifespan.
   * *Tradeoff:* Ensures database connections and table validations only occur when the ASGI server actually boots, avoiding ghost connections during testing or imports.
4. **Cost Estimation**: 
   * *Options:* Exact token counting per model vs simulated blended rate. 
   * *Picked:* Simulated blended rate ($0.0001 per 1k tokens). 
   * *Tradeoff:* Much simpler to implement in a weekend, sacrificing pinpoint financial accuracy for gateway tracking purposes.

**Why enforce budgets at the gateway instead of trusting callers?**
Clients are inherently untrusted environments. If budget logic lives in the client, a malicious user can bypass it, or a bug (like an infinite retry loop) can accidentally spam the provider. The gateway acts as a strict, centralized choke point that protects the core infrastructure (and the company's credit card) regardless of client behavior.

**Concurrency on near-exhausted keys**
If two requests on the same near-exhausted key hit at exactly the same millisecond, they might both read the database before either writes the updated spend. I knowingly accepted this race condition. Implementing distributed locking (like Redis) or complex SQL `SELECT ... FOR UPDATE` transactions for a weekend MVP violated the "cut scope" constraint. 

**Fallback Policy**
If the primary model (gemini-3.8-flash) returns a 503 Service Unavailable due to high demand, the gateway silently intercepts the error, swaps the payload to request a lighter backup model (gemini-3.5-flash-lite), and retries. If the request times out completely (30 seconds), it fails fast with a 504 to prevent hanging the client indefinitely. 

**Deliberately NOT Built**
* **Streaming:** Chose non-streaming to simplify token counting and sync logging.
* **Redis Caching:** Skipped to keep infrastructure dependencies minimal.
* **Admin UI:** Built a barebones `GET /usage` JSON endpoint instead, as the prompt explicitly requested zero hours spent on UI.

**Least Confident Decision**
*Using Synchronous Logging.* 
* *Argument for:* It guarantees that a key's spend is updated before the next request can fire, strictly enforcing budgets.
* *Argument against:* It adds database I/O latency to the user's request round-trip. A background task or message queue would make the proxy faster, but opens up vulnerabilities for budget overruns.

**Where it breaks & Next Steps**
It breaks under extremely high concurrent load for the same key due to the race condition mentioned above. With one more week, I would add Redis for strict token-bucket rate limiting and asynchronous batch-logging to reduce database I/O on every request.