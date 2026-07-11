from functools import lru_cache
from typing import Set

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str = ""
    OWNER_IDS: str = ""
    DATABASE_PATH: str = "data/voxlyra.sqlite3"
    RUN_WEBAPP: bool = True
    PORT: int = 3000
    WEBAPP_URL: str = ""
    CHANNEL_ID: str = ""
    BOT_USERNAME: str = ""
    PROJECT_NAME: str = "Вокслира"
    PUBLIC_VERSION_VISIBLE: bool = False
    PROJECT_VERSION: str = "v1.8.3-author-cover-display"
    MAX_BOOK_UPLOAD_MB: int = 0
    MAX_BOOK_UNPACKED_MB: int = 2048
    MAX_COMIC_UPLOAD_MB: int = 512
    MAX_COMIC_UNPACKED_MB: int = 1024
    MAX_COMIC_PAGES: int = 500
    MAX_COMIC_PAGE_MB: int = 30
    COMIC_IMAGE_MAX_WIDTH: int = 1920
    COMIC_IMAGE_MAX_HEIGHT: int = 12000
    COMIC_WEBP_QUALITY: int = 84
    COMIC_WEBTOON_SLICE_HEIGHT: int = 3600
    COMIC_SIGNING_SECRET: str = ""
    COMIC_STORAGE_ROOT: str = "storage/comics"
    COMIC_VARIANT_WIDTHS: str = "720,1280,1920"
    COMIC_DEVICE_CACHE_MAX_MB: int = 512
    COMIC_DEVICE_CACHE_MAX_ITEMS: int = 1200
    COMIC_PRELOAD_PAGES_FAST: int = 6
    COMIC_PRELOAD_PAGES_SLOW: int = 1
    TTS_ENABLED: bool = True
    TTS_CACHE_DIR: str = "storage/tts"
    TTS_MODEL_DIR: str = "/opt/voxlyra-voices"
    TTS_CACHE_DAYS: int = 3
    TTS_MAX_CACHE_MB: int = 512
    TTS_MAX_VARIANTS_PER_CHAPTER: int = 6
    TTS_SIGNING_SECRET: str = ""

    # Юридические реквизиты. До заполнения платный режим в рублях остаётся выключенным.
    LEGAL_OPERATOR_NAME: str = ""
    LEGAL_OPERATOR_STATUS: str = ""
    LEGAL_OPERATOR_INN: str = ""
    LEGAL_OPERATOR_OGRN: str = ""
    LEGAL_OPERATOR_ADDRESS: str = ""
    LEGAL_CONTACT_EMAIL: str = ""
    LEGAL_SUPPORT_CONTACT: str = ""
    LEGAL_DOCS_BASE_URL: str = ""
    LEGAL_REQUIRE_ON_START: bool = True
    LEGAL_BLOCK_RUB_PAYMENTS_IF_INCOMPLETE: bool = True

    # Шифрование платёжных реквизитов. Рекомендуется отдельный Fernet-ключ.
    DATA_ENCRYPTION_KEY: str = ""

    # ЮKassa: приём рублей предназначен для отдельной веб-версии, а не для
    # цифровых покупок внутри Telegram. Выплаты авторам выполняются через СБП.
    YOOKASSA_EXTERNAL_CHECKOUT_ENABLED: bool = False
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_RETURN_URL: str = ""
    YOOKASSA_PAYOUTS_ENABLED: bool = False
    YOOKASSA_PAYOUT_GATEWAY_ID: str = ""
    YOOKASSA_PAYOUT_SECRET_KEY: str = ""
    YOOKASSA_TEST_MODE: bool = True
    YOOKASSA_WEBHOOK_TOKEN: str = ""

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
