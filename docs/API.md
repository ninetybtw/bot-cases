# API мини-приложения

Версия 1.4-cs2. Базовый адрес `/v1`, все ответы в JSON. Контракт пакета 1.4 (`docs/API.md` пакета), реализация в `skinsbot/web.py`.

**Заголовки**

| Заголовок | Значение |
| --- | --- |
| `Authorization` | `tma <initData>` из Telegram WebApp, сервер проверяет подпись |
| `Idempotency-Key` | обязателен для всех POST и DELETE, повтор с тем же ключом вернёт тот же ответ |
| `X-Dev-User` | только при `DEV_MODE=1`: вход без Telegram под указанным ID |

**Деньги** всегда в копейках игрового рубля (`*_cents`), звёзды целым числом. Клиент переводит в валюту страны по блоку `currency` из `GET /me`.

**Ошибки**

```json
{ "error": { "code": "NOT_ENOUGH_COINS", "message": "Не хватает 400 ₽. Задания и колесо дадут монеты.", "need_cents": 40000 } }
```

Коды: `UNAUTHORIZED`, `RATE_LIMIT`, `BAD_REQUEST`, `NOT_FOUND`, `NOT_ENOUGH_COINS`, `NO_ENERGY`, `DAILY_LIMIT`, `LEVEL_LIMIT`, `INVENTORY_FULL`, `ITEM_LOCKED`, `ITEM_BUSY`, `PRICE_OUT_OF_BAND`, `STARS_MONTH_CAP`, `NOT_ALLOWED_YET`, `CONFIG_CHANGED`, `CONFIRM_REQUIRED` (с `pause_s`), `CONFLICT`, `DISABLED`, `FORBIDDEN`. Текст `message` написан в валюте страны игрока.

## Профиль и конфиг

| Метод | Путь | Что делает |
| --- | --- | --- |
| GET | `/config` | Публичный конфиг: ступени, шансы кейса, уровни с ценами и целью, кейсы с составом, износ, лимиты, колёса, валюты |
| GET | `/me` | Профиль: уровень и опыт, страна и `currency`, баланс, энергия, попытки, бонусные спины, лимиты дня, удача, гарантии (`epic_chance_pct`), места и переполнение, задания дня, честная игра, витрина, последний дроп, ссылка-приглашение |
| PUT | `/me/country` | `{ "country": "KZ" }`, не чаще раза в 30 дней |
| POST | `/me/last-drop/ack` | Последний дроп показан после обрыва анимации |
| GET | `/users/:id` | Публичный профиль для обмена: витрина и свободные предметы |

## Спин и честная игра

`POST /spin` `{ "case_id": "crate-4904", "level": "common", "source": "free|coin|attempt", "count": 1, "confirmed": false }`

`level` — `common` (обычный кейс, нужен `case_id`) или `rare | epic | legendary | elite` (только `coin`). Ответ:

```json
{
  "drops": [{ "drop_id": 12, "items": [{ "id": 55, "tier": "legendary", "name": "…", "wear": "FT", "float": 0.2314,
              "stattrak": true, "pattern": 412, "price_cents": 18450, "one_in": 31, "image_url": "…" }],
              "reel": [{ "tier": "rare", "image_url": "…", "name": "…" }], "stop_index": 52, "pity_applied": null }],
  "fair": { "server_seed_hash": "…", "client_seed": "…", "nonce": 7 },
  "balance_cents": 114000, "energy": { "now": 1, "max": 3, "next_at": 1791830000 }, "overflow": 0
}
```

| Метод | Путь | Что делает |
| --- | --- | --- |
| GET | `/fair` | Хеш текущего ключа сервера, свой ключ, nonce, последние 50 бросков с раскрытыми ключами прошлых пар |
| POST | `/fair/rotate` | `{ "client_seed": "artem" }` — новая пара, старый ключ раскрывается |

## Коллекция

| Метод | Путь | Что делает |
| --- | --- | --- |
| GET | `/inventory?state=&tier=&q=&sort=new\|price\|tier\|growth` | Предметы и сводка: места, переполнение, стоимость, изменение за неделю |
| GET | `/items/:id` | Карточка, график цены 30 дней, история событий |
| POST | `/items/sell` | `{ "item_ids": [...], "confirmed": true }` — продажа системе по 60%, ответ с `sale_id` и `undo_until` |
| POST | `/sales/:id/undo` | Отмена продажи в течение 10 минут, не больше 5 раз в сутки |
| POST | `/items/:id/lock`, `/items/:id/unlock` | Замок; снятие с `{ "confirm": true }` |
| POST | `/items/sell-duplicates/quote` | Дубли и сумма до подтверждения |
| POST | `/items/sell-cheaper/quote` | `{ "max_cents": 5000 }` — всё дешевле суммы, кроме Легендарного+ и под замком |
| POST | `/showcase` | `{ "item_ids": [a, b, c] }` — витрина профиля |

## Контракты и апгрейдер

| Метод | Путь | Что делает |
| --- | --- | --- |
| POST | `/contracts` | `{ "item_ids": [5 штук], "confirmed": true }` |
| GET | `/upgrades/targets?stake_cents=&mult=2` | Цели дороже ставки примерно в `mult` раз |
| POST | `/upgrades/quote` | `{ "stake_item_ids", "stake_coins_cents", "target": { "catalog_item_id", "wear", "stattrak" } }` → шанс, формула, нужно ли подтверждение |
| POST | `/upgrades` | То же плюс `quote_chance_pct` и `confirmed`. Если шанс изменился — `CONFIG_CHANGED`. Ответ: `success`, `roll` (0–100), `chance_pct`, `parts_cents`, `next_bonus_pct` |

## Рынок и обмены

| Метод | Путь | Что делает |
| --- | --- | --- |
| GET | `/market/lots?currency=coins&tier=&q=&sort=&mine=1` | Витрина или мои лоты |
| POST | `/market/lots` | `{ "item_id", "price", "currency": "coins" }` — вилка 50–150%, до 20 лотов, аккаунт старше 24 ч, без удержания |
| DELETE | `/market/lots/:id` | Снять лот |
| POST | `/market/lots/:id/buy` | Купить: комиссия 10% (VIP 5%) уходит из игры, удержание 24 ч |
| POST | `/market/lots/:id/invoice` | Рынок за Stars: выключен, `DISABLED` |
| GET/POST | `/market/orders` | Заявки на покупку: монеты блокируются, исполняются автоматически |
| DELETE | `/market/orders/:id` | Снять заявку, монеты возвращаются |
| GET | `/market/deals` | Мои сделки |
| GET | `/trades` | Обмены |
| POST | `/trades` | `{ "to_user", "give": [...], "take": [...] }` |
| POST | `/trades/:id/accept\|decline\|cancel` | Ответ на обмен |

## Удержание и социальное

| Метод | Путь | Что делает |
| --- | --- | --- |
| POST | `/wheel/daily`, `/wheel/weekly` | Колёса; недельное — если задания в 5 днях из 7 |
| GET | `/quests` | Задания дня и недели |
| POST | `/quests/:code/claim` | Награда: монеты, билет розыгрыша, косметика |
| GET | `/raffle/today` | Мои билеты, призы, хеш зерна, вчерашние победители с раскрытым зерном |
| GET | `/leaderboard?board=capital\|rarest\|level\|collection&scope=friends\|global` | Рейтинг, по умолчанию друзья |
| GET | `/friends` | Приглашённые, ступени наград |
| GET/POST | `/duels` | Дуэли без ставок: `{ "item_ids": [3], "friend_id": null }` |
| POST | `/duels/:id/join`, `/duels/:id/cancel` | Принять или отменить |
| GET | `/cosmetics`, POST `/cosmetics/equip` | Рамки и значки |
| GET | `/feed` | Лента дропов Эпик+ |
| POST | `/share/:item_id` | Ссылка на PNG-карточку дропа и текст для `shareToStory` |
| GET | `/share/:item_id.png?t=` | Сама картинка (подписанная ссылка) |

## Магазин Stars

| Метод | Путь | Что делает |
| --- | --- | --- |
| GET | `/shop` | Товары, остатки лимитов, траты за месяц из 500 ⭐ |
| POST | `/shop/invoice` | `{ "product": "attempts_20" }` → `{ "invoice_link" }`; выдача в боте по `successful_payment` |

## Админка

Префикс `/v1/admin`. Вход: Telegram ID из `ADMIN_IDS` и TOTP-код в `X-Admin-Code`. Страница — `/admin`.

| Путь | Что делает |
| --- | --- |
| `GET /admin/economy?days=14` | Приток и слив по дням и причинам, приток на активного игрока в платных спинах, алерт, возврат по шкалам |
| `GET /admin/configs`, `POST /admin/configs/check`, `POST /admin/configs` | Версии конфига: проверка (суммы шансов, возврат с гарантиями 78–82%, курсы), сравнение, сохранение и включение |
| `GET /admin/items/:id`, `POST /admin/items/:id/freeze\|unfreeze` | Предмет, история, дроп, заморозка |
| `GET /admin/users/:id`, `POST /admin/users/:id/ban\|unban` | Игрок, журнал денег, продажи, бан |
| `POST /admin/sales/:id/rollback` | Откат продажи системе с записью в журналы |
| `GET /admin/fraud`, `POST /admin/fraud/:id/resolve` | Очередь антифрода |
| `GET /admin/payments`, `POST /admin/refunds/:id` | Платежи Stars и возврат |
