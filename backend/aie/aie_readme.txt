GitHub Repo URL
      │
      ▼
1. [api & ingestion]     ──► Safely downloads the code without running it
      │
      ▼
2. [classifier]          ──► Throws away junk (node_modules, binaries), tags languages
      │
      ▼
3. [discovery]           ──► Reads package.json / requirements.txt to detect the tech stack
      │
      ▼
4. [docs_analyzer]       ──► Reads READMEs and extracts configs (while redacting passwords)
      │
      ▼
5. [static_analysis]     ──► Inspects Python AST & JS code (finds routes, classes, queries)
      │
      ▼
6. [components]          ──► Groups code into architecture blocks (API, DB, Cache)
      │
      ▼
7. [relationships]       ──► Connects the dots (e.g. "Web Tier calls Database")
      │
      ▼
8. [knowledge]           ──► Asks Groq LLM: "Explain this component and how it can fail"
      │
      ▼
9. [enrichment]          ──► Adds known real-world patterns (Redis evictions, DB pool limits)
      │
      ▼
10. [validation]         ──► Verifies evidence and calculates confidence scores (0.0 to 1.0)
      │
      ▼
11. [graph]              ──► Auto-generates Contract B (Descriptor) & Contract C (Capabilities)
      │
      ▼
12. [persistence]        ──► Stores everything in-memory and creates vector search embeddings