from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    code: str
    label: str
    owner_only: bool = False


PERMISSIONS = [
    Permission("mod_books", "📚 Модерация книг"),
    Permission("mod_comments", "💬 Модерация комментариев"),
    Permission("complaints", "🧾 Работа с жалобами"),
    Permission("authors", "✍️ Работа с авторами"),
    Permission("block_users", "👤 Блокировка пользователей"),
    Permission("block_books", "📕 Блокировка книг"),
    Permission("view_finance", "💰 Просмотр финансов"),
    Permission("refunds", "↩️ Возвраты"),
    Permission("ads", "📢 Реклама"),
    Permission("channel", "📣 Канал"),
    Permission("stats", "📊 Статистика"),
    Permission("support", "🛟 Техподдержка"),
    Permission("change_commission", "⚠️ Изменение комиссии", owner_only=True),
    Permission("payouts", "⚠️ Выплаты авторам", owner_only=True),
    Permission("delete_content", "⚠️ Полное удаление контента", owner_only=True),
    Permission("manage_admins", "⚠️ Назначение администрации", owner_only=True),
    Permission("platform_settings", "⚠️ Настройки платформы", owner_only=True),
]

PERMISSION_BY_CODE = {p.code: p for p in PERMISSIONS}

MODERATION_BUTTONS = {
    "mod_books": ("📚 Книги на проверке", "mod:books"),
    "mod_comments": ("💬 Комментарии", "mod:comments"),
    "complaints": ("🧾 Жалобы", "mod:complaints"),
    "authors": ("✍️ Авторы", "mod:authors"),
    "block_users": ("👤 Пользователи", "mod:users"),
    "block_books": ("📕 Заблокировать книгу", "mod:block_books"),
    "view_finance": ("💰 Финансы", "mod:finance"),
    "refunds": ("↩️ Возвраты", "mod:refunds"),
    "ads": ("📢 Реклама", "mod:ads"),
    "channel": ("📣 Канал", "mod:channel"),
    "stats": ("📊 Статистика", "mod:stats"),
    "support": ("🛟 Поддержка", "mod:support"),
}
