from types import SimpleNamespace

import pytest

from lab_asset_agent.models import OpenAICompatibleModelConfig
from lab_asset_agent.openai_compatible import OpenAICompatibleClient, make_strict_json_schema


class FakeCompletions:
    def __init__(self, effects=None):
        self.calls = []
        self.effects = list(effects or [])

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.effects:
            effect = self.effects.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect
        message = SimpleNamespace(content='{"value": 1}', reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeOpenAI:
    def __init__(self, effects=None):
        self.chat = SimpleNamespace(completions=FakeCompletions(effects))


def response(text='{"value": 1}'):
    message = SimpleNamespace(content=text, reasoning_content=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def config(**overrides):
    values = dict(
        base_url="https://example.invalid/v1",
        api_key_env="IGNORED_IN_TEST",
        model="gpt-4o",
        max_tokens=1234,
        temperature=0.2,
    )
    values.update(overrides)
    return OpenAICompatibleModelConfig(**values)


def test_shared_client_uses_configured_model_and_endpoint_contract():
    sdk = FakeOpenAI()
    client = OpenAICompatibleClient(config(), client=sdk)

    text = client.chat(
        [{"role": "user", "content": "hello"}],
        response_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
        schema_name="demo",
    )

    assert text == '{"value": 1}'
    call = sdk.chat.completions.calls[0]
    assert call["model"] == "gpt-4o"
    assert call["max_tokens"] == 1234
    assert call["temperature"] == 0.2
    assert call["messages"][0]["content"] == "hello"
    assert call["response_format"]["type"] == "json_schema"


def test_strict_schema_recursively_sets_additional_properties_and_required():
    source = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {"$ref": "#/$defs/Item"},
            },
            "note": {"type": "string"},
        },
        "$defs": {
            "Item": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["name"],
            }
        },
    }

    normalized = make_strict_json_schema(source)

    assert "additionalProperties" not in source
    assert normalized["additionalProperties"] is False
    assert normalized["required"] == ["items", "note"]
    item = normalized["$defs"]["Item"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["name", "score"]


def test_json_object_mode_never_sends_json_schema():
    sdk = FakeOpenAI()
    client = OpenAICompatibleClient(config(response_format_mode="json_object"), client=sdk)

    client.chat(
        [{"role": "user", "content": "return JSON"}],
        response_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
    )

    call = sdk.chat.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}


class FakeGatewayError(Exception):
    def __init__(self, message: str, status_code: int = 429):
        super().__init__(message)
        self.status_code = status_code
        self.body = {"error": {"message": message}}


def test_mislabelled_429_schema_error_falls_back_to_json_object():
    sdk = FakeOpenAI(
        [
            FakeGatewayError(
                "Invalid schema for response_format 'demo': "
                "'additionalProperties' is required to be supplied and to be false."
            ),
            response(),
        ]
    )
    client = OpenAICompatibleClient(config(response_format_mode="auto"), client=sdk)

    text = client.chat(
        [{"role": "user", "content": "return JSON"}],
        response_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
        schema_name="demo",
    )

    assert text == '{"value": 1}'
    assert len(sdk.chat.completions.calls) == 2
    assert sdk.chat.completions.calls[0]["response_format"]["type"] == "json_schema"
    assert sdk.chat.completions.calls[1]["response_format"] == {"type": "json_object"}


def test_genuine_rate_limit_is_not_swallowed():
    sdk = FakeOpenAI([FakeGatewayError("Rate limit exceeded. Please retry later.")])
    client = OpenAICompatibleClient(config(), client=sdk)

    with pytest.raises(FakeGatewayError, match="Rate limit exceeded"):
        client.chat(
            [{"role": "user", "content": "return JSON"}],
            response_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
        )

    assert len(sdk.chat.completions.calls) == 1


def test_per_call_mode_override_forces_json_object():
    sdk = FakeOpenAI()
    client = OpenAICompatibleClient(config(response_format_mode="auto"), client=sdk)

    client.chat(
        [{"role": "user", "content": "return JSON"}],
        response_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
        response_format_mode="json_object",
    )

    assert sdk.chat.completions.calls[0]["response_format"] == {"type": "json_object"}



def stream_chunk(*, content=None, reasoning_content=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning_content)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def test_streaming_aggregates_chunks_prints_final_content_and_saves_partial(tmp_path, capsys):
    sdk = FakeOpenAI(
        [[
            stream_chunk(reasoning_content="thinking-a"),
            stream_chunk(reasoning_content="thinking-b" * 40),
            stream_chunk(content="<BLENDER_"),
            stream_chunk(content="SCRIPT>ok</BLENDER_SCRIPT>"),
        ]]
    )
    client = OpenAICompatibleClient(
        config(stream=True, stream_to_terminal=True, stream_reasoning="progress"),
        client=sdk,
    )
    partial = tmp_path / "response.partial.txt"

    text = client.chat(
        [{"role": "user", "content": "hello"}],
        stream_label="unit test",
        stream_output_path=partial,
    )

    assert text == "<BLENDER_SCRIPT>ok</BLENDER_SCRIPT>"
    assert partial.read_text(encoding="utf-8") == text
    assert sdk.chat.completions.calls[0]["stream"] is True
    output = capsys.readouterr().out
    assert "Streaming model response: unit test" in output
    assert "<BLENDER_SCRIPT>ok</BLENDER_SCRIPT>" in output
    assert "reasoning received" in output


def test_stream_reasoning_full_prints_reasoning(capsys):
    sdk = FakeOpenAI(
        [[
            stream_chunk(reasoning_content="visible reasoning"),
            stream_chunk(content="final"),
        ]]
    )
    client = OpenAICompatibleClient(
        config(stream=True, stream_to_terminal=True, stream_reasoning="full"),
        client=sdk,
    )

    assert client.chat([{"role": "user", "content": "hello"}]) == "final"
    output = capsys.readouterr().out
    assert "visible reasoning" in output
    assert "final" in output


def test_stream_can_be_disabled_for_non_streaming_gateway():
    sdk = FakeOpenAI([response("plain")])
    client = OpenAICompatibleClient(config(stream=False), client=sdk)

    assert client.chat([{"role": "user", "content": "hello"}]) == "plain"
    assert "stream" not in sdk.chat.completions.calls[0]
