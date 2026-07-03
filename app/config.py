from functools import lru_cache
from typing import Set

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str = ""
    OWNER_IDS: str = ""
    DATABASE_PATH: str = "data/voxlyra.sqlite3"
    RUN_WEBAPP: bool = True
    PORT: int = 8080
    WEBAPP_URL: str = ""
    CHANNEL_ID: str = ""
    BOT_USERNAME: str = ""
    PROJECT_NAME: str = "Вокслира"
    PUBLIC_VERSION_VISIBLE: bool = False
    PROJECT_VERSION: str = "v1.6.2-settings-repair"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def owner_ids(self) -> Set[int]:
        result: Set[int] = set()
        for item in self.OWNER_IDS.replace(";", ",").split(","):
            item = item.strip()
            if item.isdigit():
                result.add(int(item))
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
