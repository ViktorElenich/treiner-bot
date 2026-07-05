# CLAUDE.md

Заметки для будущих сессий Claude Code, работающих с `treiner-bot`.

## Что это

Telegram-бот `@viktortreiner_bot` для фитнес-тренера Виктора — подписки, платежи ЮKassa, генерация контента (Gemini + Kie AI), модерация групп, админ-панель.

- **Python 3.11**, aiogram 3.15, aiohttp, aiosqlite, APScheduler, yookassa
- **Деплой:** Render (webhook-режим)
- **Локальная разработка:** polling-режим (если `WEBHOOK_BASE_URL` не задан)
- **Точка входа:** `python -m bot.main`
- **БД:** SQLite, файл `subscriptions.db`

## Render free tier — важно

Бот живёт на Render free tier и засыпает после ~15 мин без HTTP-трафика. Несколько вещей, которые уже настроены и **ломать не надо**:

### 1. UptimeRobot держит контейнер тёплым

Внешний монитор пингает `https://treiner-bot.onrender.com/health` каждые 5 минут. Это меньше 15-минутного порога Render, поэтому контейнер не успевает уснуть. Endpoint реализован в `bot/main.py` (`health_check`).

Если контейнер всё-таки уходит в sleep (например, UptimeRobot моргнул или был редеплой) — смотри ниже.

### 2. Webhook не удаляется при shutdown

`on_shutdown` в `bot/main.py` **намеренно не вызывает `bot.delete_webhook()`**. Раньше вызывал — это приводило к гонке при редеплое: старый контейнер умирал после того как новый уже поставил webhook, и стирал его. Результат — Telegram не знал, куда доставлять апдейты, pending очередь росла, пользователи не получали ответа.

**Не добавляй `delete_webhook()` обратно.**

### 3. Pending updates не дропаются на startup

`on_startup` в `bot/main.py` вызывает `bot.set_webhook()` **без** `drop_pending_updates=True`. Это критично: когда контейнер просыпается из-за входящего апдейта от Telegram, `on_startup` запускается и пересоздаёт webhook. Если бы там был `drop_pending_updates=True`, он выбросил бы то самое сообщение, которое разбудило контейнер → бот «не отвечает на первый /start».

В polling-ветке (`bot/main.py:146`) флаг оставлен — там он нужен, чтобы при локальном запуске не получить залежавшиеся продовые апдейты.

### Диагностика «бот молчит»

Если бот перестал отвечать, первое, что проверить:

```bash
curl https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo
```

- `url: ""` → webhook не установлен, восстановить:
  ```bash
  curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
    -d "url=https://treiner-bot.onrender.com/webhook" \
    -d "secret_token=<WEBHOOK_SECRET>"
  ```
- `pending_update_count > 0` + `url` стоит → бот получает апдейты, но не может их обработать. Смотри логи Render.
- Время ответа `/health` > 1 сек → контейнер был холодный, проверь UptimeRobot (ещё жив? пингует?).

## Модерация групп

Файл: `bot/handlers/moderation.py`. Слушает 4 группы из конфига (общая/старт/прогресс/результат). Две независимые подсистемы:

### Фильтр мата

Сканирует текстовые сообщения (`contains_profanity` из `bot/data/bad_words.py`). Лестница наказаний: **warning → mute 1ч → ban**. Счётчик в таблице `warnings`. Команды админа: `/warnings`, `/reset_warnings` (оба — в reply на сообщение пользователя).

Админ и бот освобождены от проверки.

### Защита тем форума (channel-like поведение)

В форум-супергруппе можно пометить конкретные темы как «защищённые»: в них только админ (`admin_chat_id`) пишет новые посты, участники — **только комментируют** (reply на его посты). Новые топ-левел сообщения от не-админов удаляются + показывается временное уведомление (10 сек).

**Ключевая логика** (`_should_delete_topic_post` в `moderation.py`):
- Сообщение в supergroup с `message_thread_id` (форум-тема)
- Группа в списке отслеживаемых
- Отправитель ≠ админ и ≠ бот
- Это **не** ответ на другое сообщение, либо ответ идёт на `forum_topic_created` (т.е. на корень темы — значит новый топ-левел пост)
- Тема присутствует в таблице `protected_topics`

Защищённые темы хранятся в таблице `protected_topics(chat_id, thread_id, title, created_at)`.

**Команды админа:**
- `/protect_topic` — внутри темы, добавить её в защищённые
- `/unprotect_topic` — внутри темы, снять защиту
- `/topic_id` — показать `chat_id` и `thread_id` текущей темы (для отладки)

**Требование:** бот должен быть админом группы с правом **Delete Messages**, иначе `message.delete()` упадёт.

## Автопубликация постов

Файл: `bot/handlers/autopost.py`. Ежедневно в **8:30 МСК** планировщик генерирует пост (чётный день года — питание, нечётный — тренировки), картинку через Kie AI и присылает тренеру с кнопками «Опубликовать / Переделать / Пропустить». По кнопке пост уходит в общий чат (`GROUP_GENERAL_ID`): питание → топик `TOPIC_NUTRITION_ID`, тренировки → `TOPIC_ARTICLES_ID`. Ручной запуск: `/autopost` (опционально `/autopost nutrition` или `/autopost article`).

Кнопка «🎤 Надиктую сам»: бот ждёт голосовое или текст от тренера (состояние `_dictation`), Gemini расшифровывает аудио напрямую и оформляет надиктовку в пост (`structure_dictation` в `content_gen.py`) — без выдумывания фактов, с сохранением манеры. Дальше то же превью с кнопками; «Переделать» у такого поста заново оформляет ту же надиктовку (исходник хранится в `draft["source"]`), а не генерирует с нуля.

Черновики и состояние надиктовки хранятся в памяти (`_drafts`, `_dictation`) — после редеплоя кнопки старых черновиков не работают, нужен `/autopost` заново.

Промпты в `bot/services/content_gen.py` содержат **образцы живой речи Виктора** (`STYLE_SAMPLES` — расшифровки его голосовых) и антиИИ-правила (`COMMON_RULES`). Не выкидывай образцы при правках промпта — они дают основной эффект «человеческого» текста.

## Структура кода

```
bot/
  main.py              — точка входа, webhook/polling, health endpoint
  config.py            — env vars, admin_chat_id, group IDs, токены
  database.py          — все функции БД (subscriptions, leads, waitlist,
                         warnings, content_history, protected_topics)
  handlers/
    start.py           — /start, главное меню
    payments.py        — ЮKassa: инвойсы, подтверждения
    admin.py           — /stats, /users, /extend (только тренер)
    content.py         — генерация контента (Gemini + Kie AI)
    autopost.py        — ежедневный автопост с одобрением тренера
    moderation.py      — мат-фильтр + защита тем
  services/
    scheduler.py       — APScheduler: проверка истекающих подписок,
                         вейтлист, кик из групп
  web/
    webhooks.py        — приём заявок с сайта (/api/lead, /api/consultation,
                         /api/waitlist), ЮKassa webhook
```

Порядок роутеров в `create_bot_and_dispatcher` (`bot/main.py:79-87`) важен: `moderation_router` первый — ловит сообщения в группах раньше других хендлеров.

## Env vars (Render)

Критичные: `BOT_TOKEN`, `ADMIN_CHAT_ID`, `WEBHOOK_BASE_URL` (без trailing slash), `WEBHOOK_SECRET`, `YUKASSA_SHOP_ID`, `YUKASSA_SECRET_KEY`, `GROUP_GENERAL_ID`, `GROUP_START_ID`, `GROUP_PROGRESS_ID`, `GROUP_RESULT_ID`, `GEMINI_API_KEY`, `KIE_API_KEY`.

## Платежи

Используется **боевой** магазин ЮKassa (переключено с тестового в июле 2026). Аккуратно с изменениями в платёжном коде — деньги настоящие.

## Тон общения

Виктор общается по-русски, не технарь. Отвечай коротко, избегай жаргона, при необходимости объясняй «что это значит на практике» и «что тебе надо сделать руками». Коммит-сообщения — по-английски в стиле уже существующих (см. `git log`).
