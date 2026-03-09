from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class DistroPluginSettings(BaseSettings):
    distro_home: Path = Path.home() / ".amplifier-distro"
    amplifier_home: Path = Path.home() / ".amplifier"

    model_config = SettingsConfigDict(env_prefix="DISTRO_PLUGIN_")
