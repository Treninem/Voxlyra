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
    PROJECT_VERSION: str = "v1.11.8-owner-only"
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
    TTS_PROVIDER_ORDER: str = "vosk,moss,qwen,piper"
    TTS_PROVIDER_ORDER_HQ: str = "moss,qwen,vosk,piper"
    TTS_QWEN_URL: str = ""
    TTS_MOSS_URL: str = ""
    TTS_VOSK_ENABLED: bool = True
    TTS_VOSK_MODEL_NAME: str = "vosk-model-tts-ru-0.9-multi"
    TTS_VOSK_MODEL_DIR: str = "storage/tts/models/vosk"
    TTS_VOSK_FEMALE_SPEAKER: int = 2
    TTS_VOSK_MALE_SPEAKER: int = 4
    TTS_VOSK_AUTO_SELECT: bool = True
    TTS_VOSK_FEMALE_CANDIDATES: str = "0,1,2"
    TTS_VOSK_MALE_CANDIDATES: str = "3,4"
    TTS_VOSK_PROFILE_PATH: str = "storage/tts/vosk_voice_profile.json"
    TTS_VOSK_BENCHMARK_TIMEOUT_SECONDS: int = 180
    TTS_REMOTE_TOKEN: str = ""
    TTS_REMOTE_TIMEOUT_SECONDS: int = 120
    TTS_REMOTE_FIRST_TIMEOUT_SECONDS: int = 10
    TTS_REMOTE_COOLDOWN_SECONDS: int = 60
    TTS_WORKERS: int = 2
    TTS_SESSION_TTL_SECONDS: int = 7200
    TTS_SESSION_INITIAL_SEGMENTS: int = 8
    TTS_SESSION_WINDOW_SEGMENTS: int = 10
    TTS_FIRST_SEGMENT_WAIT_SECONDS: int = 2
    TTS_SEGMENT_TARGET_CHARS: int = 280
    TTS_SEGMENT_MAX_CHARS: int = 480
    TTS_FIRST_SEGMENT_MAX_CHARS: int = 150
    TTS_QUALITY_RETRIES: int = 1
    TTS_SEGMENT_SESSION_RETRIES: int = 2

    # Юридические реквизиты. До заполнения платный режим в рублях остаётся выключенным.
    LEGAL_OPERATOR_NAME: str = "Тренин Евгений Максимович"
    LEGAL_OPERATOR_STATUS: str = "Самозанятый (НПД), физическое лицо, не ИП"
    LEGAL_OPERATOR_INN: str = "332201556141"
    LEGAL_OPERATOR_OGRN: str = "не присваивался"
    LEGAL_OPERATOR_ADDRESS: str = "602337, Владимирская область, Селивановский район, п. Новлянка"
    LEGAL_CONTACT_EMAIL: str = "info@voxlyra.ru"
    LEGAL_SUPPORT_CONTACT: str = "@Treninem"
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
