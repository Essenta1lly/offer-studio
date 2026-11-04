# Offer Studio + FunPay Cardinal

Исходники macOS-приложения и серверной системы управления лотами FunPay. Приватная копия для передачи разработчику, без рабочих данных и авторизаций.

## Состав

- `OfferStudio/Sources` — SwiftUI-приложение: чтение LZT, характеристики, шаблоны, связь с сервером, статусы и очередь создания.
- `OfferStudio/Server` — мониторинг LZT через Playwright, SQLite, локальный HTTP API, нейропомощник, финансовый учёт, очередь создания и тесты.
- `Cardinal` — использовавшаяся серверная версия FunPay Cardinal и FunPayAPI. Авторство и исходная документация сохранены; см. `THIRD_PARTY.md`.
- `Cardinal/plugins/offer_studio.py` — адаптер; исходник для дальнейших изменений находится в `OfferStudio/Server/offer_studio_plugin.py`.
- `examples` — шаблоны настроек с заглушками.
- `scripts/prepare_cardinal.py` — копирует общий код и актуальный адаптер в Cardinal. Повторяйте после изменений модулей.

Не включены: конфиги пользователя, golden_key, ключи нейросетей, Telegram-токены и ID владельцев, SSH-ключи, браузерный профиль/cookies, SQLite, заказы, переписки, лоты, товары автовыдачи, логи, резервные копии, снимки страниц, бинарная сборка и старая Git-история.

## macOS

Нужны macOS 13+ и Xcode Command Line Tools.

```sh
cd OfferStudio
./build.sh
./OfferStudio.app/Contents/MacOS/OfferStudio --self-test
open OfferStudio.app
```

Локальное чтение страниц и шаблоны работают без серверной настройки. Библиотека создаётся заново в `~/Library/Application Support/OfferStudio/Offers/`.

Для сервера создайте `~/Library/Application Support/OfferStudio/Server/connection.json` по образцу `examples/connection.example.json`, добавьте собственный SSH-ключ, проверенный `known_hosts` и файл API-токена. SSH должен разрешать локальные туннели к серверным `127.0.0.1:8765` и `127.0.0.1:6080`. Адреса и ключи прежнего сервера не включены.

## Сервер: первый запуск в отдельном окружении

Нужен Python 3.11+. Команды рассчитаны на чистую копию; не запускайте поверх чужой рабочей установки.

```sh
python3 -m venv .venv
.venv/bin/pip install -r Cardinal/requirements.txt
.venv/bin/pip install -r OfferStudio/Server/requirements.txt
.venv/bin/python scripts/prepare_cardinal.py
.venv/bin/playwright install chromium
cd Cardinal
export OFFERSTUDIO_DATA="$PWD/storage/offer_studio"
../.venv/bin/python main.py
```

Интерактивная настройка Cardinal запросит собственный golden_key и параметры Telegram. Пользователи Telegram авторизуются в Cardinal обычным способом. Они не перенесены из исходной установки. Во время первого запуска сервер создаёт пустую SQLite и случайный `api_token`; файл доступен только владельцу. API слушает только loopback, порт 8765. Не публикуйте его в Интернет.

Чтобы запускать браузерную проверку LZT, установите системные зависимости Playwright (`playwright install-deps chromium` на поддерживаемом Linux), Xvfb и при необходимости x11vnc/noVNC. Запустите `worker.py` с тем же `OFFERSTUDIO_DATA`, `DISPLAY` и установленным Chromium. Вход LZT выполняется владельцем вручную в браузере; CAPTCHA автоматически не решается.

`OfferStudio/Server/*.service` и `.timer` — шаблоны systemd исходной архитектуры. Они используют пользователя `fpc`, каталог `/home/fpc/FunPayCardinal`, браузерное окружение `/opt/offerstudio/venv` и `DISPLAY=:99`. Перед установкой согласуйте пути и пользователя с вашим сервером. Файлы мониторинга, Cardinal и приложения должны использовать одну базу данных.

Для нейропомощника скопируйте `examples/ai_secrets.example.json` в `$OFFERSTUDIO_DATA/ai_secrets.json`, подставьте собственные ключи и установите права 600. Управление: `/ai_status`, `/ai_on`, `/ai_off`. Правила: `OfferStudio/Server/ai_instructions.txt`.

Второй Telegram-бот необязателен: его собственный токен помещается в `mirror_token`, привязка владельца происходит одноразовым кодом из `mirror_pairing`. Без токена работает основной бот Cardinal.

## Основные команды и правила

- `/os_help`, `/os_health`, `/os_status` — управление и состояние служб.
- `/os_stats` — завершённые и ожидаемые продажи, возвраты и прибыль без комиссии.
- `/os_order ЗАКАЗ LZT_ID` — явная связь заказа с аккаунтом и фиксация закупки.
- `/os_refund ЗАКАЗ СУММА ВОЗВРАТ_ЗАКУПКИ` — фактические суммы возврата.
- Новые лоты создаются по цене закупка ×2,5, активными, с выключенной автовыдачей.
- Ошибка LZT не считается продажей. Перед автоснятием требуется повторное подтверждение.
- Неоднозначное создание лота не повторяется автоматически: это защищает от дублей.
- Помощник проверяет владельца и покупателя заказа; ручной ответ продавца приостанавливает диалог.

## Тесты

Запускайте с отдельной временной БД, чтобы не затронуть рабочую систему:

```sh
cd OfferStudio/Server
OFFERSTUDIO_DATA="$(mktemp -d)" ../../.venv/bin/python -m unittest test_core test_operations test_plugin test_creation test_ai test_upgrade test_runtime -v
```

Playwright нужен для runtime-тестов. Тесты не должны публиковать реальные лоты или писать покупателям. Реальные страницы и финансовые данные из исходной установки не включены.

Перед каждым коммитом:

```sh
gitleaks dir . --redact --no-banner
```

Рабочие данные и ключи исключены через `.gitignore`. Он защищает будущие коммиты, но не заменяет проверку секретов.
