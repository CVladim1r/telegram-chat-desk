# Диалоги

Веб-чат для Telegram-бота на React, FastAPI и aiogram

## Запуск

Установите Python 3.12+, Node.js 22.19+ и [uv](https://docs.astral.sh/uv/getting-started/installation/). Из папки проекта выполните:

```powershell
.\scripts\start.ps1
```

На Linux и macOS: `sh scripts/start.sh`

Откройте [localhost:8000](http://localhost:8000) и вставьте токен из [BotFather](https://t.me/BotFather). Собеседник должен первым написать боту или нажать Начать, после чего можно ответить ему на сайте

Для настройки порта и базы скопируйте `.env.example` в `.env`. История хранится в `data/chat.db`. Токен живёт в памяти сервера и после перезапуска введите его снова

Повторный запуск без сборки: `uv run --no-sync python -m app`


С Docker: `docker compose up --build`

## Проверка

Backend: `uv run pytest`
Frontend: в `frontend` выполните `npm test` и `npm run build`
