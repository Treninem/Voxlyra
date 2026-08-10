# Массовый импорт комиксов VoxLyra

Одна кнопка принимает общий ZIP с независимыми корневыми папками `Books` и `Comics`.

```text
Comics/
  001/
    metadata.json
    description.txt
    cover.jpg
    Chapters/
      001/
        chapter.json
        001.webp
        002.webp
      002.cbz
    Volumes/
      002/
        volume.json
        Chapters/
          001.pdf
          002/
            001.jpg
            002.jpg
```

`metadata.json`:

```json
{
  "title": "Название",
  "author": "Автор",
  "genre": ["Фэнтези"],
  "age_rating": "16+",
  "language": "ru",
  "year": "2026",
  "content_type": "manhwa",
  "reading_mode": "vertical",
  "license": "author_permission",
  "rights_checked": true,
  "source": "источник разрешения",
  "free_or_paid": "free",
  "price_stars": 0,
  "preview_pages": 3
}
```

Допустимые `content_type`: `comic`, `manga`, `manhwa`, `webtoon`, `graphic_novel`.

Режимы: `ltr`, `rtl`, `vertical`, `single`, `spread`. Для manga обычно `rtl`, для manhwa/webtoon — `vertical`.

Глава может быть папкой отдельных изображений либо одним файлом PDF/CBZ/ZIP/CBR/RAR/7Z/fixed-layout EPUB. Смешивать архив и отдельные изображения внутри одной главы нельзя.

Все импортированные произведения и главы создаются как черновики. Публичная публикация выполняется только после проверки.
