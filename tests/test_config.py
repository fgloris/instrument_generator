from pathlib import Path

import yaml

from lab_asset_agent.config import load_config
from lab_asset_agent.models import ModelsConfig


def test_example_config_loads():
    project = Path(__file__).resolve().parents[1]
    config = load_config(project / "config.example.yaml")
    assert config.models.initial_generator == "deepseek"
    assert config.models.initial_model.model == "deepseek-reasoner"
    assert config.models.iteration_agent.model == "gpt-4o"
    assert config.paths.rules == (project / "AGENT_RULES.md").resolve()


def test_legacy_v02_model_keys_are_migrated():
    models = ModelsConfig.model_validate(
        {
            "code_writer": {
                "base_url": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "model": "deepseek-chat",
            },
            "visual_reviewer": {
                "base_url": "https://api.vectorengine.ai/v1",
                "api_key_env": "VECTOR_ENGINE_API_KEY",
                "model": "gpt-4o",
            },
        }
    )
    assert models.initial_model.model == "deepseek-chat"
    assert models.iteration_agent.model == "gpt-4o"


def test_gpt_can_be_selected_for_initial_generation():
    models = ModelsConfig.model_validate(
        {
            "initial_generator": "gpt",
            "iteration_agent": {
                "base_url": "https://api.vectorengine.ai/v1",
                "api_key_env": "VECTOR_ENGINE_API_KEY",
                "model": "gpt-4o",
            },
        }
    )
    assert models.initial_model is models.iteration_agent
