from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .code_writer import CodeWriter
from .models import AppConfig, HistoricalVisualIssue, InstrumentSpec, VLMReview
from .openai_compatible import OpenAICompatibleClient
from .utils import extract_json_object


_COMMON_SAFETY = """Never use network access, subprocesses, shell commands, eval, exec, or destructive filesystem
operations. Only revise the generated instrument script. The shared toolkit, reference script, rules, and
documentation are immutable."""


REVIEW_SYSTEM_PROMPT = (
    """You are simultaneously:
1. a visual QA engineer for procedurally generated laboratory instruments; and
2. an expert Blender 5.2 Python engineer who can directly repair the exact script that produced the renders.

Judge all supplied views jointly against the target specification and the exact current script. Evaluate
geometric and structural correctness: proportions, silhouette, openings, rims, wall thickness, side parts,
connections, graduations, and topology. Do not invent unseen defects.

IMPORTANT Photometric Ignore Policy:
- Do not report, score, or revise merely because a render is dark, has weak reflections/highlights,
  has low apparent transparency, or has imperfect exposure/contrast/shadows. Treat those as environment-lighting
  artifacts rather than asset defects.
- Do not change lighting, exposure, world strength, background, camera, or glass roughness only to make the image
  prettier. If the geometry is readable in the supplied views, judge geometry only.

About Graduations: 
- verify from the code, not just by visual inspection.
- Ensure in the code that the graduations are derived from volume integration.
- For vessels with non-uniform cross-sections, non-uniform spacing is normal.
- Focus on errors such as ticks detaching or floating off the surface, overlapping labels or missing marks.

Return plain text with these tags and no Markdown fences:

<REVIEW_JSON>
A valid JSON object containing: verdict (pass|revise), overall_score (0-10), issues [{severity,
view_names, observation, likely_cause, recommended_change}], preserve [fine_partitions_to_keep_unchanged], summary.
</REVIEW_JSON>
<BLENDER_SCRIPT>
When verdict=revise, the complete revised executable Python file. Never return a patch.
When verdict=pass, omit this entire section.
</BLENDER_SCRIPT>

"""
    + _COMMON_SAFETY
)


REPAIR_SYSTEM_PROMPT = (
    """You are an expert Blender 5.2 Python engineer fixing a generated instrument script
that failed deterministic validation or Blender execution. There are no render images, so diagnose the script
and the error log directly.

If the target spec's nominal volume exceeds the inner profile capacity (e.g. "Requested N mL exceeds profile
capacity"), enlarge the inner profile geometry so the profile genuinely holds the nominal volume; never
silently relabel the volume to fit a too-small profile. Preserve the wall thickness and overall proportions.

Return plain text with these tags and no Markdown fences:

<SUMMARY>
A concise root-cause and repair summary.
</SUMMARY>
<BLENDER_SCRIPT>
The complete corrected executable Python file. Never return a patch.
</BLENDER_SCRIPT>

"""
    + _COMMON_SAFETY
)


@dataclass
class VisionCodeDecision:
    review: VLMReview
    revised_script: str | None
    summary: str
    raw_response: str


@dataclass
class ScriptRepair:
    script: str
    summary: str
    raw_response: str


class VisionCodingAgent:
    """One GPT call reviews renders and directly writes the next candidate script."""

    def __init__(
        self,
        config: AppConfig,
        *,
        client: OpenAICompatibleClient | None = None,
    ) -> None:
        self.config = config
        self.model_config = config.models.iteration_agent
        self.client = client or OpenAICompatibleClient(self.model_config)
        self.toolkit = ""
        self.reference = ""
        self.docs = ""
        self.rules = ""

    async def start(self) -> None:
        self.toolkit = self.config.paths.toolkit.read_text(encoding="utf-8")
        self.reference = self.config.paths.reference.read_text(encoding="utf-8")
        self.rules = self.config.paths.rules.read_text(encoding="utf-8")
        doc_parts: list[str] = []
        for path in sorted(self.config.paths.docs_dir.rglob("*")):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".py"}:
                doc_parts.append(
                    f"\n### {path.relative_to(self.config.paths.docs_dir)}\n"
                    + path.read_text(encoding="utf-8", errors="replace")
                )
        self.docs = "\n".join(doc_parts)

    async def close(self) -> None:
        return None

    async def review_and_revise(
        self,
        spec: InstrumentSpec,
        script_path: Path,
        images: list[Path],
        iteration: int,
        issue_history: list[HistoricalVisualIssue] | None = None,
    ) -> VisionCodeDecision:
        selected = self._select_images(images)
        content: list[dict] = [
            {
                "type": "text",
                "text": self._review_and_revision_prompt(
                    spec=spec,
                    script=script_path.read_text(encoding="utf-8"),
                    images=selected,
                    iteration=iteration,
                    issue_history=issue_history or [],
                ),
            }
        ]
        for image_path in selected:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._image_data_url(image_path)},
                }
            )

        # Deliberately send no response_format. The response contains JSON review
        # plus a long Python file, which is more reliable as tagged plain text.
        partial_path = script_path.parent / "gpt_review_and_code_response.partial.txt"
        final_response_path = script_path.parent / "gpt_review_and_code_response.txt"
        text = await asyncio.to_thread(
            self.client.chat,
            [
                {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            stream_label=(
                f"GPT review+coder iteration {iteration} "
                f"({self.model_config.model})"
            ),
            stream_output_path=partial_path,
        )
        final_response_path.write_text(text, encoding="utf-8")
        partial_path.unlink(missing_ok=True)
        return self._parse_decision(text)

    async def repair_render_failure(
        self,
        spec: InstrumentSpec,
        script_path: Path,
        iteration: int,
        error: str,
        issue_history: list[HistoricalVisualIssue] | None = None,
    ) -> ScriptRepair:
        prompt = f"""The current script failed deterministic validation or Blender execution at iteration
{iteration}. There are no useful render images, so diagnose the code and log directly.

TARGET SPEC:
{json.dumps(spec.model_dump(mode='json'), ensure_ascii=False, indent=2)}

{self._shared_context()}

{self._issue_history_context(issue_history or [])}

CURRENT EXACT SCRIPT:
```python
{script_path.read_text(encoding='utf-8')}
```

FAILURE EVIDENCE:
```
{error}
```

Make the smallest robust fix and preserve correct geometry.
"""
        partial_path = script_path.parent / "repair_agent_response.partial.txt"
        final_response_path = script_path.parent / "repair_agent_response.txt"
        text = await asyncio.to_thread(
            self.client.chat,
            [
                {"role": "system", "content": REPAIR_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            stream_label=(
                f"GPT render repair iteration {iteration} "
                f"({self.model_config.model})"
            ),
            stream_output_path=partial_path,
        )
        final_response_path.write_text(text, encoding="utf-8")
        partial_path.unlink(missing_ok=True)
        script, summary = CodeWriter._parse_response(text)
        return ScriptRepair(script=script, summary=summary, raw_response=text)

    def write_revision(self, decision: VisionCodeDecision, candidate_path: Path) -> None:
        if decision.revised_script is None:
            raise RuntimeError("The GPT decision did not contain a revised script.")
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(decision.revised_script.rstrip() + "\n", encoding="utf-8")

    @classmethod
    def _parse_decision(cls, text: str) -> VisionCodeDecision:
        review_match = re.search(
            r"<REVIEW_JSON>\s*(.*?)\s*</REVIEW_JSON>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not review_match:
            raise RuntimeError("GPT response is missing <REVIEW_JSON>.")
        review = VLMReview.model_validate(extract_json_object(review_match.group(1)))

        summary = review.summary

        script_match = re.search(
            r"<BLENDER_SCRIPT>\s*(.*?)\s*</BLENDER_SCRIPT>",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        script = None
        if script_match:
            script = CodeWriter._strip_code_fence(script_match.group(1))
            if not script:
                script = None

        if review.verdict == "revise" and script is None:
            raise RuntimeError(
                "GPT returned verdict=revise but omitted the complete <BLENDER_SCRIPT>."
            )
        if script is not None and not CodeWriter._looks_like_python_script(script):
            raise RuntimeError("GPT returned a <BLENDER_SCRIPT> that is not recognizable as Blender Python.")

        return VisionCodeDecision(
            review=review,
            revised_script=script,
            summary=summary,
            raw_response=text,
        )

    def _review_and_revision_prompt(
        self,
        *,
        spec: InstrumentSpec,
        script: str,
        images: list[Path],
        iteration: int,
        issue_history: list[HistoricalVisualIssue],
    ) -> str:
        return f"""Iteration: {iteration}
Image order / view filenames: {[path.name for path in images]}
Pass threshold configured by the orchestrator: {self.config.loop.pass_score}/10

TARGET SPECIFICATION:
{json.dumps(spec.model_dump(mode='json'), ensure_ascii=False, indent=2)}

{self._shared_context()}

{self._issue_history_context(issue_history)}

CURRENT EXACT INSTRUMENT SCRIPT THAT PRODUCED THESE IMAGES:
```python
{script}
```

Evaluate both the renders and the code. If the asset genuinely passes, return verdict=pass and no script.
If it needs any revision, return verdict=revise and directly produce the complete corrected script in the same
response. Trace each important visual defect to likely parameters or geometry in the current script. Preserve
already-correct features, prioritize higher-severity geometry defects, and ignore cosmetic photometric issues.
"""

    @staticmethod
    def _issue_history_context(issue_history: list[HistoricalVisualIssue]) -> str:
        """Serialize prior moderate-or-higher issues as a regression checklist.

        Historical issues are evidence of what went wrong before, not proof that
        the current script still has the defect. The model must verify each item
        against the current code/renders while avoiding regressions.
        """

        if not issue_history:
            payload = "[]"
        else:
            payload = json.dumps(
                [item.model_dump(mode="json") for item in issue_history],
                ensure_ascii=False,
                indent=2,
            )
        return f"""PRIOR MODERATE-OR-HIGHER ISSUE HISTORY (REGRESSION MEMORY):
{payload}

Use this complete history as a regression checklist:
- Verify each historical issue against the current exact script and current renders; do not blindly repeat it.
- Preserve fixes that are already correct and do not reintroduce previously reported defects.
- If a historical issue recurs, explicitly address its likely code cause in the next script.
- Historical comments about darkness, weak reflections/highlights, exposure, or other environment lighting are
  non-actionable under the photometric ignore policy and must not drive revisions.
"""

    def _shared_context(self) -> str:
        return f"""AGENT RULES:
{self.rules}

BLENDER/PROJECT DOCUMENTATION:
{self.docs}

REFERENCE INSTRUMENT SCRIPT:
```python
{self.reference}
```

SHARED TOOLKIT:
```python
{self.toolkit}
```"""

    def _select_images(self, images: list[Path]) -> list[Path]:
        if not images:
            raise ValueError("No render images were supplied to the GPT iteration agent.")
        if len(images) <= self.model_config.max_images:
            return images
        if self.model_config.max_images == 1:
            return [images[len(images) // 2]]
        last = len(images) - 1
        indices = sorted(
            {round(i * last / (self.model_config.max_images - 1)) for i in range(self.model_config.max_images)}
        )
        return [images[index] for index in indices]

    def _image_data_url(self, path: Path) -> str:
        with Image.open(path) as source:
            source.thumbnail((self.model_config.max_image_side, self.model_config.max_image_side))
            if source.mode in {"RGBA", "LA"} or "transparency" in source.info:
                rgba = source.convert("RGBA")
                image = Image.new("RGB", rgba.size, (255, 255, 255))
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = source.convert("RGB")
            buffer = io.BytesIO()
            image.save(
                buffer,
                format="JPEG",
                quality=self.model_config.jpeg_quality,
                optimize=True,
            )
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
