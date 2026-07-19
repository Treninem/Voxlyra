from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    code: str
    label: str
    owner_only: bool = False


PERMISSIONS = [
    Permission("mod_books", "📚 Модерация книг"),
    Permission("library_import_manage", "⚙️ Управление импортом библиотеки"),
    Permission("library_bulk_import", "📥 Массовый импорт книг"),
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
    Permission("grant_access", "🎟 Выдача доступа и Premium"),
    Permission("change_commission", "⚠️ Изменение комиссии", owner_only=True),
    Permission("payouts", "⚠️ Выплаты авторам", owner_only=True),
    Permission("delete_content", "⚠️ Полное удаление контента", owner_only=True),
    Permission("manage_admins", "⚠️ Назначение администрации", owner_only=True),
    Permission("platform_settings", "⚠️ Настройки платформы", owner_only=True),
]

PERMISSION_BY_CODE = {p.code: p for p in PERMISSIONS}
DELEGABLE_PERMISSIONS = [permission for permission in PERMISSIONS if not permission.owner_only]
DELEGABLE_PERMISSION_CODES = {permission.code for permission in DELEGABLE_PERMISSIONS}

MODERATION_BUTTONS = {
    # Показываем только те разделы, где уже есть рабочая логика.
    "mod_books": ("📚 Книги на проверке", "mod:books"),
    "library_import_manage": ("📚 Управление библиотекой", "library:menu"),
    "library_bulk_import": ("📥 Массовый импорт книг", "library:import"),
    "mod_comments": ("💬 Комментарии и отзывы", "mod:comments"),
    "complaints": ("🧾 Жалобы", "mod:complaints"),
    "refunds": ("↩️ Возвраты", "mod:refunds"),
    "ads": ("📢 Реклама", "mod:ads"),
}
