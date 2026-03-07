import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


VALID_PROVIDERS = {"anthropic", "openai", "bedrock", "ollama"}

CONFIG_DIR = Path(__file__).parent


def _load_yaml_config() -> dict:
    config_path = CONFIG_DIR / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


class ModelSettings(BaseSettings):
    provider: str = "anthropic"
    model_id: str = "claude-sonnet-4-5-20250514"
    api_key_env: str = "ANTHROPIC_API_KEY"

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if v not in VALID_PROVIDERS:
            raise ValueError(
                f"Invalid provider '{v}'. Must be one of: {', '.join(sorted(VALID_PROVIDERS))}"
            )
        return v

    def get_api_key(self) -> Optional[str]:
        return os.environ.get(self.api_key_env)


class TransmutationSettings(BaseSettings):
    tau: float = 1.0
    maslow_weights: list[int] = [5, 4, 3, 2, 1]


class ModelCost(BaseSettings):
    input: float = 0.0
    output: float = 0.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    cookie_secret: str = "change-me-to-a-random-string"
    db_path: str = "transmute.db"

    model: ModelSettings = ModelSettings()
    model_costs: dict[str, ModelCost] = {}
    transmutation: TransmutationSettings = TransmutationSettings()

    @model_validator(mode="before")
    @classmethod
    def load_yaml(cls, data: dict) -> dict:
        yaml_config = _load_yaml_config()

        if "model" not in data and "model" in yaml_config:
            data["model"] = yaml_config["model"]

        if "model_costs" not in data and "model_costs" in yaml_config:
            data["model_costs"] = {
                k: ModelCost(**v) if isinstance(v, dict) else v
                for k, v in yaml_config["model_costs"].items()
            }

        if "transmutation" not in data and "transmutation" in yaml_config:
            data["transmutation"] = yaml_config["transmutation"]

        return data

    def get_cost_per_token(self, model_id: str) -> ModelCost:
        if model_id in self.model_costs:
            return self.model_costs[model_id]
        for pattern, cost in self.model_costs.items():
            if pattern.endswith("/*") and model_id.startswith(pattern[:-2]):
                return cost
        return ModelCost()


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
