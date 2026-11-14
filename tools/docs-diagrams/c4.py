#!/usr/bin/env python3
"""Схемы C4 для README: контекст, контейнеры, развёртывание и путь доступа к VPN.

Запуск: python tools/docs-diagrams/c4.py. Связи сверены с кодом: runtime.py (кто какие адаптеры
собирает), web.py и */api.py (ручки), cron/main.py, jobs/tasks.py, events/outbox.py, docker-compose.yml,
.gitlab-ci.yml и deploy/release.sh.
"""

from svgkit import ROOT, Canvas

OUT = "docs/architecture/diagrams/c4"

BOTH = ("both", "Запрос и ответ: 1-я строка — запрос, «Ответ:» — что возвращается")
LINE = ("line", "Передача в одну сторону")


def context():
    c = Canvas("C4 · контекст", 1800, 1010)
    c.person("user", 40, 60, 290, 200, "Абонент", "Person", "Мобильное приложение с VPN-клиентом")
    c.person("support", 40, 330, 290, 200, "Поддержка", "Person", "Начисляет дни, разбирает спорные платежи")
    c.person("tg", 40, 700, 290, 200, "Абонент в Telegram", "Person", "Регистрация, код входа, оплата")

    c.box("vas", 720, 120, 360, 540, "vpn-access-service", "Software System",
          "Аккаунты и вход, тарифы и оплата, автопродление, выдача доступа в VPN-панели",
          kind="system", size=22)

    c.box("acq", 1380, 80, 360, 200, "Эквайринг", "Software System", "Платёжная страница, сохранённые карты, вебхуки", kind="external", size=20)
    c.box("panel", 1380, 320, 360, 120, "VPN-панель", "Software System", "Remnawave-подобный API, ведёт VPN-узлы", kind="external", size=20)
    c.box("mail", 1380, 470, 360, 100, "Почтовый сервис", "Software System", "Коды входа и письма", kind="external", size=20)
    c.box("fcm", 1380, 590, 360, 100, "FCM", "Software System", "Пуш-уведомления", kind="external", size=20)
    c.box("tgapi", 720, 773, 360, 130, "Telegram Bot API", "Software System", kind="external", size=20)

    c.edge(("user", "r", 0.5), ("vas", "l", (198 - 120) / 540), both=True,
           label=("Вход, оплата, ссылка на VPN", "Ответ: токены, ссылки", "[HTTPS/JSON]"))
    c.edge(("support", "r", 0.5), ("vas", "l", (468 - 120) / 540), both=True,
           label=("Начислить дни, платежи on_hold", "Ответ: список на разбор", "[внутренний API · ключ]"))
    c.edge(("tg", "r", 0.5), ("tgapi", "l", 0.5), both=True, label=("Команды, кнопки", "Ответ: тарифы, статус"))
    c.edge(("vas", "b", 0.5), ("tgapi", "t", 0.5), both=True, at=(900, 716),
           label=("Опрос обновлений, сообщения", "Ответ: команды", "[HTTPS · Bot API]"))

    c.edge(("vas", "r", 10 / 540), ("acq", "l", 0.25), both=True, at=(1230, 130),
           label=("Платёж, списание с карты, статус", "Ответ: ссылка на оплату, исход", "[HTTPS/JSON]"))
    c.edge(("acq", "l", 0.75), ("vas", "r", 110 / 540), at=(1230, 230),
           label=("Вебхук об исходе", "[HTTPS · подпись]"))
    c.edge(("vas", "r", 260 / 540), ("panel", "l", 0.5), both=True, at=(1230, 380),
           label=("Пользователь, срок, вкл/выкл", "Ответ: ссылка подписки", "[HTTPS/JSON]"))
    c.edge(("vas", "r", 400 / 540), ("mail", "l", 0.5), at=(1230, 520), label=("Код входа, письма", "[HTTPS]"))
    c.edge(("vas", "r", 520 / 540), ("fcm", "l", 0.5), at=(1230, 640), label=("Пуш", "[HTTPS]"))

    c.legend(1140, 750, [
        ("person", "Пользователь [Person]"),
        ("system", "Система [Software System]"),
        ("external", "Внешняя система [Software System]"),
        BOTH, LINE,
    ], width=620)
    return c.write(f"{OUT}/c4-context.drawio.svg")


def containers():
    c = Canvas("C4 · контейнеры", 2060, 1640)
    c.person("user", 20, 360, 240, 230, "Абонент", "Person", "Мобильное приложение")
    c.person("tg", 20, 700, 240, 230, "Абонент в Telegram", "Person")
    c.box("tgapi", 20, 1000, 240, 130, "Telegram Bot API", "Software System", kind="external", size=17)

    c.box("mail", 560, 30, 290, 130, "Почтовый сервис", "Software System", "Коды входа", kind="external", size=18)
    c.box("acq", 1180, 30, 380, 130, "Эквайринг", "Software System", "Платёжная страница, карты, вебхуки", kind="external", size=18)
    c.box("notify", 1250, 1420, 300, 130, "Каналы уведомлений", "Software System", "Telegram Bot API, почта, FCM", kind="external", size=17)
    c.box("panel", 1590, 1420, 300, 130, "VPN-панель", "Software System", "Remnawave-подобный API", kind="external", size=18)

    c.boundary(470, 230, 1400, 1120, "vpn-access-service", "Software System")
    c.box("api", 560, 421, 290, 170, "api", "Container: FastAPI", "Вход, тарифы, checkout, вебхуки, ссылка на VPN")
    c.database("redis", 560, 690, 290, 180, "Redis", "Container: кеш", "Лимиты запросов, кеш тарифов, локи синхронизации, буфер аналитики")
    c.box("bot", 560, 980, 290, 170, "bot", "Container: aiogram 3", "Клиент тех же сервисов: регистрация, код входа, оплата")
    c.database("pg", 980, 640, 280, 230, "PostgreSQL 16", "Container: база", "Пользователи, платежи, журнал периодов, outbox")
    c.box("cron", 1360, 421, 320, 170, "cron", "Container: APScheduler", "Активен один экземпляр по advisory-локу: outbox, автопродление, сверки")
    c.queue("broker", 1360, 680, 320, 130, "broker", "Container: Redis, очереди Celery", "access, billing, notify, analytics")
    c.box("jobs", 1360, 980, 320, 170, "jobs", "Container: Celery", "access.push, billing.charge_renewal, notify.send")
    c.note(980, 1195, 380, "Опционально: ClickHouse (cron сбрасывает в него буфер событий из Redis) "
                          "и Prometheus — профили compose analytics и monitoring.")

    c.edge(("user", "r", 0.5), ("api", "l", 0.5), both=True, at=(365, 506),
           label=("Вход, оплата, ссылка", "Ответ: токены, ссылки", "[HTTPS/JSON]"))
    c.edge(("tg", "b", 0.5), ("tgapi", "t", 0.5), both=True, at=(140, 965), label=("Команды",))
    c.edge(("bot", "l", 0.5), ("tgapi", "r", 0.5), both=True, at=(365, 1065),
           label=("Опрос обновлений", "Ответ: команды", "[long polling]"))

    c.edge(("api", "t", 0.5), ("mail", "b", 0.5), at=(705, 330), label=("Код входа", "[HTTPS]"))
    c.edge(("api", "r", 0.2), ("acq", "b", 0.25), via=[(1275, 455)], both=True, at=(1060, 455),
           label=("Платёж; вебхук об исходе", "Ответ: ссылка на оплату", "[HTTPS · подпись]"))
    c.edge(("cron", "t", 0.3), ("acq", "b", (1456 - 1180) / 380), both=True, at=(1456, 320),
           label=("Сверка платежей", "Ответ: статус"))
    c.edge(("api", "r", 0.75), ("pg", "t", 60 / 280), via=[(1040, 548.5)], both=True, at=(940, 548), label=("SQL",))
    c.edge(("cron", "l", 0.75), ("pg", "t", 220 / 280), via=[(1200, 548.5)], both=True, at=(1290, 548),
           label=("SQL", "[advisory lock]"))
    c.edge(("api", "b", 0.5), ("redis", "t", 0.5), both=True, at=(705, 640), label=("Лимиты, кеш тарифов",))
    c.edge(("bot", "t", 0.5), ("redis", "b", 0.5), both=True, at=(705, 925), label=("Кеш тарифов",))
    c.edge(("bot", "r", 0.5), ("pg", "b", 140 / 280), via=[(1120, 1065)], both=True, at=(985, 1065),
           label=("Те же сервисы", "[SQL]"))
    c.edge(("cron", "b", 0.5), ("broker", "t", 0.5), at=(1520, 635), label=("Outbox → задачи", "[Celery]"))
    c.edge(("broker", "b", 0.5), ("jobs", "t", 0.5), at=(1520, 895), label=("Задачи", "[по очередям]"))
    c.edge(("jobs", "l", 0.5), ("pg", "b", 220 / 280), via=[(1200, 1065)], both=True, at=(1280, 1065), label=("SQL",))
    c.edge(("jobs", "r", 0.5), ("acq", "r", 0.5), via=[(1930, 1065), (1930, 95)], both=True, at=(1745, 95),
           label=("Списание с сохранённой карты", "Ответ: исход", "[автопродление]"))
    c.edge(("jobs", "b", 0.125), ("notify", "t", 0.5), at=(1400, 1250), label=("Уведомления",))
    c.edge(("jobs", "b", 0.875), ("panel", "t", 50 / 300), both=True, at=(1640, 1290),
           label=("Пользователь, срок", "Ответ: ссылка подписки"))

    c.legend(470, 1400, [
        ("person", "Пользователь [Person]"),
        ("container", "Контейнер vpn-access-service [Container]"),
        ("external", "Внешняя система [Software System]"),
        BOTH, LINE,
    ], width=620)
    return c.write(f"{OUT}/c4-containers.drawio.svg")


def deployment():
    c = Canvas("C4 · развёртывание", 1810, 1490)
    c.person("dev", 20, 60, 200, 210, "Разработчик", "Person")
    c.node(360, 20, 790, 330, "GitLab", "Deployment Node: GitLab CI", fill="#F4F6F8")
    c.box("ci", 400, 119, 360, 150, "Пайплайн", "Container: GitLab CI",
          "lint · schema · pytest · image; релиз и откат — ручные джобы")
    c.box("registry", 880, 119, 250, 150, "Container Registry", "Container", "Образ на коммит: :SHA")
    c.box("ext", 1480, 40, 290, 170, "Внешние API", "Software System",
          "Эквайринг, VPN-панель, Telegram Bot API, почта, FCM", kind="external", size=17)

    c.node(360, 420, 1410, 790, "Сервер", "Deployment Node: Linux, Docker Compose · /srv/vas", fill="#F4F6F8", ident="server")
    c.box("api", 400, 500, 260, 150, "api", "Container: uvicorn · :8000", "Graceful shutdown 20 с, healthcheck /health/ready", size=16)
    c.box("jobs", 720, 500, 260, 150, "jobs", "Container: Celery", "Очереди access, billing, notify, analytics", size=16)
    c.box("cron", 1040, 500, 260, 150, "cron", "Container: APScheduler · :9101", "Лидер по advisory-локу, метрики на :9101", size=16)
    c.box("release", 1360, 500, 370, 150, "release.sh", "Script",
          "pull → schema → up --wait api jobs cron → ждёт новый SHA в /version; откат — previous_sha", size=16)
    c.database("db", 400, 760, 260, 190, "PostgreSQL 16", "Container: том db", "Схему ведёт alembic", size=16)
    c.database("redis", 720, 760, 260, 190, "Redis", "Container: кеш", "allkeys-lru, 256 МБ", size=16)
    c.queue("broker", 1040, 780, 260, 150, "broker", "Container: Redis", "AOF, noeviction, том broker", size=16)
    c.box("schema", 1360, 780, 260, 150, "schema", "Container: alembic upgrade head", "Одноразовый, перед стартом процессов", size=16)
    c.box("bot", 400, 1010, 260, 150, "bot", "Container: профиль bot", "Нужен VAS_BOT_TOKEN", size=16)
    c.box("prom", 720, 1010, 260, 150, "Prometheus", "Container: профиль monitoring", "Метрики api и cron, правила алертов", size=16)
    c.database("ch", 1040, 1000, 260, 170, "ClickHouse", "Container: профиль analytics", "Продуктовые события", size=16)
    c.note(1360, 1010, 370, "Один образ на все процессы, разница только в команде. Процессы находят db, redis и broker "
                            "по именам сервисов compose. Миграции совместимы со старым кодом: expand → migrate → contract, "
                            "поэтому откат образа не требует отката схемы.")

    c.person("user", 20, 440, 200, 210, "Абонент", "Person")

    c.edge(("dev", "r", 0.5), ("ci", "l", (191.35 - 119) / 150), at=(290, 191), label=("git push",))
    c.edge(("ci", "r", 0.5), ("registry", "l", 0.5), at=(820, 194), label=("docker push",))
    c.edge(("ci", "b", 0.85), ("release", "t", 82 / 370), via=[(706, 390), (1442, 390)], at=(1150, 390),
           label=("Релиз и откат по кнопке", "[ssh · release.sh SHA]"))
    c.edge(("registry", "b", 0.5), ("release", "t", 290 / 370), via=[(1005, 330), (1650, 330)], at=(1320, 330),
           label=("docker compose pull", "[образ :SHA]"))
    c.edge(("user", "r", 0.5), ("api", "l", (571.35 - 500) / 150), at=(290, 571), label=("HTTPS",))
    c.edge(("server", "t", (1700 - 360) / 1410), ("ext", "b", (1700 - 1480) / 290), both=True, at=(1700, 280),
           label=("HTTPS к внешним API", "вебхук эквайринга — в api"))

    c.legend(360, 1250, [
        ("person", "Пользователь [Person]"),
        ("node", "Узел развёртывания [Deployment Node]"),
        ("container", "Контейнер или скрипт [Container]"),
        ("external", "Внешняя система [Software System]"),
        BOTH, LINE,
    ], width=620)
    return c.write(f"{OUT}/c4-deployment.drawio.svg")


def vpn_access():
    c = Canvas("Доступ к VPN", 1920, 1140)
    c.node(40, 60, 420, 330, "Устройство абонента", "Deployment Node: телефон или ноутбук", fill="#F4F6F8")
    c.box("app", 80, 140, 340, 200, "Приложение с VPN-клиентом", "Container: клиент",
          "Хранит токены, импортирует ссылку подписки, поднимает туннель", kind="external", size=17)
    c.box("vas", 900, 110, 380, 190, "vpn-access-service", "Software System",
          "Знает, до какого числа оплачен доступ, и приводит к этому панель", kind="system", size=20)

    c.boundary(40, 470, 1300, 450, "VPN-инфраструктура", "вне проекта")
    c.box("panel", 900, 560, 380, 170, "VPN-панель", "Software System: Remnawave-подобная",
          "Пользователи, сроки, ключи; раздаёт конфигурацию по ссылке подписки", kind="external", size=18)
    c.box("nodes", 80, 560, 340, 170, "VPN-узлы", "Container: несколько серверов",
          "Принимают подключения по ключу пользователя", kind="external", size=18)
    c.box("net", 80, 980, 340, 110, "Интернет", "", "Сайты и сервисы", kind="external", size=18)
    c.note(470, 770, 840, "Сервис не видит трафик: он только создаёт и продлевает пользователя в панели. "
                          "Узлы пускают по данным панели, поэтому срок хранится и у нас (paid_until), и в панели "
                          "(expire_at); отставание панели видно метрикой access_lag_users, догоняет cron раз в 5 минут. "
                          "В стенде панель заменяет заглушка fakes_upstream, узлов нет.")

    c.edge(("app", "r", 0.5), ("vas", "l", (240 - 110) / 190), both=True, at=(680, 240),
           label=("1. Ссылка подписки", "Ответ: URL или 202, Retry-After", "[HTTPS · GET /access/link]"))
    c.edge(("vas", "b", 0.5), ("panel", "t", 0.5), both=True, at=(1090, 385),
           label=("2. Создать / продлить пользователя", "Ответ: URL подписки", "[HTTPS · задача access.push]"))
    c.edge(("app", "r", 0.8), ("panel", "l", 50 / 170), via=[(700, 300), (700, 610)], both=True, at=(700, 375),
           label=("3. Конфигурация узлов", "Ответ: адреса и ключи", "[HTTPS · по URL подписки]"))
    c.edge(("panel", "l", 0.5), ("nodes", "r", 0.5), at=(560, 645), label=("Пользователи, ключи, сроки",))
    c.edge(("app", "b", 0.5), ("nodes", "t", 0.5), both=True, at=(250, 430), label=("4. Шифрованный туннель",))
    c.edge(("nodes", "b", 0.5), ("net", "t", 0.5), both=True, at=(250, 825), label=("5. Трафик абонента",))

    c.legend(1400, 470, [
        ("node", "Устройство [Deployment Node]"),
        ("system", "Наш сервис [Software System]"),
        ("external", "Вне проекта: клиент, панель, узлы"),
        BOTH, LINE,
    ], width=480)
    return c.write(f"{OUT}/vpn-access.drawio.svg")


if __name__ == "__main__":
    for build in (context, containers, deployment, vpn_access):
        print(build().relative_to(ROOT))
