# Spaced Repetition Agent

An open-source, self-hostable study agent built with Python and FastAPI.

Phase 1 will turn study transcripts into structured entries, schedule reviews, and expose digest, search, and conversational query endpoints.

Requires Python 3.11 or newer. Create a virtual environment and install with `pip install -e .`.

## License

[MIT](LICENSE)

## LangSmith tracing

The tracing helper is available; API integration arrives with the ingestion and query tickets.


Tracing is optional and disabled by default. To enable it, add these settings
to `.env` (or export them in your shell), then restart the API:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your-langsmith-api-key
LANGSMITH_PROJECT=spaced-repetition-agent
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

Use the endpoint for your LangSmith region (for example,
`https://eu.api.smith.langchain.com` for EU). If your API key is linked to
multiple workspaces, also set `LANGSMITH_WORKSPACE_ID`.

Call `/api/v1/entries/ingest` or `/api/v1/chat`, then open your project in
[LangSmith](https://smith.langchain.com). Look for `sra-ingestion` and
`sra-query`: each trace includes graph nodes and nested LangChain model/tool
calls, including inputs, outputs, timing, and errors. Traces include transcript,
chat, and retrieved study content. Set `LANGSMITH_TRACING=false` and restart
to disable uploads.

The routes use `sra.agents.tracing.trace_agent()` to pass Pydantic's `.env`
settings to LangSmith without changing process environment variables. If you
invoke a graph from a standalone Python script, wrap its invocation too:

```python
from sra.agents.tracing import trace_agent

with trace_agent():
    result = graph.invoke(initial_state)  # your compiled graph and input
```

See the [LangGraph tracing guide](https://docs.langchain.com/langsmith/trace-with-langgraph)
for details. No extra decorators are needed on the existing LangChain tools or models.

