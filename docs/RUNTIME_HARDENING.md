# VoxLyra runtime hardening

Этот документ описывает эксплуатационный слой, добавленный поверх текущего `v1.16.2` без сброса БД и без изменения пользовательских данных.

## Цели

- не объявлять контейнер готовым до завершения БД и FastAPI bootstrap;
- автоматически проваливать liveness после терминальной ошибки старта, чтобы хостинг мог перезапустить контейнер;
- видеть состояние application/database/Telegram/VK без утечки токенов;
- ловить проблемы persistent storage до тяжёлого импорта приложения;
- ограничивать всплески HTTP-конкурентности на небольших контейнерах;
- получать correlation id для медленных и ошибочных запросов;
- тестировать healthcheck тем же кодом, который запускается внутри Docker.

## HTTP probes

`GET /health` и `GET /healthz` — liveness. Во время нормального долгого bootstrap возвращают `200`, но после терминального `stage=failed` возвращают `503`.

`GET /readiness` и `GET /readyz` — strict readiness. До готовности базы и полного приложения возвращают `503`; после успешного bootstrap — `200`.

Probe JSON содержит только безопасные поля: версию, uptime, startup stage, boolean readiness и статусы компонентов. Секреты в startup error проходят общий runtime redactor.

## Correlation ID

Каждый HTTP-ответ получает:

- `X-Request-ID` — безопасный входящий ID либо новый UUID;
- `X-VoxLyra-Version` — текущая внутренняя версия сборки.

Запросы со статусом `5xx` и запросы медленнее `RUNTIME_SLOW_REQUEST_MS` пишутся в лог вместе с request id и path. Нормальный трафик не спамит журнал.

## Component registry

`app/services/runtime_state.py` хранит независимые состояния:

- `application`;
- `database`;
- `telegram`;
- `vk`.

Старые функции `mark_bot_*` и `bot_runtime_snapshot()` сохранены для обратной совместимости.

## Preflight

`scripts/runtime_preflight.py` запускается из `scripts/start.sh` до импорта приложения. Критическими считаются:

- некорректный `PORT`;
- недоступный для записи каталог БД;
- недоступные persistent media/import каталоги;
- свободное место ниже `RUNTIME_MIN_FREE_DISK_MB`.

Отсутствующие настройки необязательных Telegram/VK/GitHub Source Write/YooKassa выводятся как warnings и сами по себе не блокируют web runtime. Значения секретов не печатаются.

## Runtime limits

Настраиваются через env:

```env
RUNTIME_MIN_FREE_DISK_MB=64
RUNTIME_SLOW_REQUEST_MS=2000
RUNTIME_MAX_CONCURRENCY=256
RUNTIME_KEEPALIVE_SECONDS=10
RUNTIME_LISTEN_BACKLOG=512
```

Импорт книг и GitHub Import продолжают использовать собственные, более строгие резервы диска/памяти.

## Docker

Docker image выполняет compileall для `app`, `scripts` и `main.py` во время сборки. Синтаксически повреждённый образ не доходит до redeploy.

Docker `HEALTHCHECK` использует `scripts/healthcheck.py`, а не inline Python. Это даёт один тестируемый источник логики для локальной проверки и хостинга.

## Проверка

`tests/test_runtime_hardening.py` проверяет probe semantics, заголовки, secret redaction, component registry, writable preflight layout и Docker/startup contracts. GitHub Actions запускает этот файл в targeted-блоке, затем запускает весь maintained regression suite.
