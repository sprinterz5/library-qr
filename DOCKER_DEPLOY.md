# Docker Deployment Guide

## Упаковка образа

### Вариант 1: Экспорт в файл (для офлайн-переноса)

```bash
# 1. Собрать образ
docker-compose build

# 2. Сохранить образ в файл
docker save elibra-middleware:latest | gzip > elibra-middleware.tar.gz
```

**Размер файла:** ~1.5-2 GB (включая Chromium)

### Вариант 2: Загрузка в Docker Registry

```bash
# 1. Собрать образ
docker-compose build

# 2. Залогиниться в Docker Hub (или другой registry)
docker login

# 3. Закоммитить образ
docker tag elibra-middleware:latest yourusername/elibra-middleware:latest

# 4. Загрузить в Hub
docker push yourusername/elibra-middleware:latest
```

## Что передать на целевую машину

### Минимальный набор файлов:

```
elibra-middleware/
├── docker-compose.yml       # Обязательно
├── Dockerfile               # Если используешь build (не нужен, если образ в Registry)
├── requirements.txt         # Если используешь build
├── .env.example            # Шаблон для создания .env
└── elibra-middleware.tar.gz # Если используешь вариант 1 (экспорт в файл)
```

**Или если образ в Registry:**
- Только `docker-compose.yml` (нужно изменить `build:` на `image:`)

## Установка на целевой машине

### Шаг 1: Подготовка файлов

Скопируй файлы на целевую машину в папку (например, `~/elibra-middleware/`)

### Шаг 2: Загрузка образа (если использовал вариант 1)

```bash
# Загрузить образ из файла
gunzip -c elibra-middleware.tar.gz | docker load
```

### Шаг 3: Создание .env файла

```bash
# Скопировать шаблон
cp .env.example .env

# Отредактировать .env (обязательно заполнить все переменные!)
nano .env
# или
vi .env
```

**КРИТИЧЕСКИ ВАЖНО:** Заполни все переменные в `.env`, особенно:
- `APP_ACTIVATION_KEY`
- `APP_ACTIVATION_PASSWORD`
- `ELIBRA_USER_EMAIL`
- `ELIBRA_PASSWORD`

Без них приложение не запустится!

### Шаг 4: Запуск

```bash
# Запустить контейнер
docker-compose up -d

# Проверить логи
docker-compose logs -f

# Проверить статус
docker-compose ps
```

### Шаг 5: Проверка

Открой браузер: `http://localhost:8000/scan`

## Управление

```bash
# Остановить
docker-compose down

# Перезапустить
docker-compose restart

# Обновить (после изменений в коде)
docker-compose build
docker-compose up -d

# Просмотр логов
docker-compose logs -f elibra-middleware

# Войти в контейнер
docker exec -it elibra-middleware bash
```

## Важные замечания

1. **.env файл НЕ попадает в образ** - его нужно создавать вручную на каждой машине
2. **База данных** сохраняется в `./data/gateway.db` (volume)
3. **Playwright сессия** сохраняется в `./pw_profile/` (volume)
4. **Порт 8000** должен быть свободен
5. **Минимум 2GB RAM** рекомендуется для работы Chromium

## Troubleshooting

### Контейнер не запускается
- Проверь, что `.env` файл создан и заполнен
- Проверь логи: `docker-compose logs`

### Ошибка активации
- Убедись, что `APP_ACTIVATION_KEY` и `APP_ACTIVATION_PASSWORD` правильные

### Chromium не работает
- Проверь, что `shm_size: '2gb'` установлен в docker-compose.yml
- Проверь логи на ошибки Playwright


