from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.db import (
    accept_legal_document,
    get_admin_permissions,
    get_author_profile,
    get_legal_acceptances,
    get_missing_legal_documents,
    upsert_user,
)
from app.keyboards import legal_menu, legal_doc_menu, main_menu
from app.legal_texts import (
    AUTHOR_INFORMATION_DOCS,
    LEGAL_DOCS,
    REQUIRED_FOR_AUTHOR,
    REQUIRED_ON_START,
    get_doc,
)
from app.services.legal_documents import ensure_legal_pdf

router = Router()


def _required_payload(codes: list[str]) -> list[tuple[str, str, str]]:
    return [(code, LEGAL_DOCS[code].version, LEGAL_DOCS[code].digest) for code in codes]


async def missing_legal_codes(user_id: int, codes: list[str]) -> list[str]:
    return await get_missing_legal_documents(user_id, _required_payload(codes))


def _legal_menu_text() -> str:
    return (
        "<b>📜 Документы Вокслиры</b>\n\n"
        "Каждый документ доступен отдельным PDF-файлом. Оферта и согласия принимаются раздельно: "
        "получение файла само по себе не означает принятия. Версия и контрольная сумма сохраняются в журнале."
    )


def _caption(code: str, *, required: bool = False) -> str:
    doc = get_doc(code)
    if not doc:
        return "Документ не найден."
    action = {
        "agreement": "После ознакомления нажмите «Принимаю условия».",
        "consent": "Согласие оформляется отдельно. После ознакомления нажмите «Даю отдельное согласие».",
        "information": "После ознакомления можно подтвердить получение документа.",
    }.get(doc.consent_kind, "Ознакомьтесь с документом.")
    prefix = "<b>Обязательный шаг</b>\n\n" if required else ""
    return (
        f"{prefix}<b>{doc.title}</b>\n"
        f"Редакция: <b>{doc.version}</b>\n"
        f"Контрольная сумма: <code>{doc.digest[:20]}…</code>\n\n{action}"
    )


async def send_legal_document(message: Message, code: str, *, required: bool = False) -> None:
    doc = get_doc(code)
    if not doc:
        await message.answer("Документ не найден.")
        return
    path = ensure_legal_pdf(code)
    await message.answer_document(
        document=FSInputFile(path, filename=doc.filename),
        caption=_caption(code, required=required),
        reply_markup=legal_doc_menu(code, doc.consent_kind),
        protect_content=False,
    )


async def send_next_required_document(message: Message, user_id: int, *, author: bool = False) -> bool:
    codes = REQUIRED_FOR_AUTHOR if author else REQUIRED_ON_START
    missing = await missing_legal_codes(user_id, codes)
    if not missing:
        return False
    await send_legal_document(message, missing[0], required=True)
    return True


async def _completion_markup(author: bool = False):
    kb = InlineKeyboardBuilder()
    if author:
        kb.button(text="✍️ Продолжить регистрацию", callback_data="author:register")
    else:
        kb.button(text="🏠 Открыть Вокслиру", callback_data="menu:main")
    kb.button(text="📜 Все документы", callback_data="main:legal")
    kb.adjust(1)
    return kb.as_markup()


@router.callback_query(F.data == "main:legal")
async def legal_menu_handler(call: CallbackQuery) -> None:
    await call.message.edit_text(_legal_menu_text(), reply_markup=legal_menu())
    await call.answer()


@router.callback_query(F.data.startswith("legal:view:"))
async def legal_view(call: CallbackQuery) -> None:
    code = call.data.split(":")[-1]
    doc = get_doc(code)
    if not doc:
        await call.answer("Документ не найден", show_alert=True)
        return
    await send_legal_document(call.message, code)
    await call.answer("PDF отправлен")


@router.callback_query(F.data.startswith("legal:accept:"))
async def legal_accept(call: CallbackQuery) -> None:
    code = call.data.split(":")[-1]
    doc = get_doc(code)
    if not doc:
        await call.answer("Документ не найден", show_alert=True)
        return
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await accept_legal_document(
        user["id"],
        doc.code,
        doc.version,
        doc_hash=doc.digest,
        source="telegram_bot",
        telegram_message_id=call.message.message_id,
    )
    await call.answer("Принятие сохранено")

    if doc.code in REQUIRED_ON_START:
        if await send_next_required_document(call.message, int(user["id"])):
            await call.message.answer("Первый документ принят. Остался ещё один отдельный шаг.")
            return
        perms = await get_admin_permissions(int(user["id"]))
        author_profile = await get_author_profile(int(user["id"]))
        await call.message.answer(
            "<b>Готово.</b> Оферта и отдельное согласие на обработку данных сохранены.\n\n"
            "Теперь можно пользоваться Вокслирой.",
            reply_markup=main_menu(
                call.from_user.id in settings.owner_ids,
                bool(perms),
                bool(author_profile),
            ),
        )
        return

    if doc.code in REQUIRED_FOR_AUTHOR:
        if await send_next_required_document(call.message, int(user["id"]), author=True):
            await call.message.answer("Документ автора принят. Остался ещё один отдельный шаг.")
            return
        await call.message.answer(
            "<b>Документы автора приняты.</b>\n\n"
            "Лицензионный договор и отдельное согласие на обработку данных сохранены. "
            "Можно продолжить создание профиля автора.",
            reply_markup=await _completion_markup(author=True),
        )
        return

    await call.message.answer(
        f"Сохранено: <b>{doc.title}</b>\nРедакция: <b>{doc.version}</b>",
        reply_markup=legal_menu(),
    )


@router.callback_query(F.data.startswith("legal:decline:"))
async def legal_decline(call: CallbackQuery) -> None:
    code = call.data.split(":")[-1]
    doc = get_doc(code)
    if not doc:
        await call.answer("Документ не найден", show_alert=True)
        return
    if doc.code in REQUIRED_ON_START:
        text = (
            "Без принятия оферты и отдельного согласия на обработку данных мы не сможем создать рабочий профиль, "
            "сохранить библиотеку и проводить покупки. Документ можно открыть снова в любое время."
        )
    else:
        text = (
            "Без договора автора и отдельного согласия на обработку платёжных данных монетизация и выплаты недоступны. "
            "Читать произведения как обычный пользователь можно после принятия базовых документов."
        )
    await call.message.answer(text, reply_markup=legal_doc_menu(code, doc.consent_kind))
    await call.answer("Выбор не сохранён")


# Совместимость со старыми кнопками: не принимаем несколько документов одним нажатием.
@router.callback_query(F.data.in_({"legal:accept_required", "legal:accept_author"}))
async def legacy_accept_sequence(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    author = call.data.endswith("author")
    if not await send_next_required_document(call.message, int(user["id"]), author=author):
        await call.message.answer("Все необходимые документы этой группы уже приняты.", reply_markup=legal_menu())
    await call.answer()


@router.callback_query(F.data == "legal:my_acceptances")
async def legal_my_acceptances(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    rows = await get_legal_acceptances(user["id"])
    active = [row for row in rows if not row["withdrawn_at"]]
    if not active:
        text = "Активных согласий и акцептов пока нет."
    else:
        lines = ["<b>Мои документы</b>\n"]
        for row in active:
            doc = get_doc(row["doc_code"])
            title = doc.short_title if doc else row["doc_code"]
            verified = " · проверено" if row["doc_hash"] else ""
            lines.append(f"• {title} · {row['doc_version']} · {row['accepted_at'][:10]}{verified}")
        lines.append("\nОтозвать отдельное согласие можно через поддержку. Акцепт исполненного договора не удаляется задним числом.")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=legal_menu())
    await call.answer()


async def _send_command_doc(message: Message, code: str) -> None:
    await send_legal_document(message, code)


@router.message(Command("terms"))
async def cmd_terms(message: Message) -> None:
    await _send_command_doc(message, "terms")


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    await _send_command_doc(message, "privacy")


@router.message(Command("refunds"))
async def cmd_refunds(message: Message) -> None:
    await _send_command_doc(message, "refunds")


@router.message(Command("copyright"))
async def cmd_copyright(message: Message) -> None:
    await _send_command_doc(message, "copyright")


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    contact = settings.LEGAL_SUPPORT_CONTACT.strip() or "поддержка внутри Вокслиры"
    await message.answer(
        "<b>🛟 Поддержка</b>\n\n"
        "Опишите проблему одним сообщением. Для оплаты укажите произведение, главу, дату, способ оплаты и примерную сумму. "
        "Для обращения о правах укажите материал и подтверждающие документы.\n\n"
        f"Канал обращений: <b>{contact}</b>."
    )
