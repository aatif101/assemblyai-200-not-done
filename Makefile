# 200 != done. Phase 1 targets. `make phase1` is the one command; the rest are its steps.
PY      ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,.venv/bin/python)
BACKEND ?= espeak
REPEATS ?= 2

.PHONY: setup check test secrets plan fixtures validate-fixtures killtest probes classify phase1 hooks docs precheck precheck-decide oracle-check image pilot sweep judge grade redact-ids all

setup:               ## create the virtualenv and install dependencies
	python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.txt
	@$(MAKE) --no-print-directory hooks

hooks:               ## run the secret check before every commit
	@if [ -d .git ]; then printf '#!/bin/sh\nexec .venv/bin/python scripts/check_secrets.py --staged\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit; fi

test:                ## offline unit tests (no network, no key)
	$(PY) -m pytest -q

secrets:             ## fail if a credential appears anywhere in the repo
	$(PY) scripts/check_secrets.py

check: test secrets  ## everything that runs without a key

plan:                ## print every request the probes would send (offline)
	$(PY) -m twohundred plan

docs:                ## regenerate docs/PROBES.md from the probe list
	$(PY) -m twohundred probes list

fixtures:            ## rebuild audio fixtures (BACKEND=espeak | aai-tts)
	$(PY) -m twohundred fixtures build --backend $(BACKEND)

validate-fixtures:   ## live: confirm each clip carries its ground truth
	$(PY) -m twohundred fixtures validate

killtest:            ## live: the 10-probe kill test, classified
	$(PY) -m twohundred probes run --kill --repeats $(REPEATS)
	$(PY) -m twohundred probes classify --kill
	@$(MAKE) --no-print-directory secrets

probes:              ## live: all probes
	$(PY) -m twohundred probes run --repeats $(REPEATS)

classify:            ## offline: rebuild the matrix from results/phase1/raw
	$(PY) -m twohundred probes classify

phase1: check        ## live: fixtures check, all probes, matrix, secret check
	-$(PY) -m twohundred fixtures validate
	$(PY) -m twohundred probes run --repeats $(REPEATS)
	$(PY) -m twohundred probes classify --kill
	$(PY) -m twohundred probes classify
	@$(MAKE) --no-print-directory secrets

# ---------------------------------------------------------------- Phase 2

precheck:            ## live: Q3 (British spelling gradable?) and Q4 (summarization works?), ~7 short transcripts
	$(PY) -m twohundred precheck run
	@$(MAKE) --no-print-directory secrets

precheck-decide:     ## offline: re-decide Q3 and Q4 from results/phase2/precheck/raw
	$(PY) -m twohundred precheck decide

oracle-check:        ## live: reference solutions (right and wrong) through each task's oracle, ~4 min
	$(PY) -m twohundred oracle-check
	@$(MAKE) --no-print-directory secrets

# Agent runs. MODEL is pinned and recorded per run. BUDGET is the per-run cap (claude --max-budget-usd),
# TOTAL the stop-starting-new-runs line over everything already spent. Both are optional.
MODEL    ?= gpt-6.1-sol
BUDGET   ?=
TOTAL    ?=
PARALLEL ?= 1
AGENT    ?= codex
EFFORT   ?= medium
RUNFLAGS = --agent $(AGENT) --effort $(EFFORT) --model $(MODEL) $(if $(BUDGET),--budget-usd $(BUDGET)) $(if $(TOTAL),--total-usd $(TOTAL)) --parallel $(PARALLEL)

image:               ## build the runner image (Docker must be running)
	docker build -t twohundred-runner runner

pilot: image         ## live: 12 runs, one per cell, into results/phase2/codex/pilot (AGENT=claude: results/phase2/pilot)
	$(PY) runner/run_all.py pilot $(RUNFLAGS)
	@$(MAKE) --no-print-directory secrets

sweep: image         ## live: 120 runs into results/phase2/codex/runs (AGENT=claude: results/phase2/runs)
	$(PY) runner/run_all.py sweep $(RUNFLAGS)
	@$(MAKE) --no-print-directory secrets

# ---------------------------------------------------------------- Phase 3

judge: image         ## Codex plan: looks_right judge, every sweep run twice plus the skill repo's committed outputs (~250 calls)
	$(PY) runner/judge_all.py --parallel 4
	@$(MAKE) --no-print-directory secrets

grade:               ## offline: grades.csv, headline numbers, continuity table and both charts into results/phase3
	$(PY) -m twohundred.pilotreport codex/runs
	$(PY) -m twohundred.phase3

redact-ids:          ## offline: remove account identifiers from saved records (F6)
	$(PY) scripts/redact_account_ids.py

# Everything from scratch, in order. Live steps need ASSEMBLYAI_API_KEY, Docker and a signed-in Codex CLI.
all: setup check phase1 precheck oracle-check sweep judge redact-ids grade check
