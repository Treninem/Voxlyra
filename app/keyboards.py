from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.permissions import DELEGABLE_PERMISSIONS, MODERATION_BUTTONS


def main_menu(is_owner: bool, has_admin_access: bool, has_author_profile: bool = False) -> InlineKeyboardMarkup:
    """Главная навигация: только рабочие разделы, одинаковая сетка и без служебных кнопок."""
    kb = InlineKeyboardBuilder()
    if settings.WEBAPP_URL:
        base = settings.WEBAPP_URL.rstrip('/')
        kb.button(text="📚 Книги", web_app=WebAppInfo(url=f"{base}/catalog"))
        kb.button(text="🖼 Комиксы", web_app=WebAppInfo(url=f"{base}/comics"))
        kb.button(text="🎧 Слушать", web_app=WebAppInfo(url=f"{base}/audio"))
    kb.button(text="⭐ Моё", callback_data="main:my")
    kb.button(text="✍️ Автору", callback_data="author:menu")
    kb.button(text="💎 Бонусы", callback_data="main:bonuses")
    kb.button(text="⚙️ Ещё", callback_data="main:more")
    if has_admin_access:
        kb.button(text="🛡 Модерация", callback_data="mod:menu")
    if is_owner:
        kb.button(text="👑 Управление", callback_data="owner:menu")
    if settings.WEBAPP_URL:
        kb.adjust(2, 1, 2, 2, 1, 1)
    else:
        kb.adjust(2, 2, 1, 1)
    return kb.as_markup()


def more_menu(has_author_profile: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if settings.WEBAPP_URL:
        kb.button(text="🎨 Оформление", web_app=WebAppInfo(url=f"{settings.WEBAPP_URL.rstrip('/')}/settings"))
    else:
        kb.button(text="🎨 Оформление", callback_data="main:settings")
    kb.button(text="🛟 Поддержка", callback_data="main:support")
    kb.button(text="📜 Правила", callback_data="main:legal")
    kb.button(text="⬅️ Назад", callback_data="menu:main")
    kb.adjust(1, 2, 1)
    return kb.as_markup()


def owner_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if settings.WEBAPP_URL:
        kb.button(text="📱 Панель управления", web_app=WebAppInfo(url=f"{settings.WEBAPP_URL.rstrip('/')}/control"))
    rows = [
        ("👥 Администрация", "owner:admins"),
        ("📚 Книги", "owner:books"),
        ("✍️ Авторы", "owner:authors"),
        ("👤 Пользователи", "owner:users"),
        ("💰 Финансы", "owner:finance"),
        ("📢 Канал", "owner:channel"),
        ("🧾 Жалобы", "owner:complaints"),
        ("📊 Статистика", "owner:stats"),
        ("⚙️ Настройки", "owner:settings"),
        ("🛡 Безопасность", "owner:security"),
        ("🧩 Система", "owner:system"),
        ("⬅️ Назад", "menu:main"),
    ]
    for text, data in rows:
        kb.button(text=text, callback_data=data)
    if settings.WEBAPP_URL:
        kb.adjust(1, 2, 2, 2, 2, 2, 1, 1)
    else:
        kb.adjust(2, 2, 2, 2, 2, 1, 1)
    return kb.as_markup()


def admins_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить", callback_data="owner:add_admin")
    kb.button(text="👥 Список", callback_data="owner:list_admins")
    kb.button(text="📝 Журнал", callback_data="owner:audit")
    kb.button(text="⬅️ Назад", callback_data="owner:menu")
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def admin_card_menu(target_user_id: int, allowed: set[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for perm in DELEGABLE_PERMISSIONS:
        mark = "✅" if perm.code in allowed else "▫️"
        label = f"{mark} {perm.label}"
        kb.button(text=label, callback_data=f"owner:perm:{target_user_id}:{perm.code}")
    kb.button(text="🚫 Убрать доступ", callback_data=f"owner:remove_admin:{target_user_id}")
    kb.button(text="⬅️ Назад", callback_data="owner:list_admins")
    kb.adjust(1)
    return kb.as_markup()


def admins_list_menu(admins) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in admins:
        name = row["full_name"] or row["username"] or str(row["telegram_id"])
        kb.button(text=f"👤 {name}", callback_data=f"owner:admin_card:{row['user_id']}")
    kb.button(text="⬅️ Назад", callback_data="owner:admins")
    kb.adjust(1)
    return kb.as_markup()


def moderation_menu(permissions: set[str]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if settings.WEBAPP_URL and permissions:
        kb.button(text="📱 Панель модерации", web_app=WebAppInfo(url=f"{settings.WEBAPP_URL.rstrip('/')}/control"))
    for code, (text, callback) in MODERATION_BUTTONS.items():
        if code in permissions:
            kb.button(text=text, callback_data=callback)
    kb.button(text="⬅️ Назад", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def author_menu(has_profile: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if has_profile:
        rows = [
            ("📚 Мои произведения", "author:books"),
            ("➕ Добавить книгу", "author:add_book"),
            ("🎧 Аудиокниги", "author:audio"),
            ("💰 Доход", "author:income"),
            ("📢 Продвижение", "author:ads"),
            ("👤 Профиль", "author:profile"),
            ("📜 Правила авторов", "legal:view:authors"),
        ]
    else:
        rows = [("✍️ Стать автором", "author:register")]
    for text, data in rows:
        kb.button(text=text, callback_data=data)
    if has_profile and settings.WEBAPP_URL:
        kb.button(
            text="🖼 Добавить комикс / мангу",
            web_app=WebAppInfo(url=f"{settings.WEBAPP_URL.rstrip('/')}/author?new=graphic"),
        )
    kb.button(text="⬅️ Назад", callback_data="menu:main")
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def _append_navigation(
    kb: InlineKeyboardBuilder,
    *,
    back_callback: str | None = None,
    cancel_callback: str | None = None,
    back_text: str = "⬅️ Назад",
    cancel_text: str = "❌ Отмена",
) -> None:
    """Добавляет только уместные действия выхода из текущего сценария."""
    if back_callback:
        kb.button(text=back_text, callback_data=back_callback)
    if cancel_callback and cancel_callback != back_callback:
        kb.button(text=cancel_text, callback_data=cancel_callback)


def navigation_menu(
    *,
    back_callback: str | None = None,
    cancel_callback: str | None = None,
    back_text: str = "⬅️ Назад",
    cancel_text: str = "❌ Отмена",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    _append_navigation(
        kb,
        back_callback=back_callback,
        cancel_callback=cancel_callback,
        back_text=back_text,
        cancel_text=cancel_text,
    )
    kb.adjust(1)
    return kb.as_markup()


def age_menu(prefix: str, back_callback: str | None = None, cancel_callback: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for value in ["0+", "6+", "12+", "16+", "18+"]:
        kb.button(text=value, callback_data=f"{prefix}:{value}")
    _append_navigation(kb, back_callback=back_callback, cancel_callback=cancel_callback)
    kb.adjust(3, 2, 1, 1)
    return kb.as_markup()


def writing_status_menu(back_callback: str | None = None, cancel_callback: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Пишется", callback_data="book:status:writing")
    kb.button(text="Завершена", callback_data="book:status:finished")
    kb.button(text="Заморожена", callback_data="book:status:frozen")
    _append_navigation(kb, back_callback=back_callback, cancel_callback=cancel_callback)
    kb.adjust(1)
    return kb.as_markup()


def yes_no_menu(prefix: str, back_callback: str | None = None, cancel_callback: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да", callback_data=f"{prefix}:yes")
    kb.button(text="Нет", callback_data=f"{prefix}:no")
    _append_navigation(kb, back_callback=back_callback, cancel_callback=cancel_callback)
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def pricing_menu(back_callback: str | None = None, cancel_callback: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Бесплатная книга", callback_data="book:pricing:free")
    kb.button(text="Платная книга", callback_data="book:pricing:whole_book")
    _append_navigation(kb, back_callback=back_callback, cancel_callback=cancel_callback)
    kb.adjust(1)
    return kb.as_markup()


def cover_menu(back_callback: str | None = None, cancel_callback: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⏭ Пропустить", callback_data="book:cover:skip")
    _append_navigation(kb, back_callback=back_callback, cancel_callback=cancel_callback)
    kb.adjust(1)
    return kb.as_markup()


def book_created_menu(book_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Главы", callback_data=f"author:chapters:{book_id}")
    kb.button(text="🎧 Аудио", callback_data=f"author:audio:{book_id}")
    kb.button(text="📤 На проверку", callback_data=f"author:submit_book:{book_id}")
    kb.button(text="📚 Мои книги", callback_data="author:books")
    kb.button(text="⬅️ В меню", callback_data="author:menu")
    kb.adjust(1)
    return kb.as_markup()


def author_books_menu(books) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for book in books:
        status = {
            "draft": "черновик",
            "review": "на проверке",
            "published": "опубликована",
            "hidden": "скрыта",
            "blocked": "заблокирована",
        }.get(book["publication_status"], book["publication_status"])
        kb.button(text=f"📘 {book['title']} · {status}", callback_data=f"author:book:{book['id']}")
    kb.button(text="➕ Добавить книгу", callback_data="author:add_book")
    kb.button(text="⬅️ Кабинет автора", callback_data="author:menu")
    kb.button(text="🏠 Главное меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def author_book_card_menu(book_id: int, publication_status: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if publication_status == "draft":
        kb.button(text="📤 Отправить на проверку", callback_data=f"author:submit_book:{book_id}")
    if publication_status == "published":
        kb.button(text="📢 Опубликовать в канале", callback_data=f"channel:promote:{book_id}")
    kb.button(text="➕ Главы", callback_data=f"author:chapters:{book_id}")
    kb.button(text="🎧 Аудио", callback_data=f"author:audio:{book_id}")
    kb.button(text="✏️ Название", callback_data=f"book:edit_title:{book_id}")
    kb.button(text="📝 Описание", callback_data=f"book:edit_description:{book_id}")
    kb.button(text="🔞 Возраст", callback_data=f"book:edit_age:{book_id}")
    kb.button(text="📌 Статус", callback_data=f"book:edit_status:{book_id}")
    kb.button(text="📥 Скачивание", callback_data=f"book:edit_download:{book_id}")
    kb.button(text="💰 Цена всей книги", callback_data=f"book:edit_price:{book_id}")
    kb.button(text="🗑 Удалить книгу", callback_data=f"book:delete_ask:{book_id}")
    kb.button(text="⬅️ К моим книгам", callback_data="author:books")
    kb.button(text="🏠 Главное меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def book_delete_confirm_menu(book_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=f"book:delete_confirm:{book_id}")
    kb.button(text="⬅️ Не удалять", callback_data=f"author:book:{book_id}")
    kb.adjust(1)
    return kb.as_markup()


def moderation_books_menu(books) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for book in books:
        kb.button(text=f"📘 {book['title']}", callback_data=f"mod:book:{book['id']}")
    kb.button(text="⬅️ Назад", callback_data="mod:menu")
    kb.adjust(1)
    return kb.as_markup()


def moderation_book_card_menu(book_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Опубликовать", callback_data=f"mod:book_publish:{book_id}")
    kb.button(text="↩️ На доработку", callback_data=f"mod:book_reject:{book_id}")
    kb.button(text="⬅️ К списку", callback_data="mod:books")
    kb.adjust(1)
    return kb.as_markup()


def finance_owner_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💳 Платёжные системы", callback_data="owner:payment_systems")
    kb.button(text="📤 Заявки на выплату", callback_data="owner:payouts")
    kb.button(text="⚙️ Удержания и вывод", callback_data="owner:payout_settings")
    kb.button(text="Комиссия книг", callback_data="owner:set_commission:commission_books")
    kb.button(text="Комиссия аудио", callback_data="owner:set_commission:commission_audio")
    kb.button(text="Комиссия донатов", callback_data="owner:set_commission:commission_donations")
    kb.button(text="↩️ Возвраты", callback_data="owner:refunds")
    kb.button(text="⬅️ Назад", callback_data="owner:menu")
    kb.adjust(1)
    return kb.as_markup()




def payment_systems_owner_menu(config: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"{'✅' if config.get('stars_enabled') else '▫️'} Оплата Stars",
        callback_data="owner:payment_toggle:stars_enabled",
    )
    kb.button(
        text=f"{'✅' if config.get('content_protection_enabled') else '▫️'} Защита содержимого",
        callback_data="owner:payment_toggle:content_protection_enabled",
    )
    kb.button(
        text=f"{'✅' if config.get('watermark_enabled') else '▫️'} Персональный водяной знак",
        callback_data="owner:payment_toggle:watermark_enabled",
    )
    if settings.WEBAPP_URL:
        kb.button(
            text="⚙️ Курсы и расчёты",
            web_app=WebAppInfo(url=f"{settings.WEBAPP_URL.rstrip('/')}/control?section=payments"),
        )
    kb.button(text="⬅️ Финансы", callback_data="owner:finance")
    kb.adjust(1)
    return kb.as_markup()

def reader_ads_owner_menu(settings_dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    enabled = "✅" if settings_dict.get("enabled") else "▫️"
    top = "✅" if settings_dict.get("top") else "▫️"
    bottom = "✅" if settings_dict.get("bottom") else "▫️"
    kb.button(text=f"{enabled} Реклама в читалке", callback_data="owner:reader_ads_toggle:reader_ads_enabled")
    kb.button(text=f"{top} Блок сверху главы", callback_data="owner:reader_ads_toggle:reader_ads_top")
    kb.button(text=f"{bottom} Блок снизу главы", callback_data="owner:reader_ads_toggle:reader_ads_bottom")
    kb.button(text="⬅️ Назад", callback_data="owner:settings")
    kb.adjust(1)
    return kb.as_markup()



def author_profile_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Псевдоним", callback_data="profile:edit:pen_name")
    kb.button(text="📝 Описание", callback_data="profile:edit:bio")
    kb.button(text="🌍 Страна", callback_data="profile:edit:country")
    kb.button(text="🔞 Возраст", callback_data="profile:edit:adult")
    kb.button(text="⬅️ Кабинет автора", callback_data="author:menu")
    kb.adjust(1)
    return kb.as_markup()

def back_to_main() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    return kb.as_markup()


def author_chapters_menu(book_id: int, pricing_mode: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Ввести главу вручную", callback_data=f"chapter:add_manual:{book_id}")
    kb.button(text="📄 Загрузить файл", callback_data=f"chapter:upload:{book_id}")
    kb.button(text="📚 Список глав", callback_data=f"chapter:list:{book_id}")
    if pricing_mode == "whole_book":
        kb.button(text="🔓 Доступ одной / диапазона", callback_data=f"chapter:bulk_price:{book_id}")
    elif pricing_mode == "chapters" or pricing_mode is None:
        kb.button(text="💰 Доступ и цена одной / диапазона", callback_data=f"chapter:bulk_price:{book_id}")
    kb.button(text="📤 На проверку", callback_data=f"author:submit_book:{book_id}")
    kb.button(text="⬅️ К книге", callback_data=f"author:book:{book_id}")
    kb.adjust(1)
    return kb.as_markup()


def chapter_import_confirm_menu(book_id: int, duplicate_warning: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    label = "⚠️ Проверить и сохранить" if duplicate_warning else "✅ Сохранить главы"
    kb.button(text=label, callback_data=f"chapter:import_confirm:{book_id}")
    kb.button(text="❌ Отмена", callback_data=f"chapter:import_cancel:{book_id}")
    kb.adjust(1)
    return kb.as_markup()


def author_chapter_list_menu(book_id: int, chapters, pricing_mode: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for chapter in chapters[:40]:
        if pricing_mode == "free" or int(chapter["is_free"] or 0) == 1:
            access_mark = "бесплатно"
        elif pricing_mode == "chapters" and int(chapter["price_stars"] or 0) > 0:
            access_mark = f"{int(chapter['price_stars'])} Stars"
        else:
            access_mark = "после покупки книги"
        kb.button(text=f"{chapter['number']}. {chapter['title']} · {access_mark}", callback_data=f"chapter:view:{chapter['id']}")
    kb.button(text="➕ Добавить", callback_data=f"chapter:add_manual:{book_id}")
    kb.button(text="📄 Загрузить файл", callback_data=f"chapter:upload:{book_id}")
    kb.button(text="⬅️ Назад", callback_data=f"author:chapters:{book_id}")
    kb.adjust(1)
    return kb.as_markup()


def chapter_view_menu(book_id: int, chapter_id: int, pricing_mode: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Название", callback_data=f"chapter:edit_title:{chapter_id}")
    kb.button(text="📝 Текст", callback_data=f"chapter:edit_text:{chapter_id}")
    if pricing_mode != "free":
        kb.button(text="🔐 Доступ этой главы", callback_data=f"chapter:edit_price:{chapter_id}")
    kb.button(text="🗑 Удалить главу", callback_data=f"chapter:delete_ask:{chapter_id}")
    kb.button(text="⬅️ К главам", callback_data=f"chapter:list:{book_id}")
    kb.adjust(1)
    return kb.as_markup()


def chapter_delete_confirm_menu(book_id: int, chapter_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Да, удалить", callback_data=f"chapter:delete:{chapter_id}")
    kb.button(text="⬅️ Не удалять", callback_data=f"chapter:view:{chapter_id}")
    kb.button(text="📚 К главам", callback_data=f"chapter:list:{book_id}")
    kb.adjust(1)
    return kb.as_markup()


def author_audio_menu(book_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить аудиоглаву", callback_data=f"audio:add:{book_id}")
    kb.button(text="📦 Загрузить ZIP", callback_data=f"audio:zip:{book_id}")
    kb.button(text="🎧 Список аудио", callback_data=f"audio:list:{book_id}")
    kb.button(text="⬅️ К книге", callback_data=f"author:book:{book_id}")
    kb.adjust(1)
    return kb.as_markup()


def author_audio_list_menu(book_id: int, audios) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for audio in audios[:40]:
        free_mark = "бесплатно" if audio["is_free"] else f"{audio['price_stars']} Stars"
        duration = audio["duration_seconds"] or 0
        minutes = duration // 60
        kb.button(text=f"{audio['number']}. {audio['title']} · {minutes} мин · {free_mark}", callback_data=f"audio:view:{audio['id']}")
    kb.button(text="➕ Добавить", callback_data=f"audio:add:{book_id}")
    kb.button(text="📦 ZIP", callback_data=f"audio:zip:{book_id}")
    kb.button(text="⬅️ Назад", callback_data=f"author:audio:{book_id}")
    kb.adjust(1)
    return kb.as_markup()


def audio_view_menu(book_id: int, audio_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Удалить аудио", callback_data=f"audio:delete:{audio_id}")
    kb.button(text="⬅️ К аудио", callback_data=f"audio:list:{book_id}")
    kb.adjust(1)
    return kb.as_markup()


def payment_invoice_menu(token: str, amount_stars: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"💫 Оплатить {int(amount_stars)} Stars", pay=True)
    kb.button(text="❌ Отменить покупку", callback_data=f"payment:cancel:{str(token)}")
    kb.adjust(1)
    return kb.as_markup()


def purchase_cancel_confirm_menu(purchase_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, отменить и вернуть Stars", callback_data=f"purchase:cancel_confirm:{int(purchase_id)}")
    kb.button(text="Нет, оставить покупку", callback_data=f"purchase:view:{int(purchase_id)}")
    kb.adjust(1)
    return kb.as_markup()


def pay_target_menu(kind: str, target_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💫 Купить за Stars", callback_data=f"buy:{kind}:{target_id}")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def user_purchases_menu(purchases) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in purchases[:20]:
        if "graphic_volume_number" in row.keys() and row["graphic_volume_number"]:
            title = f"Том {row['graphic_volume_number']}: {row['graphic_volume_title'] or row['book_title'] or 'произведение'}"
        elif "graphic_chapter_title" in row.keys() and row["graphic_chapter_title"]:
            title = f"Графика: {row['graphic_chapter_title']}"
        elif row["chapter_title"]:
            title = f"Глава: {row['chapter_title']}"
        elif row["audio_title"]:
            title = f"Аудио: {row['audio_title']}"
        elif row["book_title"]:
            title = f"Книга: {row['book_title']}"
        else:
            title = "Покупка"
        status = "возврат" if row["status"] == "refunded" else "отменяется" if row["status"] == "canceling" else "оплачено"
        kb.button(text=f"{title[:35]} · {row['amount_stars']} ⭐ · {status}", callback_data=f"purchase:view:{row['id']}")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def purchase_card_menu(purchase_id: int, status: str, can_cancel: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if status == "paid":
        if can_cancel:
            kb.button(text="❌ Отменить неиспользованную покупку", callback_data=f"purchase:cancel:{purchase_id}")
        kb.button(text="↩️ Запросить возврат", callback_data=f"refund:request:{purchase_id}")
    kb.button(text="⭐ Мои покупки", callback_data="main:my")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def refund_requests_menu(refunds) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in refunds[:30]:
        buyer = row["username"] or row["full_name"] or row["telegram_id"]
        title = row["book_title"] or row["chapter_title"] or row["audio_title"] or (row["graphic_chapter_title"] if "graphic_chapter_title" in row.keys() else None) or "Покупка"
        kb.button(text=f"#{row['id']} · {title[:28]} · {buyer}", callback_data=f"refund:card:{row['id']}")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def refund_card_menu(refund_id: int, can_process: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if can_process:
        kb.button(text="✅ Одобрить возврат", callback_data=f"refund:approve:{refund_id}")
        kb.button(text="⛔ Отклонить", callback_data=f"refund:reject:{refund_id}")
    kb.button(text="↩️ К возвратам", callback_data="refund:list")
    kb.adjust(1)
    return kb.as_markup()


def access_granted_menu(kind: str, target_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if kind == "chapter":
        kb.button(text="📖 Читать главу", callback_data=f"read:chapter:{target_id}")
    elif kind == "audio":
        kb.button(text="🎧 Получить аудио", callback_data=f"listen:audio:{target_id}")
    elif kind == "book":
        kb.button(text="📚 Открыть книгу", callback_data=f"open:book:{target_id}")
    elif kind == "chapter_package" and settings.WEBAPP_URL:
        kb.button(text="📚 Выбрать главы", web_app=WebAppInfo(url=f"{settings.WEBAPP_URL.rstrip('/')}/book/{int(target_id)}"))
    elif kind == "graphic" and settings.WEBAPP_URL:
        kb.button(text="🖼 Открыть графическую главу", web_app=WebAppInfo(url=f"{settings.WEBAPP_URL.rstrip('/')}/comic/{int(target_id)}"))
    kb.button(text="⭐ Мои покупки", callback_data="main:my")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def single_select_menu(
    prefix: str,
    choices,
    back_callback: str | None = None,
    cancel_callback: str | None = None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for choice in choices:
        kb.button(text=choice.label, callback_data=f"single:{prefix}:{choice.code}")
    _append_navigation(kb, back_callback=back_callback, cancel_callback=cancel_callback)
    kb.adjust(1)
    return kb.as_markup()


def multi_select_menu(prefix: str, choices, selected: set[str] | list[str] | tuple[str, ...], page: int = 0,
                      per_page: int = 12, back_callback: str | None = None,
                      cancel_callback: str | None = None) -> InlineKeyboardMarkup:
    selected_set = set(selected or [])
    total_pages = max(1, (len(choices) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    end = start + per_page
    kb = InlineKeyboardBuilder()
    for choice in choices[start:end]:
        mark = "✅" if choice.code in selected_set else "▫️"
        kb.button(text=f"{mark} {choice.label}", callback_data=f"sel:{prefix}:t:{choice.code}")
    if total_pages > 1:
        if page > 0:
            kb.button(text="⬅️ Назад", callback_data=f"sel:{prefix}:p:{page-1}")
        kb.button(text=f"{page + 1}/{total_pages}", callback_data=f"sel:{prefix}:noop")
        if page < total_pages - 1:
            kb.button(text="Вперёд ➡️", callback_data=f"sel:{prefix}:p:{page+1}")
    kb.button(text="✅ Готово", callback_data=f"sel:{prefix}:d")
    _append_navigation(kb, back_callback=back_callback, cancel_callback=cancel_callback)
    kb.adjust(1)
    return kb.as_markup()


def bonuses_menu(can_claim: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if can_claim:
        kb.button(text="🎁 Получить ежедневный бонус", callback_data="bonus:daily")
    kb.button(text="👥 Пригласить друга", callback_data="bonus:referral")
    kb.button(text="📜 История бонусов", callback_data="bonus:history")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def author_ads_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Создать рекламу", callback_data="ad:create")
    kb.button(text="📢 Мои кампании", callback_data="ad:list")
    kb.button(text="🎟 Промокоды", callback_data="promo:list")
    kb.button(text="➕ Создать промокод", callback_data="promo:create")
    kb.button(text="⬅️ Назад", callback_data="author:menu")
    kb.adjust(1)
    return kb.as_markup()


def author_books_pick_menu(books, prefix: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for book in books[:40]:
        status = book["publication_status"]
        kb.button(text=f"📘 {book['title']} · {status}", callback_data=f"{prefix}:{book['id']}")
    kb.button(text="⬅️ Назад", callback_data="author:ads")
    kb.adjust(1)
    return kb.as_markup()


def ad_campaigns_menu(campaigns) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in campaigns[:30]:
        left = max(0, int(row["budget_units"] or 0) - int(row["spent_units"] or 0))
        kb.button(text=f"📢 {row['book_title'][:24]} · {row['status']} · остаток {left}", callback_data=f"ad:card:{row['id']}")
    kb.button(text="➕ Создать рекламу", callback_data="ad:create")
    kb.button(text="⬅️ Назад", callback_data="author:ads")
    kb.adjust(1)
    return kb.as_markup()


def promo_codes_menu(codes) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in codes[:30]:
        kb.button(text=f"🎟 {row['code']} · {row['discount_percent']}% · {row['used_count']}/{row['max_uses']}", callback_data=f"promo:card:{row['id']}")
    kb.button(text="➕ Создать промокод", callback_data="promo:create")
    kb.button(text="⬅️ Назад", callback_data="author:ads")
    kb.adjust(1)
    return kb.as_markup()


def promo_code_card_menu(promo_id: int, status: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    label = "⏸ Приостановить" if status == "active" else "▶️ Возобновить"
    kb.button(text=label, callback_data=f"promo:toggle:{promo_id}")
    kb.button(text="⬅️ К промокодам", callback_data="promo:list")
    kb.adjust(1)
    return kb.as_markup()


def moderation_content_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💬 Комментарии", callback_data="mod:content:comments")
    kb.button(text="⭐ Отзывы", callback_data="mod:content:reviews")
    kb.button(text="⬅️ Назад", callback_data="mod:menu")
    kb.adjust(1)
    return kb.as_markup()


def moderation_comments_menu(comments) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in comments[:30]:
        who = row["username"] or row["full_name"] or "читатель"
        reports = int(row["report_count"] or 0) if "report_count" in row.keys() else 0
        spoiler = " ⚠️" if ("is_spoiler" in row.keys() and int(row["is_spoiler"] or 0)) else ""
        report_mark = f" · жалоб {reports}" if reports else ""
        kb.button(
            text=f"💬 #{row['id']}{spoiler} · {who}{report_mark} · {row['book_title'][:18]}",
            callback_data=f"mod:comment:{row['id']}",
        )
    kb.button(text="⬅️ Назад", callback_data="mod:comments")
    kb.adjust(1)
    return kb.as_markup()


def moderation_reviews_menu(reviews) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in reviews[:30]:
        who = row["username"] or row["full_name"] or "читатель"
        kb.button(text=f"⭐ #{row['id']} · {row['rating']}★ · {who}", callback_data=f"mod:review:{row['id']}")
    kb.button(text="⬅️ Назад", callback_data="mod:comments")
    kb.adjust(1)
    return kb.as_markup()


def moderation_hide_menu(kind: str, item_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🫥 Скрыть", callback_data=f"mod:{kind}_hide:{item_id}")
    kb.button(text="⬅️ Назад", callback_data="mod:comments")
    kb.adjust(1)
    return kb.as_markup()


def moderation_ads_menu(campaigns) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in campaigns[:30]:
        left = max(0, int(row["budget_units"] or 0) - int(row["spent_units"] or 0))
        kb.button(text=f"📢 #{row['id']} · {row['book_title'][:24]} · остаток {left}", callback_data=f"mod:ad:{row['id']}")
    kb.button(text="⬅️ Назад", callback_data="mod:menu")
    kb.adjust(1)
    return kb.as_markup()


def ad_moderation_card_menu(campaign_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⏸ Остановить", callback_data=f"mod:ad_pause:{campaign_id}")
    kb.button(text="🚫 Заблокировать", callback_data=f"mod:ad_block:{campaign_id}")
    kb.button(text="⬅️ К рекламе", callback_data="mod:ads")
    kb.adjust(1)
    return kb.as_markup()


def ad_campaign_card_menu(campaign_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Пополнить на 10 Stars", callback_data=f"adbudget:pay:{campaign_id}:10")
    kb.button(text="➕ Пополнить на 50 Stars", callback_data=f"adbudget:pay:{campaign_id}:50")
    kb.button(text="➕ Пополнить на 100 Stars", callback_data=f"adbudget:pay:{campaign_id}:100")
    kb.button(text="📊 Отчёт", callback_data=f"ad:report:{campaign_id}")
    kb.button(text="⬅️ К кампаниям", callback_data="ad:list")
    kb.adjust(1)
    return kb.as_markup()


def promo_apply_menu(kind: str, target_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💫 Купить за Stars", callback_data=f"buy:{kind}:{target_id}")
    kb.button(text="🎟 Ввести промокод", callback_data=f"promo:apply:{kind}:{target_id}")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def owner_search_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Найти пользователя/автора", callback_data="owner:search_user")
    kb.button(text="📚 Найти книгу", callback_data="owner:search_book")
    kb.button(text="⬅️ Назад", callback_data="owner:menu")
    kb.adjust(1)
    return kb.as_markup()


def owner_users_search_results_menu(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in rows[:20]:
        name = row["pen_name"] or row["full_name"] or row["username"] or str(row["telegram_id"])
        blocked = "🚫" if row["is_blocked"] else "👤"
        kb.button(text=f"{blocked} {name}", callback_data=f"owner:user_card:{row['id']}")
    kb.button(text="⬅️ Назад", callback_data="owner:users")
    kb.adjust(1)
    return kb.as_markup()


def owner_user_card_menu(user_id: int, is_blocked: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Разблокировать" if is_blocked else "🚫 Заблокировать", callback_data=f"owner:user_block:{user_id}:{0 if is_blocked else 1}")
    kb.button(text="⬅️ Поиск", callback_data="owner:users")
    kb.adjust(1)
    return kb.as_markup()


def owner_books_search_results_menu(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in rows[:20]:
        status = row["publication_status"]
        kb.button(text=f"📘 {row['title'][:32]} · {status}", callback_data=f"owner:book_card:{row['id']}")
    kb.button(text="⬅️ Назад", callback_data="owner:books")
    kb.adjust(1)
    return kb.as_markup()


def owner_book_card_menu(book_id: int, status: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if status == "published":
        kb.button(text="📢 Опубликовать в канале повторно", callback_data=f"owner:channel_repost:{book_id}")
    if status == "blocked":
        kb.button(text="👁 Скрыть вместо блокировки", callback_data=f"owner:book_block:{book_id}:0")
    else:
        kb.button(text="🚫 Заблокировать книгу", callback_data=f"owner:book_block:{book_id}:1")
    kb.button(text="⬅️ Поиск", callback_data="owner:books")
    kb.adjust(1)
    return kb.as_markup()


def owner_channel_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📚 Найти книгу", callback_data="owner:books")
    kb.button(text="💰 Цена продвижения", callback_data="owner:channel_price")
    kb.button(text="⬅️ Назад", callback_data="owner:menu")
    kb.adjust(1)
    return kb.as_markup()


def channel_promotion_confirm_menu(book_id: int, price: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"⭐ Оплатить {int(price)} Stars", callback_data=f"channel:promote_pay:{int(book_id)}")
    kb.button(text="⬅️ К книге", callback_data=f"author:book:{int(book_id)}")
    kb.adjust(1)
    return kb.as_markup()


def complaints_menu(rows, prefix: str = "complaint") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in rows[:30]:
        who = row["username"] or row["full_name"] or row["telegram_id"] or "неизвестно"
        kb.button(text=f"🧾 #{row['id']} · {row['target_type']} · {who}", callback_data=f"{prefix}:card:{row['id']}")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def complaint_card_menu(
    complaint_id: int,
    prefix: str = "complaint",
    *,
    target_type: str = "",
    target_id: str = "",
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if target_type == "comment" and str(target_id).isdigit():
        kb.button(text="💬 Открыть комментарий", callback_data=f"mod:comment:{int(target_id)}")
    kb.button(text="✅ Закрыть", callback_data=f"{prefix}:close:{complaint_id}")
    kb.button(text="⏳ Оставить в работе", callback_data=f"{prefix}:pending:{complaint_id}")
    kb.button(text="⬅️ К жалобам", callback_data=f"{prefix}:list")
    kb.adjust(1)
    return kb.as_markup()



def legal_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Оферта для читателей", callback_data="legal:view:terms")
    kb.button(text="🔐 Политика данных", callback_data="legal:view:privacy")
    kb.button(text="✅ Согласие на данные", callback_data="legal:view:personal_data_consent")
    kb.button(text="↩️ Возвраты", callback_data="legal:view:refunds")
    kb.button(text="✍️ Договор автора", callback_data="legal:view:author_license")
    kb.button(text="🔏 Данные автора", callback_data="legal:view:author_data_consent")
    kb.button(text="💳 Комиссии и выплаты", callback_data="legal:view:fees_payouts")
    kb.button(text="©️ Авторские права", callback_data="legal:view:copyright")
    kb.button(text="🛡 Контент и модерация", callback_data="legal:view:content")
    kb.button(text="🧾 Мои согласия", callback_data="legal:my_acceptances")
    kb.button(text="⬅️ Назад", callback_data="main:more")
    kb.adjust(1)
    return kb.as_markup()


def legal_doc_menu(code: str, consent_kind: str = "agreement", *, required: bool = False) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if consent_kind == "consent":
        accept_text = "✅ Даю отдельное согласие"
    elif consent_kind == "agreement":
        accept_text = "✅ Принимаю условия"
    else:
        accept_text = "✅ Ознакомился"
    kb.button(text=accept_text, callback_data=f"legal:accept:{code}")
    if consent_kind in {"consent", "agreement"}:
        kb.button(text="Не принимаю", callback_data=f"legal:decline:{code}")
    if not required:
        kb.button(text="📜 Другие документы", callback_data="main:legal")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    return kb.as_markup()


def author_income_menu(available: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📤 Запросить выплату", callback_data="author:payout_request")
    kb.button(text="🏦 Реквизиты", callback_data="author:payout_method")
    kb.button(text="🧾 История выплат", callback_data="author:payout_history")
    kb.button(text="⬅️ Назад", callback_data="author:menu")
    kb.adjust(1)
    return kb.as_markup()


def payout_requests_menu(rows) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for row in rows:
        name = row["pen_name"] or row["username"] or str(row["telegram_id"])
        kb.button(text=f"📤 {name} · {row['amount_stars']} Stars", callback_data=f"payout:card:{row['id']}")
    kb.button(text="✅ Одобренные", callback_data="owner:payouts:approved")
    kb.button(text="⬅️ Финансы", callback_data="owner:finance")
    kb.adjust(1)
    return kb.as_markup()


def payout_card_menu(payout_id: int, status: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if status == "new":
        kb.button(text="✅ Одобрить", callback_data=f"payout:approve:{payout_id}")
        kb.button(text="🚫 Отклонить", callback_data=f"payout:reject:{payout_id}")
        kb.button(text="🧊 Заморозить", callback_data=f"payout:freeze:{payout_id}")
    elif status == "approved":
        kb.button(text="✅ Отметить выплачено", callback_data=f"payout:paid:{payout_id}")
        kb.button(text="🚫 Отклонить", callback_data=f"payout:reject:{payout_id}")
        kb.button(text="🧊 Заморозить", callback_data=f"payout:freeze:{payout_id}")
    kb.button(text="⬅️ К выплатам", callback_data="owner:payouts")
    kb.adjust(1)
    return kb.as_markup()


def payout_settings_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Минимальная сумма", callback_data="owner:set_payout:payout_min_stars")
    kb.button(text="Срок удержания", callback_data="owner:set_payout:hold_days_default")
    kb.button(text="Резерв на споры", callback_data="owner:set_payout:reserve_percent")
    kb.button(text="⬅️ Финансы", callback_data="owner:finance")
    kb.adjust(1)
    return kb.as_markup()


def skip_back_menu(
    skip_callback: str,
    back_callback: str | None = None,
    skip_text: str = "⏭ Пропустить",
    cancel_callback: str | None = None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=skip_text, callback_data=skip_callback)
    _append_navigation(kb, back_callback=back_callback, cancel_callback=cancel_callback)
    kb.adjust(1)
    return kb.as_markup()


def skip_use_menu(
    skip_callback: str,
    use_callback: str | None = None,
    use_text: str = "✅ Использовать рекомендованное",
    back_callback: str | None = None,
    cancel_callback: str | None = None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if use_callback:
        kb.button(text=use_text, callback_data=use_callback)
    kb.button(text="⏭ Пропустить", callback_data=skip_callback)
    _append_navigation(kb, back_callback=back_callback, cancel_callback=cancel_callback)
    kb.adjust(1)
    return kb.as_markup()


def user_settings_menu(preferences: dict | None = None) -> InlineKeyboardMarkup:
    preferences = preferences or {}
    theme = preferences.get("theme", "system")
    font = preferences.get("font_size", "normal")
    notify = str(preferences.get("notifications", "1")) != "0"
    theme_label = {"system": "как в Telegram", "dark": "тёмная", "light": "светлая"}.get(theme, theme)
    font_label = {"small": "мелкий", "normal": "обычный", "large": "крупный"}.get(font, font)
    kb = InlineKeyboardBuilder()
    kb.button(text=f"🎨 Тема: {theme_label}", callback_data="settings:theme")
    kb.button(text=f"🔠 Шрифт: {font_label}", callback_data="settings:font")
    kb.button(text=f"🔔 Уведомления: {'включены' if notify else 'выключены'}", callback_data="settings:notifications")
    kb.button(text="🧹 Очистить настройки", callback_data="settings:reset")
    kb.button(text="⬅️ Назад", callback_data="main:more")
    kb.adjust(1)
    return kb.as_markup()


def user_notifications_menu(preferences: dict | None = None) -> InlineKeyboardMarkup:
    preferences = preferences or {}
    items = [
        ("notifications", "Все уведомления"),
        ("notifications_chapters", "Новые главы"),
        ("notifications_audio", "Новые аудиоглавы"),
        ("notifications_discounts", "Скидки и промокоды"),
        ("notifications_reminders", "Продолжить чтение"),
        ("notifications_achievements", "Достижения"),
    ]
    kb = InlineKeyboardBuilder()
    for key, label in items:
        enabled = str(preferences.get(key, "1")) != "0"
        kb.button(
            text=f"{'✅' if enabled else '▫️'} {label}",
            callback_data=f"settings:toggle_notification:{key}",
        )
    kb.button(text="⬅️ Настройки", callback_data="main:settings")
    kb.adjust(1)
    return kb.as_markup()


def user_theme_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Как в Telegram", callback_data="settings:set_theme:system")
    kb.button(text="Тёмная", callback_data="settings:set_theme:dark")
    kb.button(text="Светлая", callback_data="settings:set_theme:light")
    kb.button(text="⬅️ Назад", callback_data="main:settings")
    kb.adjust(1)
    return kb.as_markup()


def user_font_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Мелкий", callback_data="settings:set_font:small")
    kb.button(text="Обычный", callback_data="settings:set_font:normal")
    kb.button(text="Крупный", callback_data="settings:set_font:large")
    kb.button(text="⬅️ Назад", callback_data="main:settings")
    kb.adjust(1)
    return kb.as_markup()
