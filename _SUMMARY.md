---
type: project
phase_status: paused
status: paused
owner: Павел
stack:
  - Go
  - MTProto
  - Docker
  - ЮKassa
  - device binding
last_active: 2026-04-20
next: Миграция со server-fr1 (FR1 перегружен, mtgate ест 184MB). Куда — отдельная VPS или на SPB1 в webproxy?
goal: Платный MTProto-прокси с админкой и оплатой через ЮKassa
---

# mtgate

## TL;DR
Платный MTProto-прокси для Telegram с device binding и оплатой через ЮKassa. Live в `/opt/mtgate/` на server-fr1. ~184 MB RAM (самый тяжёлый сервис на FR1). Содержит admin-панель, billing, привязку устройств.

## Стек
- **Go** (MTProto-прокси)
- **Платежи:** ЮKassa
- **Контейнеризация:** Docker Compose (admin + proxy)
- **Хост:** server-fr1 (`/opt/mtgate/`)

## Структура папки
| Подпапка | Что |
|---|---|
| `admin/` | Панель управления + billing |
| `proxy/` | MTProto core |
| `docker-compose.yml` | Production compose |
| `mtgate-monetization-prompts.md` | Спека/промпты по биллингу |

## Текущие задачи
- [ ] **Миграция со server-fr1** — он перегружен (RAM 76%, swap 70%), mtgate ест ~184 MB
- [ ] Конфликт портов на FR1: 80/443 (vless-vpn vs captcha_bot), 8443 (mtg vs vless-vpn)
- [ ] Бэкап SQLite/Postgres mtgate — в DR-kit `pull-dr-kit.sh`?
- [ ] Telegram-уведомления о платежах через `Console10_Knowledge_bot`

## Открытые вопросы
- Куда мигрировать — отдельная VPS или на server-spb1 в webproxy?
- Хранение device-bind — SQLite (текущий) или вынести в общий Postgres?
- Делать ли публичный лендинг с тарифами или оставить только по invite?

## Ключевые решения (последние 5)
| Дата | Решение | Линк |
|---|---|---|
| — | Device binding для отслеживания подписок | архитектура |
| — | ЮKassa raw API (не SDK) — стандарт для всех проектов Павла | стандарт |
| — | Размещение на FR1 (DE exit-IP) — нужен из РФ через VPN | infra-выбор |
| — | Отдельный MTProto-сервер `mtg` рядом — для тестов | стандарт |

## Полезные команды
```bash
# SSH к серверу
ssh server-fr1
cd /opt/mtgate

# Управление
docker compose ps
docker compose logs -f admin
docker compose logs -f proxy
docker compose restart

# RAM-мониторинг
docker stats --no-stream | grep mtgate
```

## Связанное
- [INSTRUCTION.md §3](../INSTRUCTION.md) — server-fr1 контекст
- [servers/_SUMMARY.md](../servers/_SUMMARY.md) — инфраструктурный hub
