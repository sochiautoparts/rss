# RSS — Telegram → GitHub

Автоматический RSS-фид, агрегирующий последние посты из Telegram-каналов
**@sochiautoparts** и **@bmw_mpower_club** и раздающий их как статический XML
прямо из репозитория на GitHub.

## 🔗 Ссылка на фид

```
https://raw.githubusercontent.com/sochiautoparts/rss/main/feed.xml
```

Добавьте этот URL в любой RSS-ридер (Feedly, Inoreader, NetNewsWire, FreshRSS,
Tiny Tiny RSS и т. д.) — и готово.

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
