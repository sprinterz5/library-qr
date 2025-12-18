# eLibra Middleware (Coventry Library)

Приватный middleware‑сервис между мобильным/веб‑интерфейсом и системой eLibra.
Вместо прямых HTTP‑запросов в eLibra используется Playwright RPA, который управляет реальным браузером и UI eLibra.

## Основные возможности

- Выдача книг по QR/barcode через eLibra UI (RPA, без Bearer/JSESSIONID).
- Создание заявок на возврат, модерация возвратов через простую админ‑панель.
- Логирование выданных книг и заявок в SQLite (`gateway.db`).
- Авто‑логин в eLibra по сохранённым учетным данным.
- Мобильный UI для сканирования на телефоне.
- Статистика: сколько книг выдано и возвращено через систему.
- Очередь/блокировка RPA‑операций через `asyncio.Lock`.

## Технологии

- Python 3.11+
- FastAPI + Uvicorn
- Playwright (Chromium, persistent context)
- SQLite
- dotenv (.env)

## Установка и запуск (Windows, локально)

1. Клонировать репозиторий (приватный).
2. Создать и активировать виртуальное окружение:

```bash
python -m venv venv
venv\Scripts\activate
```

3. Установить зависимости:

```bash
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

4. Создать `.env` в корне проекта:

```env
ELIBRA_BASE_URL=https://coventry.elibra.kz
ELIBRA_LIBRARY_ID=3
ELIBRA_CLIENTID=coventry

ELIBRA_USER_EMAIL=you@example.com
ELIBRA_PASSWORD=your_elibra_password

ADMIN_PIN=9876
DB_PATH=gateway.db
CARDCODE_PREFIX=21000000

# Активация (чтобы приложение вообще запустилось)
APP_ACTIVATION_KEY=AB2025-ELIBRA-MIDDLEWARE-AIDAR-BEGOTAYEV
APP_ACTIVATION_PASSWORD=AB2025-PROJECT



5. Запуск под Windows (через специальный лаунчер с правильным event loop):

```bash
python run_windows.py --http
```

Опционально можно сгенерировать self‑signed сертификат (`server.key`, `server.crt`) через
`generate_self_signed_cert.py` и запускать с `--https`.

## Маршруты

- `GET /scan` — основной интерфейс «Library Desk» для сканирования читателя (последние 5 цифр cardcode) и выдачи/возврата.
- `POST /submit` — обработчик формы issue/return.
  - `action=issue` — выдача книги через RPA.
  - `action=return` — создание заявки на возврат (без немедленного возврата в eLibra).
- `GET /admin/returns?pin=...` — админ‑панель для просмотра и модерации заявок на возврат.
- `POST /admin/returns/{id}/approve` — одобрить возврат (RPA‑возврат + смена статуса).
- `POST /admin/returns/{id}/reject` — отклонить возврат.
- `GET /admin/stats?pin=...` — статистика (выдачи/возвраты).
- `GET /admin/search?pin=...` — поиск читателей по имени/email/cardcode (через RPA).
- `GET /rpa/health` — диагностика статуса RPA.
- `POST /rpa/manual-login` — запуск ручного логина в eLibra в отдельном окне браузера.

## SQLite

- `gateway.db` содержит две основные таблицы:
  - `return_requests` — заявки на возврат.
  - `issued_books` — лог выданных книг (через этот middleware).
- При старте `init_db()` автоматически создаёт/мигрирует необходимые таблицы.

## Активация и защита кода

Приложение не запустится, если не заданы корректные значения:

- `APP_ACTIVATION_KEY`
- `APP_ACTIVATION_PASSWORD`

В `app/main.py` при импорте выполняется проверка:

- Если ключ/пароль не совпадают с ожидаемыми значениями — выбрасывается `RuntimeError`
  и приложение не стартует.
- Логика RPA также завязана на этих настройках (код из этого репозитория по умолчанию
  предназначен только для владельца/автора).

Эти значения хранятся только в `.env`, который добавлен в `.gitignore` и не попадает в репозиторий.

## Discord‑уведомления

Если задан `DISCORD_WEBHOOK_URL`, приложение отправляет JSON‑события в Discord:

- При старте (`event = "startup"`).
- При выключении (`event = "shutdown"`).
- Каждые `APP_HEARTBEAT_SECONDS` секунд (`event = "heartbeat"`, по умолчанию 30 минут).
- При любом `POST /submit` (`event = "submit"` + `action`, `barcode`, `reader_id`).
- При админском approve заявки (`event = "admin_approve"`, `req_id`).
- При админском reject заявки (`event = "admin_reject"`, `req_id`).

Структура сообщения:

```json
{
  "event": "submit",
  "timestamp": "2025-01-01T12:00:00.000000",
  "host": "HOSTNAME",
  "path": "http://...",
  "ip": "1.2.3.4",
  "user_agent": "...",
  "extra": {
    "action": "issue",
    "barcode": "2100000005088",
    "reader_id": "1234"
  }
}
```

## Docker (набросок)

Минимальный `Dockerfile` может выглядеть так:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

COPY . .

ENV DB_PATH=/app/data/gateway.db
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

В `docker-compose.yml` можно пробросить `.env`, `gateway.db` и `pw_profile/` как volume,
чтобы сохранялись сессии и данные.

## Лицензия / использование

Репозиторий приватный и предназначен только для использования владельцем/автором.
Код содержит механизмы активации и телеметрии, и не должен запускаться/эксплуатироваться
третьими лицами без явного согласия автора.


