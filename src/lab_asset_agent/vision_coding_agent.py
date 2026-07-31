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
operations. Modify only the generated instrument script; supplied context is immutable."""


REVIEW_SYSTEM_PROMPT = (
    """You are a visual QA engineer and Blender 5.2 Python engineer. Judge all supplied renders jointly with the
target specification and exact script. Report only actionable `moderate`, `major`, or `critical` issues; omit
minor observations and cosmetic preferences. Every issue must use exactly one axis:

- `camera_coverage`: visibility gate. Check full-object coverage, useful angle diversity, and readable scale. If the
  views are jointly insufficient, choose `retake_views`; do not infer hidden geometry defects. A single weak view is
  acceptable when the others are sufficient.
- `shape_silhouette`: most important axis. Check real-world form, outer contour, proportions, openings, rims, wall
  thickness, base, joints, side parts, physical connections, and topology.
- `graduations`: check visible ticks/labels/attachment and the exact volume-integration code, including the true
  zero-volume origin. Non-uniform equal-volume spacing is normal for non-uniform vessels.

Decisions:
- `retake_views`: score 0; return a complete script changing only camera placement, target/lens, and diagnostic
  view definitions. Preserve geometry, materials, markings, dimensions, and graduation calculations exactly.
- `revise`: coverage is sufficient and at least one moderate-or-higher shape or graduation issue requires repair;
  return the complete corrected script.
- `pass`: coverage is sufficient and no moderate-or-higher defect remains.

When coverage is sufficient, weight shape/silhouette about 70% and graduations about 30%. Ignore darkness, weak
reflections/highlights, apparent transparency, exposure, contrast, shadows, and other lighting/render-style
differences. Never change camera or rendering merely to hide a real defect.

Return exactly these plain-text tags, without Markdown fences:

<REVIEW_JSON>
A valid JSON object with verdict, overall_score, issues, preserve, and summary. Each issue contains review_axis,
severity (moderate|major|critical), view_names, observation, likely_cause, and recommended_change.
</REVIEW_JSON>
<BLENDER_SCRIPT>
For revise/retake_views only: the complete executable Python file, never a patch. Omit this section for pass.
</BLENDER_SCRIPT>

"""
    + _COMMON_SAFETY
)


REPAIR_SYSTEM_PROMPT = (
    """You are a Blender 5.2 Python engineer repairing a script that failed validation or execution. Diagnose the
exact script and error log, make the smallest robust fix, and preserve correct geometry. Return exactly:

<SUMMARY>
A concise root-cause and repair summary.
</SUMMARY>
<BLENDER_SCRIPT>
The complete corrected executable Python file, never a patch.
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
    """One GPT call reviews renders and chooses pass, revision, or a view retake."""

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
        self.docs = ""
        self.rules = ""

    async def start(self) -> None:
        self.toolkit = self.config.paths.toolkit.read_text(encoding="utf-8")
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
        human_hint: str | None = None,
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
                    human_hint=human_hint,
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
        human_hint: str | None = None,
    ) -> ScriptRepair:
        prompt = f"""The current script failed deterministic validation or Blender execution at iteration
{iteration}. There are no useful render images, so diagnose the code and log directly.

TARGET SPEC:
{json.dumps(spec.model_dump(mode='json'), ensure_ascii=False, indent=2)}

{self._repair_context()}

{self._issue_history_context(issue_history or [])}

{self._human_hint_context(human_hint)}

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
        review_payload = extract_json_object(review_match.group(1))
        if not isinstance(review_payload, dict):
            raise RuntimeError("GPT <REVIEW_JSON> must contain a JSON object.")
        issues_payload = review_payload.get("issues", [])
        if not isinstance(issues_payload, list):
            raise RuntimeError("GPT review `issues` must be a JSON array.")

        actionable_issues: list[dict] = []
        for issue_index, issue in enumerate(issues_payload, start=1):
            if not isinstance(issue, dict):
                raise RuntimeError(f"GPT review issue {issue_index} must be a JSON object.")
            # Keep old manifests readable, but never expose/store new minor findings.
            # Minor observations are deliberately omitted from the actionable protocol.
            if issue.get("severity") == "minor":
                continue
            if "review_axis" not in issue:
                raise RuntimeError(
                    f"GPT review issue {issue_index} is missing required `review_axis`."
                )
            actionable_issues.append(issue)

        review_payload = dict(review_payload)
        review_payload["issues"] = actionable_issues
        review = VLMReview.model_validate(review_payload)

        if review.verdict == "revise" and not review.issues:
            raise RuntimeError(
                "verdict=revise requires at least one moderate-or-higher actionable issue."
            )

        if review.verdict == "retake_views":
            if not review.issues:
                raise RuntimeError("verdict=retake_views requires at least one camera_coverage issue.")
            if any(issue.review_axis != "camera_coverage" for issue in review.issues):
                raise RuntimeError(
                    "verdict=retake_views may contain only camera_coverage issues; "
                    "uncertain geometry must not be diagnosed before retaking views."
                )

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

        if review.verdict in {"revise", "retake_views"} and script is None:
            raise RuntimeError(
                f"GPT returned verdict={review.verdict} but omitted the complete <BLENDER_SCRIPT>."
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
        human_hint: str | None,
    ) -> str:
        return f"""Iteration: {iteration}
Image order / view filenames: {[path.name for path in images]}
Pass threshold configured by the orchestrator: {self.config.loop.pass_score}/10

TARGET SPECIFICATION:
{json.dumps(spec.model_dump(mode='json'), ensure_ascii=False, indent=2)}

{self._revision_context()}

{self._issue_history_context(issue_history)}

{self._human_hint_context(human_hint)}

CURRENT EXACT INSTRUMENT SCRIPT THAT PRODUCED THESE IMAGES:
```python
{script}
```
"""

    @staticmethod
    def _human_hint_context(human_hint: str | None) -> str:
        if not human_hint or not human_hint.strip():
            return "HUMAN GUIDANCE FOR THIS ITERATION:\n(none)"
        return f"""HUMAN GUIDANCE FOR THIS ITERATION:
{human_hint.strip()}
Apply it when compatible with the specification and immutable-context constraints.
"""

    @staticmethod
    def _issue_history_context(issue_history: list[HistoricalVisualIssue]) -> str:
        """Serialize prior moderate-or-higher issues as compact regression memory."""

        payload = json.dumps(
            [item.model_dump(mode="json") for item in issue_history],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return f"""PRIOR MODERATE-OR-HIGHER ISSUES (verify; do not blindly repeat):
{payload}
Preserve confirmed fixes and address recurring code causes. Ignore historical photometric comments.
"""

    def _revision_context(self) -> str:
        """Context needed for multimodal review and direct script revision."""

        return f"""SCRIPT CONTRACT:
{self.rules}

BLENDER/PROJECT DOCUMENTATION:
{self.docs}

SHARED TOOLKIT:
```python
{self.toolkit}
```"""

    def _repair_context(self) -> str:
        """Minimal context for deterministic validation/render failures."""

        return f"""SCRIPT CONTRACT:
{self.rules}

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
