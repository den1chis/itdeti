# FunCode AI System — Backend

## Структура проекта
```
app/
├── main.py                  # Точка входа
├── requirements.txt
├── Dockerfile
├── .env.example             # Скопируйте в .env и заполните
│
├── core/
│   ├── config.py            # Настройки из .env
│   ├── database.py          # Async SQLAlchemy + сессия
│   ├── security.py          # JWT, bcrypt
│   └── dependencies.py      # Depends: get_current_user, admin_only
│
├── models/                  # SQLAlchemy ORM модели
│   ├── user.py
│   └── refresh_token.py
│
├── schemas/                 # Pydantic схемы (request/response)
│   └── auth.py
│
├── routers/                 # FastAPI роутеры
│   └── auth.py
│
└── services/                # Бизнес-логика (следующие модули)
```

## Запуск локально
```bash
cp .env.example .env
# Заполните .env

pip install -r requirements.txt
python main.py
```

## API Docs
После запуска: http://localhost:8000/docs

## Первый запуск — создание админа
```
POST /auth/setup-admin?email=denis@funcode.kz&password=YOUR_PASS&full_name=Денис Александрович
```
После создания удалите этот эндпоинт из routers/auth.py

## Endpoints
| Method | URL | Auth | Описание |
|--------|-----|------|----------|
| POST | /auth/login | — | Логин, получить токены |
| POST | /auth/refresh | — | Обновить access token |
| POST | /auth/logout | — | Инвалидировать refresh token |
| GET | /auth/me | ✅ | Текущий пользователь |
| GET | /health | — | Статус сервера |
