import asyncio
import base64
from pathlib import Path

from PIL import Image

from lab_asset_agent.models import AppConfig, InstrumentSpec
from lab_asset_agent.vision_coding_agent import VisionCodingAgent


class FakeCompatibleClient:
    def __init__(self, response: str):
        self.response = response
        self.messages = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return self.response


def _spec() -> InstrumentSpec:
    return InstrumentSpec.model_validate(
        {
            "id": "beaker_test",
            "name_zh": "烧杯",
            "name_en": "Beaker",
            "category": "glassware",
            "material": "borosilicate_glass",
            "nominal_volume_ml": 250,
            "appearance": {
                "summary": "低型透明烧杯",
                "required_features": ["倒液嘴"],
                "forbidden_features": [],
            },
        }
    )


def test_whole_nominal_volume_is_preserved_as_integer():
    spec = _spec()
    assert spec.nominal_volume_ml == 250
    assert isinstance(spec.nominal_volume_ml, int)
    assert spec.model_dump(mode="json")["nominal_volume_ml"] == 250


def _config(tmp_path: Path) -> AppConfig:
    toolkit = tmp_path / "toolkit.py"
    reference = tmp_path / "reference.py"
    docs = tmp_path / "docs"
    rules = tmp_path / "rules.md"
    generated = tmp_path / "generated"
    runs = tmp_path / "runs"
    toolkit.write_text("def mm(x): return x / 1000\n", encoding="utf-8")
    reference.write_text("import bpy\n", encoding="utf-8")
    docs.mkdir()
    (docs / "notes.md").write_text("Blender 5.2 notes", encoding="utf-8")
    rules.write_text("Do not edit toolkit.", encoding="utf-8")
    return AppConfig.model_validate(
        {
            "project_root": str(tmp_path),
            "blender": {},
            "models": {
                "initial_generator": "gpt",
                "iteration_agent": {
                    "base_url": "https://api.vectorengine.ai/v1",
                    "api_key_env": "IGNORED_IN_TEST",
                    "model": "gpt-4o",
                    "max_tokens": 12000,
                    "max_images": 4,
                },
            },
            "paths": {
                "toolkit": str(toolkit),
                "reference": str(reference),
                "docs_dir": str(docs),
                "rules": str(rules),
                "generated_dir": str(generated),
                "runs_dir": str(runs),
            },
        }
    )


def _revision_response(score: float = 7.5) -> str:
    return f'''<REVIEW_JSON>
{{
  "verdict": "revise",
  "overall_score": {score},
  "issues": [{{"severity": "major", "view_names": ["view.png"], "observation": "small spout", "likely_cause": "radial extension", "recommended_change": "increase extension"}}],
  "preserve": ["body"],
  "summary": "spout needs correction"
}}
</REVIEW_JSON>
<BLENDER_SCRIPT>
import bpy
import lab_blender_toolkit as lab

def build_asset():
    return bpy.context.object

if __name__ == "__main__":
    build_asset()
</BLENDER_SCRIPT>'''


def test_gpt_receives_exact_code_and_images_and_returns_revision(tmp_path: Path):
    image_path = tmp_path / "view.png"
    Image.new("RGBA", (64, 48), (0, 0, 0, 0)).save(image_path)
    script_path = tmp_path / "instrument.py"
    script_path.write_text(
        "import bpy\nimport lab_blender_toolkit as lab\nCURRENT_MAGIC = 123\n",
        encoding="utf-8",
    )

    fake = FakeCompatibleClient(_revision_response())
    agent = VisionCodingAgent(_config(tmp_path), client=fake)
    asyncio.run(agent.start())
    decision = asyncio.run(agent.review_and_revise(_spec(), script_path, [image_path], 1))

    assert decision.review.verdict == "revise"
    assert decision.review.overall_score == 7.5
    assert decision.summary == "spout needs correction"
    assert "import bpy" in decision.revised_script
    content = fake.messages[1]["content"]
    prompt = content[0]["text"]
    assert "CURRENT_MAGIC = 123" in prompt
    assert "SHARED TOOLKIT" in prompt
    image_item = next(item for item in content if item["type"] == "image_url")
    url = image_item["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1])[:2] == b"\xff\xd8"


def test_pass_decision_requires_no_script_and_uses_json_summary_only():
    text = '''<REVIEW_JSON>
{"verdict":"pass","overall_score":9.2,"issues":[],"preserve":[],"summary":"good"}
</REVIEW_JSON>
<SUMMARY>This stray duplicate tag is ignored.</SUMMARY>'''
    decision = VisionCodingAgent._parse_decision(text)
    assert decision.review.verdict == "pass"
    assert decision.review.overall_score == 9.2
    assert decision.summary == "good"
    assert decision.revised_script is None


def test_review_json_has_no_dimensions():
    decision = VisionCodingAgent._parse_decision(_revision_response(8.4))
    assert decision.review.overall_score == 8.4
    assert decision.review.issues[0].severity == "major"
    assert not hasattr(decision.review, "dimensions")


def test_revise_without_complete_script_is_rejected():
    text = '''<REVIEW_JSON>
{"verdict":"revise","overall_score":7,"issues":[],"preserve":[],"summary":"fix"}
</REVIEW_JSON>'''
    try:
        VisionCodingAgent._parse_decision(text)
    except RuntimeError as exc:
        assert "omitted" in str(exc)
    else:
        raise AssertionError("Expected missing revised script to be rejected")
