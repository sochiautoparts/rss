# RSS — Telegram → GitHub

Автоматический RSS-фид, агрегирующий последние посты из Telegram-каналов
**@sochiautoparts** и **@bmw_mpower_club** и раздающий их как статический XML
прямо из репозитория на GitHub.

## 🔗 Ссылка на фид

**✅ ОСНОВНАЯ — используйте эту (кэш всего 5 мин, всегда свежая):**

```
https://raw.githubusercontent.com/sochiautoparts/rss/main/feed.xml
```

> ⚠️ **НЕ используйте jsDelivr** (`cdn.jsdelivr.net/gh/...`) для этого фида.
> jsDelivr кэширует до 12 часов и имеет распределённый многоуровневый кэш:
> даже после успешного purge (`status: finished`, providers CF+FY инвалидированы)
> jsDelivr продолжает отдавать старую версию с других нод. Проверено:
> фид обновлён в репо в 17:28, а jsDelivr в тот же момент отдавал версию 15:57
> (отставание 1.5 часа). Raw GitHub отдаёт актуальную версию с задержкой ≤5 мин.

`Content-Type: text/plain` у raw-ссылки НЕ проблема — все современные RSS-ридеры
(Feedly, Inoreader, FreshRSS, Tiny Tiny RSS, NetNewsWire) парсят по содержимому,
а не по заголовку. `x-content-type-options: nosniff` предотвращает интерпретацию
как HTML.

Обновление — **каждые 30 минут** через самоперезапускающийся workflow
(см. `.github/workflows/update-feed.yml`).

## Как это работает

```
  GitHub Actions (cron каждые 15 мин)
            │
            ▼
   generate_feed.py
   ├── GET https://t.me/s/sochiautoparts   (+ пагинация ?before=)
   ├── GET https://t.me/s/bmw_mpower_club   (+ пагинация ?before=)
   ├── парсинг HTML (BeautifulSoup) → текст, медиа, дата, ссылка
   ├── 30 свежих постов на канал, объединение, сортировка по дате
   └── запись feed.xml  →  git commit  →  raw.githubusercontent.com
```

- **Без сервера.** Всё работает на GitHub Actions (бесплатно).
- **Без GitHub Pages.** Фид отдаётся как raw-файл — проще и надёжнее.
- **Свежесть ≈ 15 минут.** Cron-расписание `*/15 * * * *`.
- **60 постов** в фиде (по 30 из каждого канала), отсортированы от свежих к старым.
- **Полный контент:** текст поста (HTML в `content:encoded`), медиа (`media:content`),
  прямые ссылки на оригинал в Telegram.

## Локальный запуск

```bash
pip install -r requirements.txt
python generate_feed.py      # создаст feed.xml в текущей папке
```

## Структура

```
.
├── generate_feed.py                # парсер + генератор RSS
├── requirements.txt                # requests, beautifulsoup4, lxml
├── feed.xml                        # артефакт — сам фид (коммитится автоматически)
└── .github/workflows/update-feed.yml
```

## Настройка

Чтобы изменить список каналов или количество постов — отредактируйте константы
`CHANNELS` и `POSTS_PER_CHANNEL` в `generate_feed.py` и сделайте push в `main`.
Workflow сработает на push и сразу пересоберёт фид.

## Зачем raw, а не GitHub Pages?

| | raw.githubusercontent.com | GitHub Pages |
|---|---|---|
| Настройка | не нужна | нужен Pages + билд |
| Кэширование | есть (CDN) | есть |
| `Content-Type` | `text/xml` | `application/xml` |
| Задержка после commit | ~1 мин | до 1–10 мин |
| Лимиты | нетPublished-сборки | билд-лимиты Actions |

RSS-ридеры прекрасно понимают raw-XML, поэтому Pages здесь избыточен.

## Лицензия

MIT
