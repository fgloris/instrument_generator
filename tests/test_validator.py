from pathlib import Path

from lab_asset_agent.validator import validate_generated_script


def test_rejects_forbidden_import(tmp_path: Path):
    path = tmp_path / "bad.py"
    path.write_text("import bpy\nimport subprocess\n", encoding="utf-8")
    result = validate_generated_script(path)
    assert not result.ok
    assert any("subprocess" in error for error in result.errors)


def test_rejects_destructive_os_call(tmp_path: Path):
    path = tmp_path / "bad_delete.py"
    path.write_text(
        '''import os\nimport bpy\nimport lab_blender_toolkit as lab\n\ndef build_asset():\n    os.remove("x")\n\nif __name__ == "__main__":\n    build_asset()\n\n# LAB_ASSET_OUTPUT_DIR\n# lab.render_views\n# lab.save_blend\n''',
        encoding="utf-8",
    )
    result = validate_generated_script(path)
    assert not result.ok
    assert any("os.remove" in error for error in result.errors)


def test_accepts_generated_script_contract(tmp_path: Path):
    path = tmp_path / "good.py"
    path.write_text(
        '''import os\nimport bpy\nimport lab_blender_toolkit as lab\n\ndef build_asset():\n    output = os.getenv("LAB_ASSET_OUTPUT_DIR", ".")\n    lab.render_views(None, None, [], output, "asset")\n    lab.save_blend("asset.blend")\n    return bpy.context.object\n\nif __name__ == "__main__":\n    build_asset()\n''',
        encoding="utf-8",
    )
    result = validate_generated_script(path)
    assert result.ok, result.errors
