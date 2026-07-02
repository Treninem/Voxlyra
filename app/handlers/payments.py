from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from pathlib import Path

from aiogram.types import CallbackQuery, FSInputFile, LabeledPrice, Message, PreCheckoutQuery

from app.config import settings
from app.db import (
    add_audit,
    create_paid_purchase,
    create_free_promo_purchase,
    create_refund_request,
    get_admin_permissions,
    get_author_finance_summary,
    get_platform_finance_summary,
    create_author_payout_request,
    get_author_payout_method,
    get_payout_request,
    get_payout_settings,
    list_author_payout_requests,
    list_payout_requests,
    set_author_payout_frozen,
    set_author_payout_method,
    set_payout_request_status,
    get_purchase,
    get_chapter,
    get_audio_chapter,
    list_chapters_for_book,
    get_purchase_target,
    get_user_by_telegram_id,
    get_refund_request,
    has_purchase_access,
    list_refund_requests,
    list_user_purchases,
    mark_purchase_refunded,
    set_refund_status,
    set_setting,
    upsert_user,
)
from app.keyboards import (
    access_granted_menu,
    author_income_menu,
    back_to_main,
    finance_owner_menu,
    payout_card_menu,
    payout_requests_menu,
    payout_settings_menu,
    purchase_card_menu,
    refund_card_menu,
    refund_requests_menu,
    user_purchases_menu,
)
from app.services.payments import build_pay_target, describe_purchase_row

router = Router()


class RefundRequestState(StatesGroup):
    reason = State()


class PromoApplyState(StatesGroup):
    code = State()


class PayoutMethodState(StatesGroup):
    details = State()


class PayoutSettingState(StatesGroup):
    value = State()


def _target_label(row) -> str:
    if row["book_title"]:
        return f"Книга: {row['book_title']}"
    if row["chapter_title"]:
        return f"Глава: {row['chapter_title']}"
    if row["audio_title"]:
        return f"Аудио: {row['audio_title']}"
    return "Покупка"


async def _can_manage_refunds(tg_user_id: int) -> tuple[bool, int | None]:
    user = await get_user_by_telegram_id(tg_user_id)
    if user is None:
        user = await upsert_user(tg_user_id, None, None)
    if tg_user_id in settings.owner_ids:
        return True, user["id"]
    perms = await get_admin_permissions(user["id"])
    return "refunds" in perms, user["id"]


async def _can_manage_payouts(tg_user_id: int) -> tuple[bool, int | None]:
    user = await get_user_by_telegram_id(tg_user_id)
    if user is None:
        user = await upsert_user(tg_user_id, None, None)
    if tg_user_id in settings.owner_ids:
        return True, user["id"]
    perms = await get_admin_permissions(user["id"])
    return "payouts" in perms, user["id"]


async def _send_invoice(message_or_call, kind: str, target_id: int, promo_code: str | None = None, amount_stars: int | None = None) -> None:
    tg = message_or_call.from_user
    user = await upsert_user(tg.id, tg.username, tg.full_name)
    target = await build_pay_target(kind, target_id, user_id=user["id"], promo_code=promo_code, amount_stars=amount_stars)
    if target is None:
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Покупка не найдена", show_alert=True)
        else:
            await message_or_call.answer("Покупка не найдена.")
        return
    if target.amount_stars <= 0:
        if promo_code and kind in {"book", "chapter", "audio"}:
            try:
                purchase_id = await create_free_promo_purchase(user["id"], kind, target_id, promo_code)
                await add_audit(user["id"], "promo_free_access", "purchase", str(purchase_id), None, target.payload)
                text = "Промокод применён. Доступ открыт без оплаты."
                if isinstance(message_or_call, CallbackQuery):
                    await message_or_call.message.edit_text(text, reply_markup=access_granted_menu(kind, target_id))
                    await message_or_call.answer("Доступ открыт")
                else:
                    await message_or_call.answer(text, reply_markup=access_granted_menu(kind, target_id))
                return
            except Exception as exc:
                await add_audit(user["id"], "promo_free_failed", "promo", promo_code, None, str(exc))
        text = target.description or "Доступ уже открыт или материал бесплатный."
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(text, show_alert=True)
        else:
            await message_or_call.answer(text, reply_markup=back_to_main())
        return
    bot = message_or_call.bot
    chat_id = message_or_call.message.chat.id if isinstance(message_or_call, CallbackQuery) else message_or_call.chat.id
    await bot.send_invoice(
        chat_id=chat_id,
        title=target.title,
        description=target.description,
        payload=target.payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Доступ", amount=target.amount_stars)],
        protect_content=True,
    )
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer("Счёт отправлен")


@router.message(CommandStart(deep_link=True))
async def start_deeplink(message: Message, state: FSMContext) -> None:
    args = (message.text or "").split(maxsplit=1)
    payload = args[1] if len(args) > 1 else ""
    if payload.startswith("buy_chapter_"):
        await _send_invoice(message, "chapter", int(payload.replace("buy_chapter_", "")))
        return
    if payload.startswith("buy_audio_"):
        await _send_invoice(message, "audio", int(payload.replace("buy_audio_", "")))
        return
    if payload.startswith("buy_book_"):
        await _send_invoice(message, "book", int(payload.replace("buy_book_", "")))
        return
    if payload.startswith("promo_chapter_"):
        await state.update_data(kind="chapter", target_id=int(payload.replace("promo_chapter_", "")))
        await state.set_state(PromoApplyState.code)
        await message.answer("Введите промокод для покупки главы.")
        return
    if payload.startswith("promo_audio_"):
        await state.update_data(kind="audio", target_id=int(payload.replace("promo_audio_", "")))
        await state.set_state(PromoApplyState.code)
        await message.answer("Введите промокод для покупки аудиоглавы.")
        return
    if payload.startswith("promo_book_"):
        await state.update_data(kind="book", target_id=int(payload.replace("promo_book_", "")))
        await state.set_state(PromoApplyState.code)
        await message.answer("Введите промокод для покупки книги.")
        return


@router.callback_query(F.data.startswith("buy:"))
async def buy_callback(call: CallbackQuery) -> None:
    _, kind, raw_id = call.data.split(":", 2)
    if kind not in {"book", "chapter", "audio"} or not raw_id.isdigit():
        await call.answer("Неверная покупка", show_alert=True)
        return
    await _send_invoice(call, kind, int(raw_id))


@router.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery) -> None:
    target = await get_purchase_target(query.invoice_payload)
    if not target:
        await query.answer(ok=False, error_message="Покупка не найдена или уже недоступна.")
        return
    expected = int(target["amount_stars"] or 0)
    if expected <= 0:
        await query.answer(ok=False, error_message="Материал бесплатный или уже недоступен для оплаты.")
        return
    if int(query.total_amount) != expected:
        await query.answer(ok=False, error_message="Цена изменилась. Откройте покупку заново.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment_handler(message: Message) -> None:
    payment = message.successful_payment
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    try:
        purchase_id = await create_paid_purchase(
            user_id=user["id"],
            payload=payment.invoice_payload,
            amount_stars=int(payment.total_amount),
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
        )
    except Exception as exc:
        await add_audit(user["id"], "payment_save_failed", "payment", payment.invoice_payload, None, str(exc))
        await message.answer(
            "Оплата прошла, но доступ не записался автоматически. Напишите в поддержку, платёж будет проверен по журналу.",
            reply_markup=back_to_main(),
        )
        return
    target = await get_purchase_target(payment.invoice_payload)
    await add_audit(user["id"], "payment_success", "purchase", str(purchase_id), None, payment.invoice_payload)
    if target:
        if target["kind"] == "ad_budget":
            await message.answer(
                "<b>Оплата прошла.</b>\n\nРекламный бюджет пополнен. Кампания продолжит показы, если не заблокирована.",
                reply_markup=back_to_main(),
            )
        else:
            await message.answer(
                "<b>Оплата прошла.</b>\n\nДоступ открыт. Покупку можно найти в разделе «Моё».",
                reply_markup=access_granted_menu(target["kind"], int(target["target_id"])),
            )
    else:
        await message.answer(
            "<b>Оплата прошла.</b>\n\nДоступ открыт. Покупку можно найти в разделе «Моё».",
            reply_markup=back_to_main(),
        )


@router.callback_query(F.data == "main:my")
async def my_purchases(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    purchases = await list_user_purchases(user["id"])
    if not purchases:
        await call.message.edit_text("<b>⭐ Моё</b>\n\nПокупок пока нет.", reply_markup=back_to_main())
    else:
        await call.message.edit_text("<b>⭐ Моё</b>\n\nВаши последние покупки:", reply_markup=user_purchases_menu(purchases))
    await call.answer()


@router.callback_query(F.data.startswith("purchase:view:"))
async def purchase_view(call: CallbackQuery) -> None:
    purchase_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    purchase = await get_purchase(purchase_id)
    if not purchase or int(purchase["user_id"]) != int(user["id"]):
        await call.answer("Покупка не найдена", show_alert=True)
        return
    target = _target_label(purchase)
    await call.message.edit_text(
        f"<b>{target}</b>\n\n"
        f"Сумма: <b>{purchase['amount_stars']} Stars</b>\n"
        f"Статус: <b>{purchase['status']}</b>\n"
        f"Дата: <b>{purchase['created_at'][:16]}</b>\n\n"
        "Возврат можно запросить, если доступ не выдан, материал битый, оплата продублировалась или есть другая обоснованная причина.",
        reply_markup=purchase_card_menu(purchase_id, purchase["status"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("refund:request:"))
async def refund_request_start(call: CallbackQuery, state: FSMContext) -> None:
    purchase_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    purchase = await get_purchase(purchase_id)
    if not purchase or int(purchase["user_id"]) != int(user["id"]):
        await call.answer("Покупка не найдена", show_alert=True)
        return
    if purchase["status"] != "paid":
        await call.answer("По этой покупке возврат уже невозможен", show_alert=True)
        return
    await state.update_data(purchase_id=purchase_id)
    await state.set_state(RefundRequestState.reason)
    await call.message.edit_text(
        "Опишите причину возврата одним сообщением.\n\n"
        "Например: доступ не открылся, глава пустая, аудио не воспроизводится, случайная двойная покупка."
    )
    await call.answer()


@router.message(RefundRequestState.reason)
async def refund_request_finish(message: Message, state: FSMContext) -> None:
    reason = (message.text or "").strip()
    if len(reason) < 8:
        await message.answer("Опишите причину подробнее.")
        return
    data = await state.get_data()
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    refund_id = await create_refund_request(int(data["purchase_id"]), user["id"], reason)
    await add_audit(user["id"], "refund_requested", "refund", str(refund_id), None, reason[:200])
    await state.clear()
    await message.answer(
        "Запрос на возврат отправлен. Администрация проверит покупку и причину.",
        reply_markup=back_to_main(),
    )


@router.callback_query(F.data.in_({"owner:refunds", "mod:refunds", "refund:list"}))
async def refund_list(call: CallbackQuery) -> None:
    can, _ = await _can_manage_refunds(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    refunds = await list_refund_requests("new")
    if not refunds:
        await call.message.edit_text("Новых запросов на возврат нет.", reply_markup=back_to_main())
    else:
        await call.message.edit_text("<b>↩️ Возвраты</b>\n\nВыберите запрос.", reply_markup=refund_requests_menu(refunds))
    await call.answer()


@router.callback_query(F.data.startswith("refund:card:"))
async def refund_card(call: CallbackQuery) -> None:
    can, _ = await _can_manage_refunds(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    refund_id = int(call.data.split(":")[-1])
    refund = await get_refund_request(refund_id)
    if not refund:
        await call.answer("Запрос не найден", show_alert=True)
        return
    target = refund["book_title"] or refund["chapter_title"] or refund["audio_title"] or "Покупка"
    buyer = refund["username"] or refund["full_name"] or refund["telegram_id"]
    await call.message.edit_text(
        f"<b>Запрос возврата #{refund['id']}</b>\n\n"
        f"Покупатель: <b>{buyer}</b>\n"
        f"Материал: <b>{target}</b>\n"
        f"Сумма: <b>{refund['amount_stars']} Stars</b>\n"
        f"Статус покупки: <b>{refund['purchase_status']}</b>\n\n"
        f"Причина:\n{refund['reason']}",
        reply_markup=refund_card_menu(refund_id, refund["status"] == "new" and refund["purchase_status"] == "paid"),
    )
    await call.answer()


@router.callback_query(F.data.startswith("refund:reject:"))
async def refund_reject(call: CallbackQuery) -> None:
    can, actor_user_id = await _can_manage_refunds(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    refund_id = int(call.data.split(":")[-1])
    await set_refund_status(refund_id, "rejected", actor_user_id, "Отклонено модерацией")
    await add_audit(actor_user_id, "refund_rejected", "refund", str(refund_id))
    await call.message.edit_text("Возврат отклонён.", reply_markup=back_to_main())
    await call.answer("Отклонено")


@router.callback_query(F.data.startswith("refund:approve:"))
async def refund_approve(call: CallbackQuery) -> None:
    can, actor_user_id = await _can_manage_refunds(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    refund_id = int(call.data.split(":")[-1])
    refund = await get_refund_request(refund_id)
    if not refund:
        await call.answer("Запрос не найден", show_alert=True)
        return
    if refund["purchase_status"] != "paid":
        await call.answer("Покупка уже не в статусе оплаты", show_alert=True)
        return
    try:
        await call.bot.refund_star_payment(
            user_id=int(refund["telegram_id"]),
            telegram_payment_charge_id=refund["telegram_payment_charge_id"],
        )
    except Exception as exc:
        await add_audit(actor_user_id, "refund_failed", "refund", str(refund_id), None, str(exc))
        await call.answer("Telegram не принял возврат. Подробности записаны в журнал.", show_alert=True)
        return
    await mark_purchase_refunded(int(refund["purchase_id"]))
    await set_refund_status(refund_id, "refunded", actor_user_id, "Возврат Stars выполнен")
    await add_audit(actor_user_id, "refund_approved", "refund", str(refund_id))
    await call.message.edit_text("Возврат одобрен и отправлен через Telegram Stars.", reply_markup=back_to_main())
    await call.answer("Возвращено")


@router.callback_query(F.data == "author:income")
async def author_income(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    summary = await get_author_finance_summary(user["id"])
    min_stars = (await get_payout_settings()).get("payout_min_stars", "100")
    frozen_line = "\n🧊 Выплаты заморожены до проверки." if summary.get("frozen", 0) else ""
    await call.message.edit_text(
        "<b>💰 Доход автора</b>\n\n"
        f"Продажи всего: <b>{summary.get('gross', 0)} Stars</b>\n"
        f"Комиссия платформы: <b>{summary.get('commission', 0)} Stars</b>\n"
        f"Доход автора: <b>{summary.get('net', 0)} Stars</b>\n"
        f"В удержании: <b>{summary.get('held', 0)} Stars</b>\n"
        f"Доступно к выводу: <b>{summary.get('available', 0)} Stars</b>\n"
        f"В заявках на выплату: <b>{summary.get('requested', 0)} Stars</b>\n"
        f"Уже выплачено: <b>{summary.get('paid', 0)} Stars</b>\n"
        f"Возвращено покупателям: <b>{summary.get('refunded', 0)} Stars</b>\n"
        f"Минимальная сумма вывода: <b>{min_stars} Stars</b>{frozen_line}\n\n"
        "Выплата создаётся заявкой и проверяется владельцем или человеком с правом выплат.",
        reply_markup=author_income_menu(summary.get('available', 0)),
    )
    await call.answer()


@router.callback_query(F.data == "owner:finance")
async def owner_finance_report(call: CallbackQuery) -> None:
    if call.from_user.id not in settings.owner_ids:
        await call.answer("Недоступно", show_alert=True)
        return
    summary = await get_platform_finance_summary()
    await call.message.edit_text(
        "<b>💰 Финансы</b>\n\n"
        f"Оплачено: <b>{summary['paid_gross']} Stars</b> · покупок: <b>{summary['paid_count']}</b>\n"
        f"Возвращено: <b>{summary['refunded_gross']} Stars</b> · возвратов: <b>{summary['refunded_count']}</b>\n"
        f"Комиссия платформы: <b>{summary['platform_commission']} Stars</b>\n"
        f"Авторам в удержании: <b>{summary['held_authors']} Stars</b>\n"
        f"Авторам доступно: <b>{summary['available_authors']} Stars</b>\n"
        f"В заявках авторам: <b>{summary.get('requested_authors', 0)} Stars</b>\n"
        f"Уже выплачено авторам: <b>{summary.get('paid_authors', 0)} Stars</b>\n"
        f"Открытых заявок: <b>{summary.get('payout_requests_open', 0)}</b>\n\n"
        "Комиссии, удержания, возвраты и выплаты доступны только по выданным правам.",
        reply_markup=finance_owner_menu(),
    )
    await call.answer()



def _split_text(text: str, limit: int = 3600) -> list[str]:
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n"):
        line = paragraph.strip()
        if not line:
            continue
        add = line + "\n\n"
        if len(current) + len(add) > limit and current:
            chunks.append(current.strip())
            current = add
        else:
            current += add
    if current.strip():
        chunks.append(current.strip())
    return chunks or [text[:limit]]


@router.callback_query(F.data.startswith("read:chapter:"))
async def read_paid_or_free_chapter(call: CallbackQuery) -> None:
    chapter_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    chapter = await get_chapter(chapter_id)
    if not chapter:
        await call.answer("Глава не найдена", show_alert=True)
        return
    allowed = bool(chapter["is_free"]) or await has_purchase_access(user["id"], chapter_id=chapter_id)
    if not allowed:
        await call.answer("Сначала купите доступ", show_alert=True)
        await _send_invoice(call, "chapter", chapter_id)
        return
    header = f"<b>{chapter['book_title']}</b>\n<b>{chapter['title']}</b>\n\n"
    chunks = _split_text(chapter["text"])
    await call.message.edit_text(header + chunks[0][:3500])
    for chunk in chunks[1:]:
        await call.message.answer(chunk)
    await call.message.answer("Глава открыта.", reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data.startswith("listen:audio:"))
async def send_paid_or_free_audio(call: CallbackQuery) -> None:
    audio_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    audio = await get_audio_chapter(audio_id)
    if not audio:
        await call.answer("Аудиоглава не найдена", show_alert=True)
        return
    allowed = bool(audio["is_free"]) or await has_purchase_access(user["id"], audio_chapter_id=audio_id)
    if not allowed:
        await call.answer("Сначала купите доступ", show_alert=True)
        await _send_invoice(call, "audio", audio_id)
        return
    caption = f"<b>{audio['book_title']}</b>\n{audio['title']}\nДиктор: {audio['narrator'] or 'не указан'}"
    if audio["file_id"]:
        await call.message.answer_audio(audio["file_id"], caption=caption)
    elif audio["file_path"] and Path(audio["file_path"]).exists():
        await call.message.answer_audio(FSInputFile(audio["file_path"], filename=audio["source_filename"] or Path(audio["file_path"]).name), caption=caption)
    else:
        await call.answer("Файл аудио не найден", show_alert=True)
        return
    await call.message.answer("Аудиоглава открыта.", reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data.startswith("open:book:"))
async def open_paid_or_free_book(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    if not await has_purchase_access(user["id"], book_id=book_id):
        await call.answer("Сначала купите книгу", show_alert=True)
        await _send_invoice(call, "book", book_id)
        return
    chapters = await list_chapters_for_book(book_id)
    if not chapters:
        await call.message.edit_text("В книге пока нет глав.", reply_markup=back_to_main())
        await call.answer()
        return
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    kb = InlineKeyboardBuilder()
    for chapter in chapters[:50]:
        kb.button(text=f"{chapter['number']}. {chapter['title']}", callback_data=f"read:chapter:{chapter['id']}")
    kb.button(text="⬅️ В меню", callback_data="menu:main")
    kb.adjust(1)
    await call.message.edit_text("<b>Книга куплена.</b>\n\nВыберите главу.", reply_markup=kb.as_markup())
    await call.answer()


@router.callback_query(F.data.startswith("promo:apply:"))
async def promo_apply_start(call: CallbackQuery, state: FSMContext) -> None:
    parts = call.data.split(":")
    if len(parts) != 4 or parts[2] not in {"book", "chapter", "audio"}:
        await call.answer("Неверный промокод", show_alert=True)
        return
    await state.update_data(kind=parts[2], target_id=int(parts[3]))
    await state.set_state(PromoApplyState.code)
    await call.message.edit_text("Введите промокод одним сообщением.")
    await call.answer()


@router.message(PromoApplyState.code)
async def promo_apply_finish(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    if len(code) < 3:
        await message.answer("Промокод слишком короткий.")
        return
    data = await state.get_data()
    await state.clear()
    await _send_invoice(message, data["kind"], int(data["target_id"]), promo_code=code)


@router.callback_query(F.data.startswith("adbudget:pay:"))
async def ad_budget_pay(call: CallbackQuery) -> None:
    parts = call.data.split(":")
    if len(parts) != 4:
        await call.answer("Неверное пополнение", show_alert=True)
        return
    campaign_id = int(parts[2])
    amount = int(parts[3])
    await _send_invoice(call, "ad_budget", campaign_id, amount_stars=amount)


# =========================
# Stage 10: author payouts
# =========================


def _detect_payout_method(details: str) -> str:
    raw = (details or "").strip().lower()
    if raw.startswith("ton") or "eq" in raw[:20]:
        return "TON"
    if raw.startswith("сбп") or raw.startswith("sbp"):
        return "SBP"
    if raw.startswith("карта") or raw.startswith("card"):
        return "CARD"
    if raw.startswith("юмани") or raw.startswith("yoomoney") or raw.startswith("юmoney"):
        return "YOOMONEY"
    if raw.startswith("usdt") or raw.startswith("crypto") or raw.startswith("крипто"):
        return "CRYPTO"
    return "manual"


@router.callback_query(F.data == "author:payout_method")
async def author_payout_method_start(call: CallbackQuery, state: FSMContext) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    current = await get_author_payout_method(user["id"])
    current_text = ""
    if current:
        current_text = f"\n\nТекущий способ: <b>{current['method_type']}</b>\n{current['details']}"
    await state.set_state(PayoutMethodState.details)
    await call.message.edit_text(
        "<b>🏦 Реквизиты для выплаты</b>\n\n"
        "Введите реквизиты одним сообщением.\n\n"
        "Примеры:\n"
        "TON: EQ...\n"
        "СБП: +79990000000, Банк, Имя\n"
        "Карта: 2200..., Банк, Имя\n\n"
        "На старте выплата всё равно подтверждается вручную владельцем." + current_text
    )
    await call.answer()


@router.message(PayoutMethodState.details)
async def author_payout_method_finish(message: Message, state: FSMContext) -> None:
    details = (message.text or "").strip()
    if len(details) < 5:
        await message.answer("Введите реквизиты подробнее.")
        return
    user = await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    try:
        await set_author_payout_method(user["id"], _detect_payout_method(details), details)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await add_audit(user["id"], "author_payout_method_saved", "author", str(user["id"]))
    await state.clear()
    await message.answer("Реквизиты сохранены.", reply_markup=author_income_menu())


@router.callback_query(F.data == "author:payout_request")
async def author_payout_request(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    try:
        payout_id = await create_author_payout_request(user["id"])
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await add_audit(user["id"], "author_payout_requested", "payout", str(payout_id))
    await call.message.edit_text(
        f"Заявка на выплату #{payout_id} создана.\n\n"
        "Администрация проверит удержания, жалобы, возвраты и реквизиты. После реальной выплаты заявка будет отмечена как выплаченная.",
        reply_markup=author_income_menu(),
    )
    await call.answer("Заявка создана")


@router.callback_query(F.data == "author:payout_history")
async def author_payout_history(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    rows = await list_author_payout_requests(user["id"], 15)
    if not rows:
        text = "История выплат пока пустая."
    else:
        lines = ["<b>🧾 История выплат</b>\n"]
        for row in rows:
            lines.append(f"#{row['id']} · {row['amount_stars']} Stars · {row['status']} · {row['requested_at'][:16]}")
        text = "\n".join(lines)
    await call.message.edit_text(text, reply_markup=author_income_menu())
    await call.answer()


@router.callback_query(F.data.in_({"owner:payouts", "mod:payouts"}) | F.data.startswith("owner:payouts:"))
async def owner_payouts(call: CallbackQuery) -> None:
    can, _ = await _can_manage_payouts(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    status = "new"
    if call.data.endswith(":approved"):
        status = "approved"
    rows = await list_payout_requests(status)
    title = "новые" if status == "new" else "одобренные"
    if not rows:
        await call.message.edit_text(f"Заявок на выплату нет: {title}.", reply_markup=finance_owner_menu())
    else:
        await call.message.edit_text(f"<b>📤 Выплаты авторам</b>\n\nВыберите заявку: {title}.", reply_markup=payout_requests_menu(rows))
    await call.answer()


@router.callback_query(F.data.startswith("payout:card:"))
async def payout_card(call: CallbackQuery) -> None:
    can, _ = await _can_manage_payouts(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    payout_id = int(call.data.split(":")[-1])
    row = await get_payout_request(payout_id)
    if not row:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    author = row["pen_name"] or row["username"] or row["telegram_id"]
    frozen = "\n🧊 Выплаты автора заморожены." if row["is_frozen"] else ""
    await call.message.edit_text(
        f"<b>Заявка на выплату #{row['id']}</b>\n\n"
        f"Автор: <b>{author}</b>\n"
        f"Telegram: <b>{row['username'] or row['telegram_id']}</b>\n"
        f"Сумма: <b>{row['amount_stars']} Stars</b>\n"
        f"Способ: <b>{row['method_type']}</b>\n"
        f"Статус: <b>{row['status']}</b>\n"
        f"Дата: <b>{row['requested_at'][:16]}</b>{frozen}\n\n"
        f"Реквизиты:\n{row['payout_details']}",
        reply_markup=payout_card_menu(payout_id, row["status"]),
    )
    await call.answer()


@router.callback_query(F.data.startswith("payout:approve:"))
async def payout_approve(call: CallbackQuery) -> None:
    can, actor_user_id = await _can_manage_payouts(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    payout_id = int(call.data.split(":")[-1])
    ok = await set_payout_request_status(payout_id, "approved", actor_user_id, "Одобрено к ручной выплате")
    if ok:
        await add_audit(actor_user_id, "payout_approved", "payout", str(payout_id))
        await call.message.edit_text("Заявка одобрена. После реального перевода нажмите «Отметить выплачено».", reply_markup=payout_card_menu(payout_id, "approved"))
        await call.answer("Одобрено")
    else:
        await call.answer("Не удалось изменить заявку", show_alert=True)


@router.callback_query(F.data.startswith("payout:paid:"))
async def payout_paid(call: CallbackQuery) -> None:
    can, actor_user_id = await _can_manage_payouts(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    payout_id = int(call.data.split(":")[-1])
    ok = await set_payout_request_status(payout_id, "paid", actor_user_id, "Выплата отмечена как выполненная")
    if ok:
        await add_audit(actor_user_id, "payout_paid", "payout", str(payout_id))
        await call.message.edit_text("Заявка отмечена как выплаченная. Деньги автора перенесены в статус «выплачено».", reply_markup=finance_owner_menu())
        await call.answer("Выплачено")
    else:
        await call.answer("Не удалось изменить заявку", show_alert=True)


@router.callback_query(F.data.startswith("payout:reject:"))
async def payout_reject(call: CallbackQuery) -> None:
    can, actor_user_id = await _can_manage_payouts(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    payout_id = int(call.data.split(":")[-1])
    ok = await set_payout_request_status(payout_id, "rejected", actor_user_id, "Отклонено администрацией")
    if ok:
        await add_audit(actor_user_id, "payout_rejected", "payout", str(payout_id))
        await call.message.edit_text("Заявка отклонена. Сумма возвращена в доступный баланс автора.", reply_markup=finance_owner_menu())
        await call.answer("Отклонено")
    else:
        await call.answer("Не удалось изменить заявку", show_alert=True)


@router.callback_query(F.data.startswith("payout:freeze:"))
async def payout_freeze(call: CallbackQuery) -> None:
    can, actor_user_id = await _can_manage_payouts(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    payout_id = int(call.data.split(":")[-1])
    row = await get_payout_request(payout_id)
    if not row:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    await set_author_payout_frozen(int(row["author_id"]), True, "Заморожено по заявке на выплату", actor_user_id)
    ok = await set_payout_request_status(payout_id, "frozen", actor_user_id, "Выплаты заморожены до проверки")
    if ok:
        await add_audit(actor_user_id, "payout_frozen", "payout", str(payout_id))
        await call.message.edit_text("Выплаты автора заморожены до проверки. Заявка переведена в статус заморозки.", reply_markup=finance_owner_menu())
        await call.answer("Заморожено")
    else:
        await call.answer("Не удалось изменить заявку", show_alert=True)


@router.callback_query(F.data == "owner:payout_settings")
async def payout_settings(call: CallbackQuery) -> None:
    can, _ = await _can_manage_payouts(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    data = await get_payout_settings()
    await call.message.edit_text(
        "<b>⚙️ Удержания и вывод</b>\n\n"
        f"Минимальная сумма вывода: <b>{data['payout_min_stars']} Stars</b>\n"
        f"Основной способ: <b>{data['payout_default_method']}</b>\n"
        f"Ручная проверка: <b>{'да' if data['payout_manual_review'] != '0' else 'нет'}</b>\n\n"
        "Срок удержания и резерв на споры меняются здесь же. Эти настройки видит только владелец и люди с правом выплат.",
        reply_markup=payout_settings_menu(),
    )
    await call.answer()


@router.callback_query(F.data.startswith("owner:set_payout:"))
async def payout_setting_start(call: CallbackQuery, state: FSMContext) -> None:
    can, _ = await _can_manage_payouts(call.from_user.id)
    if not can:
        await call.answer("Недоступно", show_alert=True)
        return
    key = call.data.split(":")[-1]
    if key not in {"payout_min_stars", "hold_days_default", "reserve_percent"}:
        await call.answer("Неизвестная настройка", show_alert=True)
        return
    await state.update_data(payout_setting_key=key)
    await state.set_state(PayoutSettingState.value)
    await call.message.edit_text("Введите новое значение числом. Например: 100")
    await call.answer()


@router.message(PayoutSettingState.value)
async def payout_setting_finish(message: Message, state: FSMContext) -> None:
    can, actor_user_id = await _can_manage_payouts(message.from_user.id)
    if not can:
        await message.answer("Недоступно")
        await state.clear()
        return
    raw = (message.text or "").strip().replace("%", "")
    if not raw.isdigit():
        await message.answer("Введите число.")
        return
    value = int(raw)
    if value < 0 or value > 10000:
        await message.answer("Слишком большое или отрицательное значение.")
        return
    data = await state.get_data()
    key = data["payout_setting_key"]
    await set_setting(key, str(value))
    await add_audit(actor_user_id, "payout_setting_changed", "setting", key, None, str(value))
    await state.clear()
    await message.answer("Настройка сохранена.", reply_markup=finance_owner_menu())
