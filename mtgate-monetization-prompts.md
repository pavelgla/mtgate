# MTGate Monetization — Промпты для Claude CLI

## Общая информация

- **Сервер:** 72.56.112.182 (root доступ)
- **Проект на сервере:** /opt/mtgate
- **Платежи:** ЮKassa (основной, КФХ/ИП) + Robokassa (резервный)
- **Бот:** grammY (Node.js)
- **БД:** SQLite (better-sqlite3)
- **Лендинг:** статический HTML, Nginx
- **Тарифы:** 100₽/мес, 200₽/3 мес, trial 3 дня

---

## ВНИМАНИЕ: Живой сервис!

Прокси работает в продакшене, есть активные пользователи с бессрочными токенами.
Правила на все этапы:
- **НЕ перезапускать** контейнер mtgate-proxy без крайней необходимости
- **НЕ менять** формат users.json и логику UserStore
- **НЕ трогать** legacy-пользователей (те, кого нет в SQLite бота)
- Новый код (бот, scheduler) управляет ТОЛЬКО своими юзерами через SQLite
- При `docker compose up` указывать конкретный сервис, а не пересобирать всё

## Порядок запуска

Запускать последовательно на сервере. Каждый промпт — отдельная сессия `claude`.

---

## ЭТАП 0: Разведка

```bash
claude "
Исследуй текущее состояние сервера и проекта MTGate.

1. Проект лежит в /opt/mtgate. Покажи его структуру.
2. Проверь запущенные Docker-контейнеры: docker ps
3. Проверь docker-compose.yml проекта — какие сервисы, порты, volumes.
4. Проверь содержимое .env файла (если есть).
5. Проверь установлен ли Nginx: nginx -v. Если да — покажи конфиг из /etc/nginx/sites-enabled/
6. Проверь установлен ли Node.js: node -v, npm -v. Если нет — установи Node.js 20 LTS.
7. Проверь установлен ли SQLite: sqlite3 --version
8. Проверь какие порты слушаются: ss -tlnp
9. Проверь firewall: ufw status или iptables -L -n

Выведи отчёт со всей собранной информацией. Не меняй ничего кроме установки Node.js, только собери данные.
"
```

---

## ЭТАП 1: REST API в Flask-админке

```bash
claude "
Проект MTGate лежит в /opt/mtgate. Это MTProto proxy с Flask-админкой.

ЗАДАЧА: Добавить REST API в существующий Flask app (admin/app.py) для управления пользователями программно.

КОНТЕКСТ:
- Текущая админка: admin/app.py (Flask), данные в /cache/users.json
- Модель данных: admin/users.py (UserStore класс, JSON-файл с lock)
- Конфиг прокси: admin/proxy_config.py (генерирует config, шлёт SIGHUP)

ТРЕБОВАНИЯ:

1. Добавить API endpoints в admin/app.py с авторизацией по Bearer токену (API_TOKEN из .env):

   POST /api/users
   Body: { name, ttl_days?, ttl_hours? }
   -> Создаёт юзера, возвращает { name, secret, tg_link, expires_at }

   GET /api/users/<name>
   -> Возвращает { name, enabled, active_ip, bound_ip, last_seen, created_at, expires_at }

   GET /api/users
   -> Список всех юзеров

   PATCH /api/users/<name>/extend
   Body: { days, hours? }
   -> Продлевает expires_at на указанный срок от текущего момента (или от текущего expires_at если ещё не истёк)

   PATCH /api/users/<name>/toggle
   -> Включает/выключает юзера (enabled true/false), возвращает новый статус

   DELETE /api/users/<name>
   -> Удаляет юзера

2. Декоратор require_api_token для проверки Authorization: Bearer <API_TOKEN>
3. Добавить API_TOKEN в .env.example
4. tg_link формат: tg://proxy?server={SERVER_HOST}&port={SERVER_PORT}&secret=ee{secret}
5. Все endpoints возвращают JSON с правильными HTTP кодами (201, 200, 404, 401, 400)
6. НЕ ломать существующую веб-админку — API это дополнение к ней

КРИТИЧНО — СУЩЕСТВУЮЩИЕ ПОЛЬЗОВАТЕЛИ:
- В системе УЖЕ есть активные пользователи с бессрочными токенами (expires_at = null).
- Они прямо сейчас используют прокси. Нельзя нарушить их работу.
- НЕ меняй формат users.json и существующую логику UserStore.
- НЕ трогай фоновый воркер purge_expired_users — он работает правильно.
- API — это чисто ДОПОЛНЕНИЕ, не рефакторинг.

ВАЖНО:
- Прочитай существующий код ПЕРЕД изменениями
- Используй существующий UserStore из users.py
- Не дублируй логику — переиспользуй существующие методы
- После изменений пересобери ТОЛЬКО Docker-контейнер admin: docker compose build mtgate-admin && docker compose up -d mtgate-admin
- Убедись что контейнер proxy НЕ перезапускался (docker ps — проверь uptime proxy)
- Проверь API через curl
- Проверь что веб-админка по-прежнему показывает всех существующих юзеров
"
```

---

## ЭТАП 2: SQLite и модуль подписок

```bash
claude "
Проект MTGate лежит в /opt/mtgate.

ЗАДАЧА: Создать модуль подписок на SQLite для Telegram-бота.

Создай директорию bot/ внутри проекта со следующей структурой:
bot/
  src/
    database.js
  data/          (пустая, для SQLite файла)
  package.json
  .env.example

ФАЙЛ bot/src/database.js:

Схема БД (bot/data/subscriptions.db):

Таблица users:
- id INTEGER PRIMARY KEY AUTOINCREMENT
- telegram_id INTEGER UNIQUE NOT NULL
- telegram_username TEXT
- mtgate_username TEXT UNIQUE
- mtgate_secret TEXT
- created_at DATETIME DEFAULT CURRENT_TIMESTAMP

Таблица subscriptions:
- id INTEGER PRIMARY KEY AUTOINCREMENT
- user_id INTEGER REFERENCES users(id)
- plan TEXT NOT NULL CHECK(plan IN ('trial','1month','3month'))
- status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','expired','cancelled'))
- started_at DATETIME NOT NULL
- expires_at DATETIME NOT NULL
- notified_expiring INTEGER DEFAULT 0
- created_at DATETIME DEFAULT CURRENT_TIMESTAMP

Таблица payments:
- id INTEGER PRIMARY KEY AUTOINCREMENT
- user_id INTEGER REFERENCES users(id)
- robokassa_inv_id INTEGER UNIQUE
- amount REAL NOT NULL
- plan TEXT NOT NULL
- status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','failed'))
- paid_at DATETIME
- created_at DATETIME DEFAULT CURRENT_TIMESTAMP

Экспортируемые функции:
- initDatabase() — CREATE TABLE IF NOT EXISTS
- createUser(telegramId, username) -> user
- getUserByTelegramId(telegramId) -> user | null
- getUserById(id) -> user | null
- updateUserMtgate(userId, mtgateUsername, mtgateSecret)
- createSubscription(userId, plan, days) -> subscription
- getActiveSubscription(userId) -> subscription | null
- expireSubscription(subscriptionId)
- createPayment(userId, plan, amount) -> payment (id = robokassa_inv_id)
- completePayment(invId) -> payment
- getPaymentByInvId(invId) -> payment | null
- getExpiringSoon(days) -> [{subscription + user}]
- getExpired() -> [{subscription + user}]
- markNotified(subscriptionId)
- hasUsedTrial(userId) -> boolean

Используй better-sqlite3. Все операции синхронные.

package.json зависимости:
- grammy
- better-sqlite3
- dotenv
- express

Запусти npm install в bot/.
"
```

---

## ЭТАП 3: Telegram-бот

```bash
claude "
Проект MTGate в /opt/mtgate. В bot/src/ уже есть database.js, в bot/ — package.json с установленными зависимостями.

ЗАДАЧА: Создать Telegram-бота на grammY.

КОНТЕКСТ:
- database.js экспортирует функции: initDatabase, createUser, getUserByTelegramId, updateUserMtgate, createSubscription, getActiveSubscription, createPayment, hasUsedTrial
- MTGate API: http://localhost:8889/api/ с Bearer токеном из MTGATE_API_TOKEN
- Тарифы: trial 3 дня бесплатно (одноразово), 1 мес = 100 руб, 3 мес = 200 руб

СОЗДАЙ bot/src/mtgate-client.js — HTTP-клиент к MTGate API:
- createProxyUser(name, ttlDays) -> { name, secret, tg_link, expires_at }
- getProxyUser(name) -> user data
- extendProxyUser(name, days) -> updated user
- toggleProxyUser(name) -> { enabled }
- deleteProxyUser(name)
- Использовать встроенный fetch (Node 20). Базовый URL и токен из env.

СОЗДАЙ bot/src/bot.js:

Команда /start:
- Текст: 'MTGate — быстрый и стабильный прокси для Telegram.\n\nВыберите действие:'
- Inline keyboard: [[Попробовать бесплатно (3 дня)], [Тарифы], [Мой аккаунт]]

Callback 'trial':
- getUserByTelegramId. Если нет — createUser.
- hasUsedTrial. Если да — 'Пробный период уже использован' + кнопки тарифов.
- Если нет — createProxyUser('tg_{id}', 3), updateUserMtgate, createSubscription(userId, 'trial', 3)
- Отправить: 'Прокси активирован на 3 дня!\n\nВаша ссылка для подключения:\n{tg_link}\n\nНажмите — прокси подключится автоматически.'

Callback 'plans':
- Текст с тарифами: 1 мес 100р, 3 мес 200р (экономия 33%)
- Кнопки: [[Купить 1 мес — 100р], [Купить 3 мес — 200р]]

Callback 'buy_1month' и 'buy_3month':
- getUserByTelegramId, если нет — createUser
- createPayment(userId, plan, amount)
- generatePaymentUrl (пока заглушка — вернуть 'Оплата временно недоступна')
- Отправить ссылку на оплату с inline URL-кнопкой

Callback 'account':
- Если нет юзера: 'Вы не зарегистрированы. Начните с пробного периода!'
- Если есть — показать статус, тариф, дату окончания, ссылку
- Кнопки: [Продлить] [Тарифы]

Глобальный error handler — логировать ошибку, юзеру 'Произошла ошибка, попробуйте позже'.

СОЗДАЙ bot/src/index.js:
- require('dotenv').config()
- initDatabase()
- Создать бота, подключить обработчики
- bot.start() (long polling)
- Логировать 'Bot started'

СОЗДАЙ bot/.env.example:
BOT_TOKEN=
MTGATE_API_URL=http://localhost:8889
MTGATE_API_TOKEN=
ROBOKASSA_LOGIN=
ROBOKASSA_PASS1=
ROBOKASSA_PASS2=
ROBOKASSA_TEST_MODE=true
SERVER_HOST=72.56.112.182
"
```

---

## ЭТАП 4: Robokassa

```bash
claude "
Проект MTGate в /opt/mtgate. В bot/src/ есть bot.js, database.js, mtgate-client.js.

ЗАДАЧА: Интегрировать оплату через Robokassa.

СОЗДАЙ bot/src/robokassa.js:

1. generatePaymentUrl({ invId, amount, description, telegramId }):
   - Конфиг из env: ROBOKASSA_LOGIN, ROBOKASSA_PASS1, ROBOKASSA_TEST_MODE
   - Подпись: MD5 от строки 'MerchantLogin:OutSum:InvId:Pass1:Shp_telegram_id=VALUE'
     (Shp_ параметры включаются в подпись в алфавитном порядке)
   - URL: https://auth.robokassa.ru/Merchant/Index.aspx
   - Query params: MerchantLogin, OutSum, InvId, Description, SignatureValue, IsTest (если тест), Shp_telegram_id
   - Вернуть полный URL

2. verifyResultSignature({ outSum, invId, signatureValue, shpTelegramId }):
   - Подпись: MD5('OutSum:InvId:Pass2:Shp_telegram_id=VALUE')
   - Сравнить с signatureValue (case-insensitive)
   - Вернуть boolean

3. createResultHandler(bot, db, mtgateClient):
   Возвращает Express middleware (req, res) для POST /robokassa/result:
   - Извлечь OutSum, InvId, SignatureValue, Shp_telegram_id из req.body
   - Проверить подпись. Если невалидна — 400 'Invalid signature'
   - completePayment(InvId)
   - Определить plan: OutSum==100 -> '1month' (30 дней), OutSum==200 -> '3month' (90 дней)
   - Найти юзера по Shp_telegram_id
   - Если у юзера есть mtgate_username — extendProxyUser + toggle если disabled
   - Если нет — createProxyUser, updateUserMtgate
   - createSubscription в БД
   - bot.api.sendMessage(telegram_id, 'Оплата получена! Подписка до {дата}. Ссылка: {tg_link}')
   - res.send('OK' + InvId)

4. successPage(req, res) — GET /robokassa/success:
   - HTML: 'Оплата прошла успешно! Вернитесь в Telegram-бот для получения ссылки.'

5. failPage(req, res) — GET /robokassa/fail:
   - HTML: 'Оплата не завершена. Вернитесь в бот и попробуйте снова.'

ОБНОВИ bot/src/bot.js:
- В buy_1month/buy_3month — вызывать generatePaymentUrl с реальными данными
- Отправлять InlineKeyboard с URL-кнопкой 'Оплатить'

ОБНОВИ bot/src/index.js:
- Добавить Express app на порту 3001
- express.urlencoded({ extended: false }) для парсинга POST
- Роуты: POST /robokassa/result, GET /robokassa/success, GET /robokassa/fail
- Запуск Express ДО запуска бота

Используй только crypto из стандартной библиотеки Node.js для MD5.
"
```

---

## ЭТАП 4.1: ЮKassa (основной платёжный шлюз)

```bash
claude "
Проект MTGate в /opt/mtgate. В bot/src/ уже есть bot.js, database.js, mtgate-client.js, robokassa.js, index.js с Express на порту 3001.

ЗАДАЧА: Добавить ЮKassa как основной платёжный шлюз. Robokassa оставить как резервный — не удалять и не ломать.

СПРАВКА ПО API ЮKassa (v3):
- Документация: https://yookassa.ru/developers/api
- Base URL: https://api.yookassa.ru/v3
- Авторизация: HTTP Basic Auth (shopId : secretKey)
- Создание платежа: POST /v3/payments
  Обязательный заголовок: Idempotence-Key (уникальный UUID для каждого запроса)
  Body (JSON):
  {
    amount: { value: '100.00', currency: 'RUB' },
    capture: true,
    confirmation: {
      type: 'redirect',
      return_url: 'http://72.56.112.182/yookassa/return?telegram_id=XXX'
    },
    description: 'MTGate прокси — 1 месяц',
    metadata: { telegram_id: '123456', plan: '1month', payment_db_id: '42' }
  }
  Ответ: { id, status: 'pending', confirmation: { confirmation_url: '...' } }
- Пользователя редиректим на confirmation.confirmation_url
- Webhook: ЮKassa шлёт POST на указанный URL при смене статуса
  Body (JSON):
  {
    type: 'notification',
    event: 'payment.succeeded' | 'payment.canceled' | ...,
    object: { id, status, amount, metadata, ... }
  }
  Ответить HTTP 200 (любое тело). Если не 200 — ЮKassa повторит.
- IP-адреса ЮKassa для webhook (проверять необязательно, но можно):
  185.71.76.0/27, 185.71.77.0/27, 77.75.153.0/25, 77.75.156.11, 77.75.156.35, 77.75.154.128/25, 2a02:5180::/32

СОЗДАЙ bot/src/yookassa.js:

1. Константы:
   const YOOKASSA_API_URL = 'https://api.yookassa.ru/v3';
   Конфиг из env: YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY

2. createPayment({ amount, description, returnUrl, telegramId, plan, paymentDbId }):
   - POST на YOOKASSA_API_URL + '/payments'
   - Headers:
     Authorization: Basic base64(SHOP_ID:SECRET_KEY)
     Idempotence-Key: crypto.randomUUID()
     Content-Type: application/json
   - Body: amount (value как строка с копейками '100.00', currency 'RUB'),
     capture: true (одностадийный платёж),
     confirmation: { type: 'redirect', return_url: returnUrl },
     description,
     metadata: { telegram_id: String(telegramId), plan, payment_db_id: String(paymentDbId) }
   - Использовать встроенный fetch (Node 20)
   - Вернуть объект: { paymentId, confirmationUrl, status }
   - При ошибке — логировать и бросить

3. getPayment(paymentId):
   - GET на YOOKASSA_API_URL + '/payments/' + paymentId
   - Те же headers (без Idempotence-Key)
   - Вернуть объект платежа

4. createWebhookHandler(bot, db, mtgateClient):
   Возвращает Express middleware (req, res) для POST /yookassa/webhook:
   - Парсить req.body (JSON)
   - Проверить event === 'payment.succeeded'
   - Извлечь из object: id, amount.value, metadata.telegram_id, metadata.plan, metadata.payment_db_id
   - Вызвать db.completePayment(payment_db_id)
   - Определить days: plan === '1month' ? 30 : plan === '3month' ? 90 : 0
   - Получить юзера: db.getUserByTelegramId(telegram_id)
   - Если у юзера есть mtgate_username:
     - mtgateClient.extendProxyUser(mtgate_username, days)
     - Проверить статус юзера в MTGate, если disabled — mtgateClient.toggleProxyUser
   - Если нет mtgate_username:
     - mtgateClient.createProxyUser('tg_' + telegram_id, days)
     - db.updateUserMtgate(user.id, mtgate_username, secret)
   - db.createSubscription(user.id, plan, days)
   - Получить tg_link из ответа MTGate API
   - bot.api.sendMessage(telegram_id, 'Оплата получена! Подписка активна до {дата}.\n\nВаша ссылка: {tg_link}')
   - res.status(200).send('OK')

   Для event === 'payment.canceled':
   - Обновить payment status на 'failed' в SQLite
   - bot.api.sendMessage(telegram_id, 'Платёж отменён. Попробуйте снова: /start')
   - res.status(200).send('OK')

   Для остальных events — просто res.status(200).send('OK')

   Весь handler обернуть в try/catch. При ошибке — логировать, но всё равно отвечать 200 (иначе ЮKassa будет повторять).

5. returnPage(req, res) — GET /yookassa/return:
   - HTML-страница: 'Спасибо! Вернитесь в Telegram-бот. Статус оплаты обновится автоматически.'
   - Кнопка-ссылка на бот: https://t.me/BOT_USERNAME

ОБНОВИ bot/src/bot.js:
- Добавить import yookassa.js
- Определить PAYMENT_PROVIDER из env (значение: 'yookassa' | 'robokassa', по умолчанию 'yookassa')
- В обработчиках buy_1month / buy_3month:
  - db.createPayment(userId, plan, amount) — как раньше
  - Если PAYMENT_PROVIDER === 'yookassa':
    - returnUrl = 'http://' + SERVER_HOST + '/yookassa/return?telegram_id=' + telegramId
    - Вызвать yookassa.createPayment({ amount, description, returnUrl, telegramId, plan, paymentDbId })
    - Отправить InlineKeyboard с URL-кнопкой 'Оплатить' -> confirmationUrl
  - Если PAYMENT_PROVIDER === 'robokassa':
    - Вызвать robokassa.generatePaymentUrl (как было)
    - Отправить InlineKeyboard с URL-кнопкой 'Оплатить' -> robokassa URL
  - Обработать ошибку: если createPayment упал — сообщить юзеру 'Ошибка создания платежа, попробуйте позже'

ОБНОВИ bot/src/index.js:
- Добавить express.json() middleware (для JSON webhook от ЮKassa)
  ВАЖНО: express.json() должен стоять ДО express.urlencoded, или лучше использовать оба
- Добавить роуты:
  POST /yookassa/webhook -> yookassa.createWebhookHandler(bot, db, mtgateClient)
  GET /yookassa/return -> yookassa.returnPage
- Существующие роуты /robokassa/* НЕ трогать — они остаются

ОБНОВИ bot/.env.example — добавить:
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
PAYMENT_PROVIDER=yookassa

ОБНОВИ bot/.env — добавить те же переменные (значения пустые, кроме PAYMENT_PROVIDER=yookassa).

НЕ УДАЛЯТЬ и НЕ МЕНЯТЬ robokassa.js и роуты /robokassa/*. Они остаются как резервный вариант.
Переключение между шлюзами — через переменную PAYMENT_PROVIDER в .env.

Используй только встроенные модули Node.js (crypto, Buffer) + express. Никаких SDK ЮKassa — работаем напрямую с REST API через fetch.
"
```

---

## ЭТАП 5: Scheduler

```bash
claude "
Проект MTGate в /opt/mtgate. В bot/src/ есть bot.js, database.js, mtgate-client.js, robokassa.js.

ЗАДАЧА: Фоновые задачи для управления подписками.

КРИТИЧНО — СУЩЕСТВУЮЩИЕ ПОЛЬЗОВАТЕЛИ:
- В MTGate (users.json) УЖЕ есть активные пользователи с бессрочными токенами.
- Scheduler работает ТОЛЬКО с данными из SQLite (таблица subscriptions).
- Юзеры, которых нет в SQLite — это legacy-пользователи, их НЕЛЬЗЯ трогать.
- Scheduler НИКОГДА не сканирует users.json напрямую — только свою БД.
- Если юзер есть в users.json но нет в SQLite — значит он legacy, не трогаем.

СОЗДАЙ bot/src/scheduler.js:

Экспортирует startScheduler(bot, db, mtgateClient):

1. checkExpiringSoon() — каждые 12 часов:
   - db.getExpiringSoon(3) — подписки из SQLITE, истекающие в ближайшие 3 дня
   - Для каждой (где notified_expiring == 0):
     - Отправить в Telegram сообщение о скором окончании
     - Inline-кнопки оплаты (buy_1month, buy_3month)
     - db.markNotified(subscriptionId)
   - Обернуть в try/catch, логировать ошибки

2. checkExpired() — каждый час:
   - db.getExpired() — активные подписки из SQLITE с expires_at < now
   - Для каждой:
     - mtgateClient.toggleProxyUser(mtgate_username) — выключить
     - db.expireSubscription(subscriptionId)
     - Отправить в Telegram: подписка закончилась + кнопки оплаты
   - ВАЖНО: работаем ТОЛЬКО с юзерами из SQLite, не трогаем legacy из users.json
   - Обернуть в try/catch

3. При старте — запустить обе проверки сразу (на случай если бот был выключен), потом по таймеру.

ОБНОВИ bot/src/index.js:
- Импортировать startScheduler
- Вызвать после bot.start()
"
```

---

## ЭТАП 6: Лендинг

```bash
claude "
Проект MTGate в /opt/mtgate.

ЗАДАЧА: Создать лендинг.

СОЗДАЙ /opt/mtgate/landing/index.html — полностью самодостаточный HTML файл:

Дизайн: тёмная тема (#0f172a фон, белый текст, #3b82f6 акцент). Современный минималистичный. Mobile-first. Никаких внешних зависимостей — всё inline.

Секции:
1. Hero: заголовок 'MTGate — быстрый прокси для Telegram', подзаголовок про стабильный доступ, CTA 'Попробовать бесплатно' -> https://t.me/BOT_USERNAME
2. Три карточки преимуществ: скорость (MTProto), безопасность (шифрование), простота (один клик)
3. Тарифы: триал 3 дня бесплатно, 1 мес 100р, 3 мес 200р (пометка Выгодно). Кнопки -> бот
4. 3 шага подключения: открыть бот, выбрать тариф, нажать ссылку
5. Footer с годом и ссылкой на бот

Плейсхолдер BOT_USERNAME для имени бота — потом заменим sed-ом.
Плавные анимации появления при скролле (IntersectionObserver, без библиотек).
"
```

---

## ЭТАП 7: Docker + Nginx

```bash
claude "
Проект MTGate в /opt/mtgate.

ЗАДАЧА: Контейнеризировать бот и добавить конфиг в существующий Nginx.

КОНТЕКСТ:
- На сервере УЖЕ работает Nginx в Docker-контейнере vless-vpn-nginx (порты 80, 443).
- НЕ нужно ставить свой Nginx — нужно добавить location-блоки в существующий.
- Контейнер бота должен быть доступен из контейнера vless-vpn-nginx по сети.

ПОРЯДОК:

1. Исследуй контейнер vless-vpn-nginx:
   - docker inspect vless-vpn-nginx — найди volumes, конфиг-файлы, сети
   - Найди где лежит nginx.conf или conf.d/ на хосте (через volumes/mounts)
   - Покажи текущий конфиг Nginx
   - Определи в какой Docker-сети работает контейнер

2. СОЗДАЙ bot/Dockerfile:
   FROM node:20-slim
   WORKDIR /app
   COPY package*.json ./
   RUN npm ci --production
   COPY . .
   CMD [\"node\", \"src/index.js\"]

3. СОЗДАЙ bot/.dockerignore:
   node_modules
   data/*.db
   .env

4. ДОБАВЬ в docker-compose.yml сервис mtgate-bot:
   - build: ./bot
   - restart: unless-stopped
   - env_file: ./bot/.env
   - volumes: ./bot/data:/app/data
   - ports: 127.0.0.1:3001:3001 (для доступа с хоста на случай отладки)
   - depends_on: mtgate-admin
   - networks: та же сеть что у остальных сервисов MTGate + сеть vless-vpn-nginx (чтобы Nginx мог проксировать на бот)

5. Добавь MTGate location-блоки в конфиг существующего Nginx (vless-vpn-nginx):
   - Найди подходящий server-блок (порт 80) или создай отдельный conf-файл в conf.d/
   - Добавь:

     location /mtgate/ {
       alias /opt/mtgate/landing/;
       index index.html;
     }

     location /robokassa/ {
       proxy_pass http://mtgate-bot:3001;
       proxy_set_header Host \$host;
       proxy_set_header X-Real-IP \$remote_addr;
       proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
     }

     location /yookassa/ {
       proxy_pass http://mtgate-bot:3001;
       proxy_set_header Host \$host;
       proxy_set_header X-Real-IP \$remote_addr;
       proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
     }

   - Если Nginx-контейнер не видит mtgate-bot по имени — подключи их к общей Docker-сети
   - Если landing нужно отдавать из Nginx-контейнера — примонтируй /opt/mtgate/landing как volume

   ВАЖНО: Не ломай существующий конфиг vless-vpn! Добавляй аккуратно.

6. Проверь конфиг: docker exec vless-vpn-nginx nginx -t
7. Перезагрузи Nginx: docker exec vless-vpn-nginx nginx -s reload

КРИТИЧНО — СУЩЕСТВУЮЩИЕ ПОЛЬЗОВАТЕЛИ:
   На прокси прямо сейчас сидят активные пользователи. При деплое:
   - Собирай и запускай ТОЛЬКО новый контейнер бота: docker compose -f /opt/mtgate/docker-compose.yml up --build -d mtgate-bot
   - НЕ пересобирай mtgate-proxy и mtgate-admin если в них не было изменений!
   - Если admin был изменён (этап 1) и ещё не пересобран — пересобери только его: docker compose up --build -d mtgate-admin
   - Проверь что mtgate-proxy НЕ перезапускался: docker ps — его uptime должен быть старым

8. docker compose -f /opt/mtgate/docker-compose.yml up --build -d mtgate-bot
9. Проверь docker ps — все контейнеры Up, mtgate-proxy с прежним uptime
10. Проверь:
    - curl http://localhost/mtgate/ — лендинг
    - curl http://localhost/robokassa/success — страница успеха
11. Покажи логи: docker compose logs mtgate-bot --tail 20
"
```

---

## ЭТАП 8: Тестирование

```bash
claude "
Проект MTGate в /opt/mtgate. Все компоненты развёрнуты.

ЗАДАЧА: End-to-end тестирование.

1. API тест:
   - Прочитай API_TOKEN из .env
   - POST /api/users — создать test_e2e (ttl_days=1)
   - GET /api/users/test_e2e — проверить данные
   - PATCH /api/users/test_e2e/extend — продлить на 30 дней
   - PATCH /api/users/test_e2e/toggle — выключить
   - PATCH /api/users/test_e2e/toggle — включить обратно
   - DELETE /api/users/test_e2e — удалить
   Каждый шаг: показать curl и ответ. Если ошибка — починить.

2. Лендинг:
   curl -s http://72.56.112.182/mtgate/ | head -5
   Должен быть HTML с MTGate в заголовке.

3. ЮKassa webhook тест:
   - Создать тестового юзера и payment в SQLite
   - Отправить POST /yookassa/webhook с телом:
     {"type":"notification","event":"payment.succeeded","object":{"id":"test-123","status":"succeeded","amount":{"value":"100.00","currency":"RUB"},"metadata":{"telegram_id":"ТВОЙ_TG_ID","plan":"1month","payment_db_id":"ID_ИЗ_SQLITE"}}}
   - Проверить ответ HTTP 200
   - Проверить что payment в SQLite обновился на completed

4. Robokassa webhook тест (если настроен):
   - Сформировать подпись MD5 с test паролями из .env
   - Отправить POST /robokassa/result
   - Проверить ответ OK{InvId}

5. Бот:
   docker compose logs mtgate-bot --tail 30
   Должно быть 'Bot started' без ошибок.

6. Порты:
   ss -tlnp | grep -E ':(80|443|8444|8889|3001) '
   80, 443 и 8444 — на 0.0.0.0
   8889 и 3001 — на 127.0.0.1

7. Firewall:
   ufw status (или iptables)
   Убедись что 80 и 8444 открыты снаружи.

Если всё ок — выведи 'ALL TESTS PASSED'. Если нет — починить и повторить.
"
```

---

## После деплоя

### Действия вручную:
1. Создать бота через @BotFather -> получить токен -> вписать в bot/.env
2. ЮKassa (основной шлюз):
   - Зарегистрировать магазин в ЮKassa (https://yookassa.ru) от КФХ
   - Получить shopId и secretKey -> вписать в bot/.env (YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY)
   - В личном кабинете ЮKassa -> Интеграция -> HTTP-уведомления:
     URL: http://72.56.112.182/yookassa/webhook
     События: payment.succeeded, payment.canceled
   - PAYMENT_PROVIDER=yookassa в bot/.env
3. Robokassa (резервный, опционально):
   - Зарегистрироваться в Robokassa -> получить логин и пароли -> вписать в bot/.env
   - В Robokassa настроить:
     Result URL: http://72.56.112.182/robokassa/result (POST)
     Success URL: http://72.56.112.182/robokassa/success (GET)
     Fail URL: http://72.56.112.182/robokassa/fail (GET)
   - Для переключения: PAYMENT_PROVIDER=robokassa в bot/.env
4. Заменить BOT_USERNAME: `sed -i 's/BOT_USERNAME/ваш_бот/g' /opt/mtgate/landing/index.html`
5. Пересобрать бот: `docker compose -f /opt/mtgate/docker-compose.yml up --build -d mtgate-bot`
6. Тест оплаты через ЮKassa (тестовый магазин в ЛК ЮKassa)
7. Переключить на боевой режим (боевые ключи в .env) -> docker compose restart mtgate-bot

### На будущее:
- Домен + HTTPS (Let's Encrypt)
- Webhook бота вместо long polling
- Реферальная система
- Дашборд доходов
- Recurring payments
- Масштабирование на несколько серверов
