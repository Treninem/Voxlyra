def build_new_book_post(title: str, genre: str, age_limit: str, chapters_count: int, has_audio: bool) -> str:
    audio_line = "\n🎧 Есть аудиоверсия" if has_audio else ""
    return (
        "📖 Новая история на Вокслире\n\n"
        f"«{title}»\n\n"
        f"Жанр: {genre}\n"
        f"Возраст: {age_limit}\n"
        f"Глав доступно: {chapters_count}"
        f"{audio_line}\n\n"
        "👇 Читать в боте"
    )
