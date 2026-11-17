"""Runner plumbing that does not need Docker, a key or the network."""
import json
from pathlib import Path

from twohundred.agentrun import (CONDITIONS, DISALLOWED_TOOLS, MAX_TURNS, TASK_IDS, _copy_lf, claude_args, plan,
                                 should_copy, summarize_stream)
from twohundred.oracles import TASKS

# Names of API parameters and values. A prompt that contains one is a hint, and the bare
# condition stops being a baseline (Phase 2 plan: "no parameter names, no hints").
HINTS = ["keyterms", "word_boost", "speaker_labels", "redact_pii", "medical-v1", "domain", "localization", "en_uk",
         "language_code", "speech_model", "universal-", "u3-", "lemur", "speech_understanding", "summarization",
         "max_accuracy", "disfluencies", "/v3/", "bearer", "llm gateway"]


def test_plan_covers_every_cell_and_is_reproducible():
    pilot, sweep = plan("pilot", 1), plan("sweep", 1)
    assert len(pilot) == 12 and len(sweep) == 120
    assert {(t, c) for t, c, _ in pilot} == {(t, c) for t in TASK_IDS for c in CONDITIONS}
    assert plan("sweep", 1) == sweep and plan("sweep", 2) != sweep


def test_prompts_name_no_parameters():
    for task in TASK_IDS:
        text = (TASKS / task / "prompt.md").read_text(encoding="utf-8").lower()
        assert not [h for h in HINTS if h in text], task


def test_prompts_share_the_contract_paragraph():
    paras = [(TASKS / t / "prompt.md").read_text(encoding="utf-8").split("When you are done,")[1].split("It must exit")[0]
             for t in TASK_IDS]
    assert len(set(paras)) == 1


def test_every_task_is_complete():
    for task in TASK_IDS:
        spec = json.loads((TASKS / task / "task.json").read_text(encoding="utf-8"))
        assert spec["id"] == task
        for f in ("prompt.md", "schema.json", "rubric.json"):
            assert (TASKS / task / f).is_file()
    assert (TASKS / "t4_legacy_upgrade" / "seed" / "run.sh").is_file()


def test_summarize_stream_reads_the_result_and_caps():
    lines = [{"type": "system", "subtype": "init", "model": "m", "tools": ["Bash"], "mcp_servers": []},
             {"type": "assistant"},
             {"type": "result", "subtype": "error_max_turns", "num_turns": 41, "total_cost_usd": 0.5}]
    s = summarize_stream(lines)
    assert (s["cap_hit"], s["num_turns"], s["total_cost_usd"], s["model_reported"]) == ("turns", 41, 0.5, "m")
    assert summarize_stream([{"type": "result", "subtype": "success"}])["cap_hit"] is None
    assert summarize_stream([{"type": "result", "subtype": "error_max_budget_usd"}])["cap_hit"] == "budget"
    assert summarize_stream([])["result_event"] is False


def test_should_copy_keeps_source_and_drops_deps_audio_and_skill():
    assert should_copy(Path("app.py"), 100)
    assert should_copy(Path("src/client.ts"), 100)
    assert not should_copy(Path(".venv/lib/x.py"), 100)
    assert not should_copy(Path("node_modules/ws/index.js"), 100)
    assert not should_copy(Path("sample.wav"), 100)
    assert not should_copy(Path(".claude/skills/assemblyai/SKILL.md"), 100)
    assert should_copy(Path(".claude/settings.json"), 100)
    assert not should_copy(Path("big.log"), 10 * 1024 * 1024)


def test_seed_is_copied_with_lf_endings(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "run.sh").write_bytes(b"#!/bin/sh\r\necho hi\r\n")
    _copy_lf(src, dst)
    assert (dst / "run.sh").read_bytes() == b"#!/bin/sh\necho hi\n"


def test_claude_args_turn_off_web_and_pin_caps():
    args = claude_args("some-model", 0.25)
    assert args[:2] == ["claude", "-p"]
    assert all(t in args for t in DISALLOWED_TOOLS)
    assert args[args.index("--max-turns") + 1] == str(MAX_TURNS)
    assert args[args.index("--max-budget-usd") + 1] == "0.25"
    assert "--strict-mcp-config" in args and "--max-budget-usd" not in claude_args("m", None)
