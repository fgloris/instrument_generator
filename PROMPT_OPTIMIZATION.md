# Prompt token optimization notes

## Runtime files

- `CLAUDE.md` is not loaded by `CodeWriter` or `VisionCodingAgent`; it costs zero API tokens.
- `AGENT_RULES.md` is loaded into runtime prompts and therefore must stay compact.
- Files under `workspace/docs/` are loaded into authoring/revision prompts.
- The initial writer receives the reference script; later visual review/revision calls no longer do.

## Removed duplication

1. Three-axis evaluation, `retake_views`, photometric-ignore rules, scoring, and severity policy now live only in
   `REVIEW_SYSTEM_PROMPT`.
2. `AGENT_RULES.md` now contains only the shared generated-script contract.
3. The review user message no longer repeats the decision flow already defined by the system prompt.
4. The initial user message no longer repeats environment-variable, render-view, and output-format requirements.
5. Repair calls omit the reference script and project docs; they retain the exact failed script, error log, toolkit,
   and compact script contract.
6. Historical issues use compact JSON and include only moderate/major/critical findings.

## Measured text payload change

Using `workspace/specs/beaker_low_250ml.yaml` and the reference script as a representative current script, excluding
image tokens and model output:

| Request | Before (characters) | After (characters) | Reduction |
|---|---:|---:|---:|
| Initial generation | 72,129 | 66,973 | 7.1% |
| Visual review + revision | 81,820 | 69,162 | 15.5% |
| Render-failure repair | 77,696 | 63,288 | 18.5% |

Character counts are not exact tokenizer counts, but they reliably show the relative reduction.

## Largest remaining context

`workspace/toolkit/lab_blender_toolkit.py` is still the largest prompt component (about 54k characters). A future,
more aggressive optimization could generate and send an API-signature/docstring index instead of the full toolkit.
This version keeps the full toolkit source to avoid reducing code-generation reliability before that compact API
index is validated.
