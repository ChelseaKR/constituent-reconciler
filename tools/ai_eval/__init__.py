"""Eval harness for the AI assistant package (docs/adr/0014).

Every eval here writes a result carrying full provenance (provider, model,
prompt_version, commit, date) via :mod:`provenance`, and
``tests/test_ai_eval_provenance.py`` rejects a committed
``eval/ai/results.json`` missing any of it. Fixtures are synthetic only
(:mod:`fixtures`) -- no real constituent data is ever sent to a model
provider from this package.
"""

from __future__ import annotations
