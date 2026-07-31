from lab_asset_agent.code_writer import CodeWriter


def test_tagged_code_writer_response_parses_complete_script():
    text = """<BLENDER_SCRIPT>
import bpy
import lab_blender_toolkit as lab
print('ok')
</BLENDER_SCRIPT>
<SUMMARY>
Adjusted the vessel profile.
</SUMMARY>"""

    script, summary = CodeWriter._parse_response(text)

    assert script.startswith("import bpy")
    assert "lab_blender_toolkit" in script
    assert summary == "Adjusted the vessel profile."


def test_legacy_json_code_writer_response_still_parses():
    text = '{"script":"import bpy\\nimport lab_blender_toolkit as lab\\n","summary":"legacy"}'

    script, summary = CodeWriter._parse_response(text)

    assert "import bpy" in script
    assert summary == "legacy"
