from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.db import accept_legal_document, get_legal_acceptances, upsert_user
from app.keyboards import back_to_main, legal_menu, legal_doc_menu
from app.legal_texts import LEGAL_DOCS, REQUIRED_FOR_AUTHOR, REQUIRED_ON_START, get_doc

router = Router()


def _legal_menu_text() -> str:
    return (
        "<b>📜 Правила Вокслиры</b>\n\n"
        "Здесь собраны условия, возвраты, авторские права, персональные данные и правила авторов. "
        "Перед реальным запуском эти тексты нужно отдать юристу под вашу страну, реквизиты и модель выплат."
    )


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
    text = f"{doc.body}\n\n<b>Редакция:</b> {doc.version}"
    await call.message.edit_text(text[:3900], reply_markup=legal_doc_menu(code))
    await call.answer()


@router.callback_query(F.data.startswith("legal:accept:"))
async def legal_accept(call: CallbackQuery) -> None:
    code = call.data.split(":")[-1]
    doc = get_doc(code)
    if not doc:
        await call.answer("Документ не найден", show_alert=True)
        return
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    await accept_legal_document(user["id"], code, doc.version)
    await call.answer("Согласие сохранено")
    await call.message.edit_text(f"Принято: <b>{doc.title}</b>\nРедакция: <b>{doc.version}</b>", reply_markup=legal_menu())


@router.callback_query(F.data == "legal:accept_required")
async def legal_accept_required(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    for code in REQUIRED_ON_START:
        doc = LEGAL_DOCS[code]
        await accept_legal_document(user["id"], code, doc.version)
    await call.message.edit_text(
        "Базовые условия приняты.\n\n"
        "Сохранено согласие с пользовательским соглашением, политикой персональных данных и правилами возвратов.",
        reply_markup=back_to_main(),
    )
    await call.answer("Сохранено")


@router.callback_query(F.data == "legal:accept_author")
async def legal_accept_author(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    for code in REQUIRED_FOR_AUTHOR:
        doc = LEGAL_DOCS[code]
        await accept_legal_document(user["id"], code, doc.version)
    await call.message.edit_text(
        "Правила автора приняты.\n\n"
        "Сохранено согласие с условиями, персональными данными, авторскими правами, правилами авторов и правилами контента.",
        reply_markup=back_to_main(),
    )
    await call.answer("Сохранено")


@router.callback_query(F.data == "legal:my_acceptances")
async def legal_my_acceptances(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    rows = await get_legal_acceptances(user["id"])
    if not rows:
        text = "Согласия пока не сохранены."
    else:
        lines = ["<b>Мои согласия</b>\n"]
        for row in rows:
            doc = get_doc(row["doc_code"])
            title = doc.title if doc else row["doc_code"]
            lines.append(f"• {title} · {row['doc_version']} · {row['accepted_at'][:10]}")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=legal_menu())
    await call.answer()


@router.message(Command("terms"))
async def cmd_terms(message: Message) -> None:
    await message.answer(LEGAL_DOCS["terms"].body[:3900], reply_markup=legal_doc_menu("terms"))


@router.message(Command("privacy"))
async def cmd_privacy(message: Message) -> None:
    await message.answer(LEGAL_DOCS["privacy"].body[:3900], reply_markup=legal_doc_menu("privacy"))


@router.message(Command("refunds"))
async def cmd_refunds(message: Message) -> None:
    await message.answer(LEGAL_DOCS["refunds"].body[:3900], reply_markup=legal_doc_menu("refunds"))


@router.message(Command("copyright"))
async def cmd_copyright(message: Message) -> None:
    await message.answer(LEGAL_DOCS["copyright"].body[:3900], reply_markup=legal_doc_menu("copyright"))


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    await message.answer(
        "<b>🛟 Поддержка</b>\n\n"
        "Опишите проблему одним сообщением. Для платежей укажите книгу, главу, дату и примерную сумму. "
        "Для авторских прав укажите ссылку/название материала и чем подтверждаются права.",
        reply_markup=back_to_main(),
    )
