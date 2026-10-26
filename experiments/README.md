# Experiments

One folder per hypothesis: `experiments/<H-0001-short-name>/`. Start by copying `experiments/_template/` and `config/research.example.toml`. Conventions: `.claude/skills/fxra-research/SKILL.md`.

```
hypothesis.md    claim, rationale, falsification test, costs it must survive, trial count
research.toml    copy of config/research.example.toml, filled in
notes.md         optional working notes
```

Run results stay in `runs/<run_id>/` (gitignored). Reviewed write-ups go to `reports/`. Lifecycle and gates: `docs/research-lifecycle.md`.
