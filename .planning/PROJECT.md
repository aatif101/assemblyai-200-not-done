# Project: 200 ≠ done

## One paragraph

An outcome-verified evaluation of AssemblyAI's agent experience. Real coding agents get a real API key, build integrations, and each integration is graded three ways: does the code look right, does it run, and did the API actually do what the user asked. The number that matters is how often a run that executes cleanly and gets a 200 did not do the thing, and whether AssemblyAI's skill and docs MCP server change that.

## Why this exists

AssemblyAI's premise for agent experience is that coding agents, not developers, now read the docs, and that an agent should get from zero to a working, production-grade integration on the first try. Their team says they want evals over impressions: success rates, time to first working call.

What they publish today (read directly from `AssemblyAI/assemblyai-skill` at `f584ec5`, 2026-11-04):

- The results under `assemblyai-workspace/` come from the repo's first commit (2026-03-12). The repo has 76 commits.
- `skills/assemblyai/evals/evals.json` defines 10 evals. Published results cover 3 of them, one run each.
- Grading is an LLM reading the generated code against written assertions. Nothing is executed.
- `feedback.json` is `{"reviews": [], "status": "in_progress"}`.
- A pytest suite runs the skill's own code blocks against the API. It tests the skill's snippets, not what an agent produces from them.

The gap this project fills: an agent in a run-and-fix loop repairs loud errors by itself. What survives is the quiet class, where a wrong request is accepted. A text-graded eval cannot see that class, and neither can a "does it run" check. Only checking the effect can.

## What is and is not the contribution

The skill already documents that some wrong requests are accepted without an error (unknown streaming query params ignored, `u3-rt-pro` redirected, Medical Mode skipped for unsupported languages, TTS unknown fields listed in a header). **Those are AssemblyAI's documented behaviors, not findings of this project.**

The contribution is measurement:

1. How often real agents land on those quiet paths.
2. Whether the skill, and the skill plus docs MCP, prevent it.
3. Which server-side responses would close what is left.

## Audience

An experienced engineer on AssemblyAI's forward-deployed / agent-experience side, who has seen every "I built a transcription app" demo. Reads for three minutes. Wants the headline first, real numbers, method, and limits. Not a hiring manager, not a marketing reader.

## Deliverables

| # | Deliverable | Spec |
|---|---|---|
| D1 | This repo | One command reproduces everything. Raw agent transcripts committed, scrubbed. README's first three lines: what was measured, the headline number, the link to the PR. |
| D2 | Write-up | 600 words maximum. Headline in the first sentence. One table (the probe matrix). Two charts (three-way pass rates by condition; time and tokens to first working call). Then method, then limitations. |
| D3 | PR draft for `AssemblyAI/assemblyai-skill` | Adds one execution-graded eval and refreshes the results table. Framed purely as a contribution. Title and description drafted for the owner; the owner opens it. |
| D4 | One-line summary with the real numbers | For the owner's message. Details in `.planning/OUTREACH.local.md`. |

## Fixed design choices

- **Agent:** Claude Code, headless, in Docker. No second agent.
- **Conditions (3):** bare; skill; skill + docs MCP.
- **Tasks (4)**, 10 runs per cell, 120 runs total.
- **Statistics:** pooled by condition (n = 40) is the headline, with Wilson 95% intervals. Per-task numbers are labelled directional.
- **Runner:** plain Python. Not inspect_ai.
- **Verification:** server-side evidence only. `GET /v2/transcript/{id}` config echo, `speech_model_used`, `metadata.warnings`, streaming `Begin.configuration`, and the SSN digits being absent from saved output.

## Tone and framing rules

- The re-grade of AssemblyAI's three committed eval outputs appears once, in the methods section, neutrally ("for continuity with the existing eval"). It is not a lead point.
- Cite AssemblyAI's Voice Standard Bench in one line: it scores dictation mechanically against a known-correct output with no judge, and this eval applies the same principle to agent integrations. Source given by the owner: `assemblyai.com/blog/voice-standard-bench`, 2026-10-13. **Not yet read by any agent on this project; confirm the description against the post before citing.**
- No superlatives about the project. No criticism that is not also a concrete fix.

## Constraints

- Solo owner, one weekend. **Out-of-pocket budget is about $5 in total (owner, 2026-11-15, decision 24).** How the agent runs fit inside it is an open item in `STATE.md`.
- Live API work runs on the owner's Windows machine.
- Published as a clean single-commit copy at [aatif101/assemblyai-200-not-done](https://github.com/aatif101/assemblyai-200-not-done) (decision 39). The working repo stays private with the full history.

## Out of scope

- A second coding agent, other model vendors, or a leaderboard.
- Measuring transcription accuracy (WER). The oracles check what the server was configured to do, not how well the model transcribed.
- Security testing of AssemblyAI's MCP servers.
- LLM Gateway tasks (the skill says the Gateway is not available on free accounts).
