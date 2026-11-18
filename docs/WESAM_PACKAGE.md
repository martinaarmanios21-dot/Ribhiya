# Ribhiya as a Wesam.ai agent

**Status (2026-10-31):** working and tested end to end with an MCP client and a Gemini agent. **Not** submitted to,
approved by or listed on Wesam.ai. Wesam's builder pages describe agents as instructions + up to 10 `.skill` files +
reference files + MCP servers, with "publish, send and spend actions" asking first. Ribhiya fits that shape.

## How it plugs in

```
Owner ──(chat)──> Wesam agent (instructions + 1 skill) ──(MCP, bearer token)──> Ribhiya MCP server ──> Ribhiya engine
   └──────────(upload link: files go straight to Ribhiya, never through the model)──────────────┘
```

| MCP tool | Does | Typical output |
|---|---|---|
| `ribhiya_start` | new investigation for this business | ~30 tokens |
| `ribhiya_upload_link` | signed 24 h link; the owner uploads exports directly | ~80 tokens |
| `ribhiya_add_file` / `ribhiya_add_file_from_url` | small file inline / platform-hosted file (https allowlist) | ~130 tokens per file |
| `ribhiya_findings` | causes of the profit change, explanations, options, missing data | ~1,100 tokens (Arabic) |
| `ribhiya_what_if` | projection for a decision on a real month, with assumptions | ~200 tokens |
| `ribhiya_grow` | repeat rate, win-back list (masked names) with comparison group, bundles | ~450 tokens |
| `ribhiya_definitions` | plain-language terms | — |

- **The token decides the business.** One endpoint serves many Wesam workspaces; each gets its own token
  (`TENANT_TOKENS`), and the agent cannot switch businesses. HTTP refuses to start without valid tokens.
- **Least privilege:** no tool sends, approves, deletes or spends. Customer names are masked unless asked.
- **No tokens on Ribhiya's side:** every figure is calculated deterministically; the platform model only reads results.

## Measured (2026-10-31, real UCI Online Retail II data, small-shop sample)

A Gemini 3.5 Flash agent with `INSTRUCTIONS.md` + `ribhiya-profit.skill` as its system prompt ran a 3-message
consultation (analyse → explain → grow sales): **7 model calls, 25,589 tokens (23,868 in / 1,221 out)**, ≈ USD 0.047
at list price (USD 0 on the free tier). Ribhiya tool calls took 0.03–0.19 s each. Sending the same sales exports as
base64 through the model would have added roughly 15,000 output tokens per file; the upload link avoids that.
Transcript and script: `evals/real_data/wesam_agent_test.py`.

## Run it

```bash
TENANT_TOKENS="shop1:<long-random-token>" RIBHIYA_PUBLIC_URL=https://ribhiya.example.com \
  uv run python -m revenue_agent.ribhiya_mcp --http          # MCP: :8812/mcp
uv run revenue-agent serve --port 8080                       # web app + upload links (/u/<token>), same database
```
Put TLS in front of both. Optional: `RIBHIYA_FILE_HOSTS` (hosts allowed for `ribhiya_add_file_from_url`),
`RIBHIYA_LINK_SECRET` (else generated and stored in the database).

## Open questions for Wesam
How a custom MCP server is registered (URL + per-workspace header?), whether workspace files can be passed by URL,
Arabic output review, and revenue share. None of this is publicly documented.
