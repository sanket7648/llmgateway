# AI Log

**AI Tools Used**
I used an AI assistant as a thought partner to scaffold the FastAPI proxy, write the database boilerplate, and debug provider deprecation errors.

**Where the AI was wrong or misleading**
When implementing the free LLM provider (Groq), the AI confidently suggested defaulting to `llama3-8b-8192` and then `llama-3.1-8b-instant`. Both times, the provider returned errors because those specific model names were either decommissioned or not available on the free tier. I caught this by running local `curl` tests, reading the raw HTTP traceback, and successfully pivoting the gateway to use the Gemini API instead.

**Where I overrode the AI's suggestion**
* **Model Provider:** The AI initially structured the proxy to use OpenAI's API. I deliberately overrode this suggestion to use a free API provider to avoid exposing my personal OpenAI billing account for a public take-home assignment, while keeping the standard OpenAI JSON schema.
* **Security & Fallbacks:** The AI initially provided `os.getenv("GEMINI_API_KEY", "dummy-key")` so the app wouldn't crash locally. I overrode this by entirely removing the fallbacks and adding `raise ValueError` if variables were missing. This strictly adheres to the requirement that no secrets touch the repository.

**How I stayed in control of the code**
* **Secrets:** I ensured no keys were ever hardcoded. I explicitly forced the application to crash on startup if environment variables were missing, keeping credentials strictly in the host environment (Neon/Render).
* **Database Lifecycle:** Instead of letting the AI run `CREATE TABLE` scripts loosely at the module level, I refactored the initialization into FastAPI's official `lifespan` event to ensure safe and predictable application startups.
* **Budget Logic:** I manually reviewed the PostgreSQL implementation to ensure the `RealDictCursor` and synchronous `UPDATE` statements executed correctly before the HTTP response was returned to the client.

**Something I learned from scratch**
I learned about Google's official OpenAI-compatible endpoint (`generativelanguage.googleapis.com/v1beta/openai/`). I previously thought routing to Gemini required using their proprietary SDK, but learning I could just swap the base URL and keep the standard OpenAI JSON schema was a massive time-saver.