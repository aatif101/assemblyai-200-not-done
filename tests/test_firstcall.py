import json

from twohundred.firstcall import answered, first_working_call
from twohundred.judge import build_prompt, source_files, verdict


def _log(tmp_path, cmds):
    lines = []
    for t, command, code, out in cmds:
        lines.append(json.dumps({"t": t, "msg": {"type": "item.completed", "item": {
            "type": "command_execution", "command": command, "exit_code": code, "aggregated_output": out}}}))
    p = tmp_path / "agent.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


UID = "4bee2c01-fa20-40c0-b71d-44e72d297b9d"


def test_install_read_and_tests_do_not_count(tmp_path):
    p = _log(tmp_path, [
        (1, "/bin/bash -lc 'python -m pip install assemblyai'", 0, f"ok {UID}"),
        (2, "/bin/bash -lc 'cat assemblyai/SKILL.md'", 0, f'"type": "Begin", "id": "{UID}"'),
        (3, "/bin/bash -lc 'python -m unittest -v'", 0, f"transcript {UID}"),
        (4, "/bin/bash -lc './run.sh sample.wav out.json'", 1, "Traceback"),
    ])
    assert first_working_call(p)["seconds"] is None


def test_app_run_on_sample_counts_even_with_tests(tmp_path):
    p = _log(tmp_path, [(5, "/bin/bash -lc 'python -m unittest && ./run.sh sample.wav out.json'", 0, "Saved 5 turns")])
    r = first_working_call(p)
    assert r["seconds"] == 5 and r["app_run"]


def test_inline_code_with_server_answer_counts(tmp_path):
    p = _log(tmp_path, [
        (7, "/bin/bash -lc \"python - <<'PY'\nimport urllib.request\nPY\"", 0, "URLError"),
        (9, "/bin/bash -lc \"python - <<'PY'\nimport urllib.request\nPY\"", 0, f'{{"id": "{UID}", "status": "processing"}}'),
    ])
    assert first_working_call(p)["seconds"] == 9


def test_evidence_from_next_read(tmp_path):
    p = _log(tmp_path, [
        (3, "/bin/bash -lc 'python transcribe.py in.wav out.json'", 0, ""),
        (4, "/bin/bash -lc 'cat out.json'", 0, f'{{"transcript_id": "{UID}"}}'),
    ])
    r = first_working_call(p)
    assert r["seconds"] == 3 and r["evidence_from_next_read"]


def test_error_status_is_not_an_answer():
    assert not answered(f'{{"id": "{UID}", "status": "error"}}')
    assert answered("status 'completed'")


def test_judge_verdict_and_sources(tmp_path):
    (tmp_path / "assemblyai").mkdir()
    (tmp_path / "assemblyai" / "SKILL.md").write_text("x")
    (tmp_path / "transcribe.py").write_text("print(1)\n")
    (tmp_path / "run.sh").write_text("python transcribe.py\n")
    (tmp_path / "out.json").write_text("{}")
    files = source_files(tmp_path)
    assert [f.name for f in files] == ["run.sh", "transcribe.py"]
    prompt = build_prompt("Do X", ["A", "B"], files, tmp_path)
    assert "1. A" in prompt and "### transcribe.py" in prompt and "SKILL" not in prompt
    assert verdict([{"passed": True}, {"passed": True}], 2) is True
    assert verdict([{"passed": True}, {"passed": False}], 2) is False
    assert verdict([{"passed": True}], 2) is None
    assert verdict(None, 2) is None
