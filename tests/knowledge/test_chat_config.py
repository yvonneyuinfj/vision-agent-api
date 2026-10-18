import pytest
import yaml

from app.knowledge.chat_config import ChatAssistantConfig, ChatConfigStore


def valid_config():
    return {
        "name": "build_chat",
        "dataset_ids": ["ds1"],
        "llm_setting": {"temperature": 0.2},
        "prompt_config": {
            "system": "知识库：{knowledge}",
            "parameters": [{"key": "knowledge", "optional": False}],
        },
    }


def test_chat_config_rejects_server_generated_fields():
    with pytest.raises(ValueError):
        ChatAssistantConfig.model_validate({**valid_config(), "id": "server-id"})


def test_chat_config_requires_knowledge_variable_for_datasets():
    raw = valid_config()
    raw["prompt_config"] = {"system": "你好", "parameters": []}
    with pytest.raises(ValueError, match="knowledge"):
        ChatAssistantConfig.model_validate(raw)


def test_chat_config_rejects_embedded_secrets():
    raw = valid_config()
    raw["prompt_config"]["tavily_api_key"] = "do-not-store-me"
    with pytest.raises(ValueError, match="禁止保存密钥"):
        ChatAssistantConfig.model_validate(raw)


def test_chat_config_store_saves_atomically_and_loads(tmp_path):
    path = tmp_path / "chat.yaml"
    store = ChatConfigStore(path)
    config = ChatAssistantConfig.model_validate(valid_config())
    store.save(config)
    assert not path.with_suffix(".yaml.tmp").exists()
    assert store.load() == config
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["name"] == "build_chat"


def test_chat_config_store_rejects_non_mapping_yaml(tmp_path):
    path = tmp_path / "chat.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    store = ChatConfigStore(path)
    with pytest.raises(ValueError, match="YAML 映射"):
        store.load()
