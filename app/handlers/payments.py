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
    create_payment_intent,
    attach_payment_intent_message,
    cancel_payment_intent,
    validate_payment_intent,
    get_payment_intent,
    get_immediate_purchase_cancel_eligibility,
    create_immediate_cancel_request,
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
    get_book,
    get_channel_promotion,
    get_channel_promotion_availability,
    get_channel_promotion_price,
    reserve_channel_promotion,
    finish_channel_promotion,
    list_chapters_for_book,
    get_purchase_target,
    get_user_chapter_credit_summary,
    redeem_chapter_package_credit,
    get_user_by_telegram_id,
    get_refund_request,
    has_purchase_access,
    user_can_access_chapter,
    list_refund_requests,
    list_user_purchases,
    mark_purchase_refunded,
    set_refund_status,
    finalize_refund,
    reject_refund_request,
    set_setting,
    upsert_user,
    activate_premium_subscription,
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
    payment_invoice_menu,
    purchase_cancel_confirm_menu,
    refund_card_menu,
    refund_requests_menu,
    user_purchases_menu,
    channel_promotion_confirm_menu,
)
from app.services.payments import build_pay_target, describe_purchase_row
from app.services.payment_runtime import load_runtime_payment_settings
from app.services.notifications import payout_message, refund_message, send_user_notification
from app.services.publication import post_book_to_channel
from app.handlers.legal import missing_legal_codes, send_next_required_document
from app.legal_texts import REQUIRED_ON_START

router = Router()


async def _notify_finance_action(
    call: CallbackQuery,
    *,
    actor_user_id: int,
    event: str,
    target_type: str,
    target_id: int,
    app_user_id: int | None,
    telegram_id: int | None,
    text: str,
) -> None:
    result = await send_user_notification(
        app_user_id=app_user_id,
        telegram_id=telegram_id,
        text=text,
        bot=call.bot,
    )
    await add_audit(actor_user_id, f"notification_{result}", target_type, str(target_id), event, result)


class RefundRequestState(StatesGroup):
    reason = State()


class PromoApplyState(StatesGroup):
    code = State()


class PayoutMethodState(StatesGroup):
    details = State()


class PayoutSettingState(StatesGroup):
    value = State()


def _target_label(row) -> str:
    if "purchase_kind" in row.keys() and str(row["purchase_kind"] or "") == "premium":
        return "VoxLyra Premium"
    if "chapter_package_title" in row.keys() and row["chapter_package_title"]:
        return f"Пакет глав: {row['chapter_package_title']}"
    if "graphic_volume_number" in row.keys() and row["graphic_volume_number"]:
        return f"Том {row['graphic_volume_number']}: {row['graphic_volume_title'] or row['book_title'] or 'произведение'}"
    if "graphic_chapter_title" in row.keys() and row["graphic_chapter_title"]:
        return f"Графическая глава: {row['graphic_chapter_title']}"
    if row["chapter_title"]:
        return f"Глава: {row['chapter_title']}"
    if row["audio_title"]:
        return f"Аудио: {row['audio_title']}"
    if row["book_title"]:
        return f"Книга: {row['book_title']}"
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
    return tg_user_id in settings.owner_ids, user["id"]


def _promotion_available_label(value: str | None) -> str:
    if not value:
        return "позже"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d.%m.%Y")
    except Exception:
        return "позже"


async def _show_channel_promotion(message_or_call, book_id: int) -> None:
    user = await upsert_user(
        message_or_call.from_user.id,
        message_or_call.from_user.username,
        message_or_call.from_user.full_name,
    )
    book = await get_book(book_id)
    if not book or book["publication_status"] != "published":
        text = "Продвигать можно только опубликованную книгу."
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(text, show_alert=True)
        else:
            await message_or_call.answer(text, reply_markup=back_to_main())
        return
    if not settings.CHANNEL_ID.strip():
        text = "Канал пока не подключён. Платное продвижение временно недоступно."
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(text, show_alert=True)
        else:
            await message_or_call.answer(text, reply_markup=back_to_main())
        return

    availability = await get_channel_promotion_availability(book_id, int(user["id"]))
    if not availability.get("allowed"):
        text = (
            "Эта книга уже публиковалась в канале. Чтобы канал не превращался в спам, "
            f"повторное платное размещение будет доступно после {_promotion_available_label(availability.get('available_at'))}."
        )
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.edit_text(text, reply_markup=back_to_main())
            await message_or_call.answer()
        else:
            await message_or_call.answer(text, reply_markup=back_to_main())
        return

    if availability.get("reason") == "retry":
        promotion_id = int(availability["promotion_id"])
        result = await post_book_to_channel(
            message_or_call.bot, book_id, actor_user_id=int(user["id"]), force=True
        )
        await finish_channel_promotion(
            promotion_id, sent=result.channel_status == "sent", error=result.channel_error
        )
        text = (
            "Оплата уже была сохранена. Пост повторно отправлен в канал."
            if result.channel_status == "sent"
            else "Оплата уже сохранена, но Telegram снова не принял пост. Повторная оплата не нужна; попробуйте позже или обратитесь в поддержку."
        )
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.edit_text(text, reply_markup=back_to_main())
            await message_or_call.answer()
        else:
            await message_or_call.answer(text, reply_markup=back_to_main())
        return

    price = await get_channel_promotion_price()
    text = (
        f"<b>📢 Публикация книги в канале</b>\n\n"
        f"Книга: <b>{book['title']}</b>\n"
        f"Стоимость: <b>{price} Stars</b>\n\n"
        "После оплаты Вокслира сразу разместит карточку книги в подключённом канале. "
        "Одну книгу можно продвигать платно не чаще одного раза в 30 дней."
    )
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.edit_text(text, reply_markup=channel_promotion_confirm_menu(book_id, price))
        await message_or_call.answer()
    else:
        await message_or_call.answer(text, reply_markup=channel_promotion_confirm_menu(book_id, price))


async def _send_invoice(message_or_call, kind: str, target_id: int, promo_code: str | None = None, amount_stars: int | None = None) -> None:
    tg = message_or_call.from_user
    user = await upsert_user(tg.id, tg.username, tg.full_name)
    target_message = message_or_call.message if isinstance(message_or_call, CallbackQuery) else message_or_call
    if await send_next_required_document(target_message, int(user["id"])):
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Сначала примите обязательные документы", show_alert=True)
        return
    target = await build_pay_target(kind, target_id, user_id=user["id"], promo_code=promo_code, amount_stars=amount_stars)
    if target is None:
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer("Покупка не найдена", show_alert=True)
        else:
            await message_or_call.answer("Покупка не найдена.")
        return
    if target.amount_stars <= 0:
        if promo_code and kind in {"book", "chapter", "audio", "graphic"}:
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
    payment_cfg = await load_runtime_payment_settings()
    if not payment_cfg.stars_enabled:
        text = "Оплата через Telegram Stars временно выключена владельцем. Бесплатные материалы остаются доступны."
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(text, show_alert=True)
        else:
            await message_or_call.answer(text, reply_markup=back_to_main())
        return
    bot = message_or_call.bot
    chat_id = message_or_call.message.chat.id if isinstance(message_or_call, CallbackQuery) else message_or_call.chat.id
    intent = await create_payment_intent(int(user["id"]), target.payload, target.amount_stars)
    invoice_message = await bot.send_invoice(
        chat_id=chat_id,
        title=target.title,
        description=target.description,
        payload=intent["payload"],
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=("Публикация" if kind == "channel_promo" else "Пакет глав" if kind == "chapter_package" else "Доступ"), amount=target.amount_stars)],
        protect_content=True,
        reply_markup=payment_invoice_menu(intent["token"], target.amount_stars),
    )
    await attach_payment_intent_message(intent["token"], invoice_message.message_id)
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer("Счёт отправлен")


@router.message(CommandStart(deep_link=True))
async def start_deeplink(message: Message, state: FSMContext) -> None:
    args = (message.text or "").split(maxsplit=1)
    payload = args[1] if len(args) > 1 else ""
    if payload.startswith("buy_chapter_"):
        await _send_invoice(message, "chapter", int(payload.replace("buy_chapter_", "")))
        return
    if payload.startswith("buy_package_"):
        raw = payload.replace("buy_package_", "", 1)
        if raw.isdigit():
            await _send_invoice(message, "chapter_package", int(raw))
        return
    if payload.startswith("buy_audio_"):
        await _send_invoice(message, "audio", int(payload.replace("buy_audio_", "")))
        return
    if payload.startswith("buy_book_"):
        await _send_invoice(message, "book", int(payload.replace("buy_book_", "")))
        return
    if payload.startswith("buy_graphic_"):
        raw = payload.replace("buy_graphic_", "", 1)
        if raw.isdigit():
            await _send_invoice(message, "graphic", int(raw))
        return
    if payload.startswith("buy_volume_"):
        raw = payload.replace("buy_volume_", "", 1).split("_")
        if len(raw) == 2 and raw[0].isdigit() and raw[1].isdigit():
            await _send_invoice(message, "graphic_volume", int(raw[1]), amount_stars=int(raw[0]))
        return
    if payload.startswith("promote_book_"):
        raw = payload.replace("promote_book_", "")
        if raw.isdigit():
            await _show_channel_promotion(message, int(raw))
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


@router.callback_query(F.data.startswith("channel:promote:"))
async def channel_promote_preview(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    await _show_channel_promotion(call, book_id)


@router.callback_query(F.data.startswith("channel:promote_pay:"))
async def channel_promote_pay(call: CallbackQuery) -> None:
    book_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    book = await get_book(book_id)
    if not book or book["publication_status"] != "published":
        await call.answer("Книга не опубликована", show_alert=True)
        return
    if not settings.CHANNEL_ID.strip():
        await call.answer("Канал не подключён", show_alert=True)
        return
    availability = await get_channel_promotion_availability(book_id, int(user["id"]))
    if availability.get("reason") == "retry":
        await _show_channel_promotion(call, book_id)
        return
    if not availability.get("allowed"):
        await call.answer("Повторное размещение этой книги пока недоступно", show_alert=True)
        return
    price = await get_channel_promotion_price()
    try:
        promotion_id = await reserve_channel_promotion(book_id, int(user["id"]), price)
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    await _send_invoice(call, "channel_promo", promotion_id)


@router.callback_query(F.data.startswith("payment:cancel:"))
async def payment_invoice_cancel(call: CallbackQuery) -> None:
    token = call.data.split(":", 2)[-1]
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    intent = await get_payment_intent(token)
    if not intent or int(intent["user_id"]) != int(user["id"]):
        await call.answer("Счёт не найден", show_alert=True)
        return
    if str(intent["status"]) == "paid":
        await call.answer("Покупка уже оплачена", show_alert=True)
        return
    if not await cancel_payment_intent(token, int(user["id"])):
        await call.answer("Счёт уже отменён или устарел", show_alert=True)
        return
    await add_audit(int(user["id"]), "payment_invoice_canceled", "payment_intent", token, None, str(intent["canonical_payload"]))
    try:
        await call.message.delete()
    except Exception:
        try:
            await call.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await call.message.answer("Покупка отменена. Stars не списывались.", reply_markup=back_to_main())
    await call.answer("Покупка отменена")


@router.callback_query(F.data.startswith("buy:"))
async def buy_callback(call: CallbackQuery) -> None:
    _, kind, raw_id = call.data.split(":", 2)
    if kind not in {"book", "chapter", "audio", "graphic", "chapter_package"} or not raw_id.isdigit():
        await call.answer("Неверная покупка", show_alert=True)
        return
    await _send_invoice(call, kind, int(raw_id))


@router.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery) -> None:
    payment_cfg = await load_runtime_payment_settings()
    if not payment_cfg.stars_enabled:
        await query.answer(ok=False, error_message="Оплата через Telegram Stars временно выключена владельцем.")
        return
    user = await upsert_user(query.from_user.id, query.from_user.username, query.from_user.full_name)
    if str(query.invoice_payload or "").startswith("vox:intent:"):
        intent = await validate_payment_intent(query.invoice_payload, int(user["id"]), int(query.total_amount))
        if not intent:
            await query.answer(ok=False, error_message="Счёт отменён или срок его действия истёк. Откройте покупку заново.")
            return
    if await missing_legal_codes(int(user["id"]), REQUIRED_ON_START):
        await query.answer(ok=False, error_message="Сначала откройте бота и примите актуальную оферту и согласие на обработку данных.")
        return
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
    target = await get_purchase_target(payment.invoice_payload)

    # Premium — отдельный платёжный контур. С каждой оплаченной подписки
    # создаётся авторский фонд, который распределится после завершения периода
    # по подтверждённому чтению произведений Premium целыми Stars.
    if target and target.get("kind") == "premium":
        try:
            subscription_id = await activate_premium_subscription(
                user_id=int(user["id"]),
                plan_code=str(target.get("plan_code") or "monthly"),
                amount_stars=int(payment.total_amount),
                telegram_payment_charge_id=str(payment.telegram_payment_charge_id or ""),
                subscription_expiration_date=getattr(payment, "subscription_expiration_date", None),
                is_recurring=bool(getattr(payment, "is_recurring", False)),
                is_first_recurring=bool(getattr(payment, "is_first_recurring", False)),
                invoice_payload=str(payment.invoice_payload or ""),
            )
        except Exception as exc:
            await add_audit(user["id"], "premium_payment_save_failed", "payment", payment.invoice_payload, None, str(exc))
            await message.answer(
                "Оплата Premium прошла, но подписка не активировалась автоматически. Напишите в поддержку — платёж сохранён в Telegram и будет проверен.",
                reply_markup=back_to_main(),
            )
            return
        await add_audit(user["id"], "premium_payment_success", "premium_subscription", str(subscription_id), None, payment.invoice_payload)
        await message.answer(
            "<b>VoxLyra Premium активирован.</b>\n\n"
            "Открыты произведения, которые авторы включили в Premium, дополнительные стили карточек цитат, личная статистика, знак Premium и приоритет в очереди локальной озвучки. "
            "Часть оплаты направляется авторам пропорционально вашему реальному чтению. Базовые функции VoxLyra по-прежнему доступны всем пользователям.",
            reply_markup=back_to_main(),
        )
        return

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
    await add_audit(user["id"], "payment_success", "purchase", str(purchase_id), None, payment.invoice_payload)
    if target:
        if target["kind"] == "ad_budget":
            await message.answer(
                "<b>Оплата прошла.</b>\n\nРекламный бюджет пополнен. Кампания продолжит показы, если не заблокирована.",
                reply_markup=back_to_main(),
            )
        elif target["kind"] == "channel_promo":
            promotion = await get_channel_promotion(int(target["promotion_id"]))
            result = await post_book_to_channel(
                message.bot,
                int(target["book_id"]),
                actor_user_id=int(user["id"]),
                force=True,
            )
            await finish_channel_promotion(
                int(target["promotion_id"]),
                sent=result.channel_status == "sent",
                error=result.channel_error,
            )
            await add_audit(
                int(user["id"]),
                "paid_channel_promotion_sent" if result.channel_status == "sent" else "paid_channel_promotion_failed",
                "book",
                str(target["book_id"]),
                None,
                str(target["promotion_id"]),
            )
            if result.channel_status == "sent":
                await message.answer(
                    "<b>Оплата прошла.</b>\n\nКнига опубликована в канале. Следующее платное размещение этой книги будет доступно через 30 дней.",
                    reply_markup=back_to_main(),
                )
            else:
                await message.answer(
                    "<b>Оплата сохранена.</b>\n\nTelegram не принял пост в канал. Повторно платить не нужно: откройте продвижение этой книги позже, и бот повторит отправку.",
                    reply_markup=back_to_main(),
                )
        else:
            if target["kind"] == "chapter_package":
                count = int(target.get("chapters_count") or 0)
                await message.answer(
                    f"<b>Пакет оплачен.</b>\n\nНа книгу «{target.get('book_title') or 'Книга'}» доступно <b>{count} открытий глав</b>. "
                    "Они не привязаны к диапазону: можно открыть любые платные главы этой книги в любом порядке. "
                    "Каждая выбранная глава останется доступной навсегда.",
                    reply_markup=access_granted_menu("chapter_package", int(target["book_id"])),
                )
            else:
                access_kind = "graphic" if target["kind"] == "graphic_volume" else target["kind"]
                access_target = int(target.get("first_chapter_id") or target["target_id"])
                await message.answer(
                    "<b>Оплата прошла.</b>\n\nДоступ открыт. Покупку можно найти в разделе «Моё».",
                    reply_markup=access_granted_menu(access_kind, access_target),
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
    cancel_info = await get_immediate_purchase_cancel_eligibility(purchase_id, int(user["id"]))
    cancel_line = (
        f"\nАвтоматическая отмена: <b>доступна ещё {int(cancel_info.get('minutes_left') or 0)} мин.</b>"
        if cancel_info.get("allowed") else ""
    )
    await call.message.edit_text(
        f"<b>{target}</b>\n\n"
        f"Сумма: <b>{purchase['amount_stars']} Stars</b>\n"
        f"Статус: <b>{purchase['status']}</b>\n"
        f"Дата: <b>{purchase['created_at'][:16]}</b>{cancel_line}\n\n"
        "Неиспользованную покупку можно быстро отменить в отведённое время. После начала чтения или прослушивания остаётся обычный запрос на возврат с проверкой.",
        reply_markup=purchase_card_menu(purchase_id, purchase["status"], bool(cancel_info.get("allowed"))),
    )
    await call.answer()


@router.callback_query(F.data.startswith("purchase:cancel:"))
async def purchase_cancel_preview(call: CallbackQuery) -> None:
    purchase_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    purchase = await get_purchase(purchase_id)
    if not purchase or int(purchase["user_id"]) != int(user["id"]):
        await call.answer("Покупка не найдена", show_alert=True)
        return
    info = await get_immediate_purchase_cancel_eligibility(purchase_id, int(user["id"]))
    if not info.get("allowed"):
        await call.answer(str(info.get("reason") or "Автоматическая отмена недоступна"), show_alert=True)
        return
    await call.message.edit_text(
        "<b>Отменить покупку?</b>\n\n"
        f"Telegram вернёт <b>{int(purchase['amount_stars'])} Stars</b>, а доступ к материалу будет закрыт. "
        "Это действие нельзя отменить.",
        reply_markup=purchase_cancel_confirm_menu(purchase_id),
    )
    await call.answer()


@router.callback_query(F.data.startswith("purchase:cancel_confirm:"))
async def purchase_cancel_confirm(call: CallbackQuery) -> None:
    purchase_id = int(call.data.split(":")[-1])
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    purchase = await get_purchase(purchase_id)
    if not purchase or int(purchase["user_id"]) != int(user["id"]):
        await call.answer("Покупка не найдена", show_alert=True)
        return
    try:
        refund_id = await create_immediate_cancel_request(purchase_id, int(user["id"]))
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    try:
        await call.bot.refund_star_payment(
            user_id=int(purchase["telegram_id"]),
            telegram_payment_charge_id=str(purchase["telegram_payment_charge_id"]),
        )
    except Exception as exc:
        await add_audit(int(user["id"]), "purchase_cancel_refund_failed", "purchase", str(purchase_id), None, str(exc))
        await call.answer("Telegram не выполнил автоматический возврат. Запрос сохранён для проверки владельцем, доступ временно приостановлен.", show_alert=True)
        return
    if not await finalize_refund(refund_id, int(user["id"]), "Покупка отменена пользователем в период автоматической отмены"):
        await call.answer("Stars возвращены, но запись уже была обработана. Проверьте раздел покупок.", show_alert=True)
        return
    await add_audit(int(user["id"]), "purchase_canceled_by_user", "purchase", str(purchase_id), None, str(refund_id))
    await call.message.edit_text(
        f"Покупка отменена. <b>{int(purchase['amount_stars'])} Stars</b> возвращены через Telegram, доступ закрыт.",
        reply_markup=back_to_main(),
    )
    await call.answer("Покупка отменена")


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
    refund = await get_refund_request(refund_id)
    if not refund or refund["status"] not in {"new", "pending"}:
        await call.answer("Запрос уже обработан", show_alert=True)
        return
    note = "Запрос не прошёл проверку"
    ok = await reject_refund_request(refund_id, actor_user_id, note)
    if not ok:
        await call.answer("Запрос уже обработан", show_alert=True)
        return
    await add_audit(actor_user_id, "refund_rejected", "refund", str(refund_id))
    await _notify_finance_action(
        call,
        actor_user_id=int(actor_user_id),
        event="refund_rejected",
        target_type="refund",
        target_id=refund_id,
        app_user_id=int(refund["user_id"]),
        telegram_id=int(refund["telegram_id"]),
        text=refund_message("rejected", refund["amount_stars"], note),
    )
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
    if not await finalize_refund(refund_id, actor_user_id, "Возврат Stars выполнен"):
        await call.answer("Возврат выполнен Telegram, но запись уже была обработана. Проверьте журнал.", show_alert=True)
        return
    await add_audit(actor_user_id, "refund_approved", "refund", str(refund_id))
    await _notify_finance_action(
        call,
        actor_user_id=int(actor_user_id),
        event="refund_refunded",
        target_type="refund",
        target_id=refund_id,
        app_user_id=int(refund["user_id"]),
        telegram_id=int(refund["telegram_id"]),
        text=refund_message("refunded", refund["amount_stars"]),
    )
    await call.message.edit_text("Возврат одобрен и отправлен через Telegram Stars.", reply_markup=back_to_main())
    await call.answer("Возвращено")


@router.callback_query(F.data == "author:income")
async def author_income(call: CallbackQuery) -> None:
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    summary = await get_author_finance_summary(user["id"])
    min_stars = (await get_payout_settings()).get("payout_min_stars", "100")
    cfg = await load_runtime_payment_settings()
    frozen_line = "\n🧊 Выплаты заморожены до проверки." if summary.get("frozen", 0) else ""
    await call.message.edit_text(
        "<b>💰 Доход автора</b>\n\n"
        f"Продажи всего: <b>{summary.get('gross', 0)} Stars</b>\n"
        f"Комиссия платформы: <b>{summary.get('commission', 0)} Stars</b>\n"
        f"Чистый доход: <b>{summary.get('net', 0)} Stars</b>\n"
        f"Расчётный курс новых продаж: <b>{cfg.author_star_rate_minor / 100:.2f} ₽ за 1 Star</b>\n\n"
        f"В удержании: <b>{summary.get('held', 0)} Stars · {summary.get('held_minor', 0) / 100:.2f} ₽</b>\n"
        f"Доступно к выплате: <b>{summary.get('available', 0)} Stars · {summary.get('available_minor', 0) / 100:.2f} ₽</b>\n"
        f"В заявках: <b>{summary.get('requested', 0)} Stars · {summary.get('requested_minor', 0) / 100:.2f} ₽</b>\n"
        f"Уже выплачено: <b>{summary.get('paid', 0)} Stars · {summary.get('paid_minor', 0) / 100:.2f} ₽</b>\n"
        f"Возвращено покупателям: <b>{summary.get('refunded', 0)} Stars</b>\n"
        f"Минимальная сумма заявки: <b>{min_stars} Stars</b>{frozen_line}\n\n"
        "Курс фиксируется отдельно для каждой продажи. Изменение курса владельцем не пересчитывает уже начисленный доход.",
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
    allowed = await user_can_access_chapter(user["id"], chapter_id)
    if not allowed:
        from aiogram.utils.keyboard import InlineKeyboardBuilder

        mode = "free" if int(chapter["book_price_stars"] or 0) <= 0 else (
            "chapters" if str(chapter["pricing_type"] or "") == "chapters" else "whole_book"
        )
        can_buy_chapter = (
            mode == "chapters"
            and int(chapter["is_free"] or 0) == 0
            and int(chapter["price_stars"] or 0) > 0
        )
        credits = await get_user_chapter_credit_summary(user["id"], int(chapter["book_id"]), "text") if can_buy_chapter else {"remaining": 0}
        kb = InlineKeyboardBuilder()
        if can_buy_chapter and int(credits.get("remaining") or 0) > 0:
            kb.button(
                text=f"📖 Открыть из пакета · осталось {int(credits['remaining'])}",
                callback_data=f"package:unlock:chapter:{chapter_id}",
            )
        if can_buy_chapter:
            kb.button(
                text=f"⭐ Купить эту главу · {int(chapter['price_stars'] or 0)} Stars",
                callback_data=f"buy:chapter:{chapter_id}",
            )
        if int(chapter["book_price_stars"] or 0) > 0:
            kb.button(
                text=f"📚 Купить всю книгу · {int(chapter['book_price_stars'])} Stars",
                callback_data=f"buy:book:{int(chapter['book_id'])}",
            )
        kb.button(text="⬅️ В меню", callback_data="menu:main")
        kb.adjust(1)
        if can_buy_chapter:
            explanation = (
                "Эту главу можно купить отдельно или открыть покупкой всей книги. "
                "Цена главы относится только к этой главе."
            )
            if int(credits.get("remaining") or 0) > 0:
                explanation += " Также доступно одно открытие из ранее купленного пакета."
        else:
            explanation = "Эта глава отдельно не продаётся. Она откроется после покупки всей книги."
        await call.message.edit_text(
            f"<b>{chapter['book_title']}</b>\n<b>{chapter['title']}</b>\n\n{explanation}",
            reply_markup=kb.as_markup(),
        )
        await call.answer()
        return
    header = f"<b>{chapter['book_title']}</b>\n<b>{chapter['title']}</b>\n\n"
    chunks = _split_text(chapter["text"])
    await call.message.edit_text(header + chunks[0][:3500])
    for chunk in chunks[1:]:
        await call.message.answer(chunk)
    await call.message.answer("Глава открыта.", reply_markup=back_to_main())
    await call.answer()


@router.callback_query(F.data.startswith("package:unlock:chapter:"))
async def unlock_chapter_from_package_in_bot(call: CallbackQuery) -> None:
    raw = call.data.rsplit(":", 1)[-1]
    if not raw.isdigit():
        await call.answer("Неверная глава", show_alert=True)
        return
    chapter_id = int(raw)
    user = await upsert_user(call.from_user.id, call.from_user.username, call.from_user.full_name)
    try:
        result = await redeem_chapter_package_credit(user["id"], chapter_id=chapter_id)
    except ValueError as exc:
        await call.answer(str(exc), show_alert=True)
        return
    chapter = await get_chapter(chapter_id)
    if not chapter:
        await call.answer("Глава не найдена", show_alert=True)
        return
    header = f"<b>{chapter['book_title']}</b>\n<b>{chapter['title']}</b>\n\n"
    chunks = _split_text(chapter["text"])
    await call.message.edit_text(header + chunks[0][:3500])
    for chunk in chunks[1:]:
        await call.message.answer(chunk)
    remaining = result.get("remaining")
    suffix = f" В пакете осталось: {remaining}." if remaining is not None else ""
    await call.message.answer(f"Глава открыта навсегда.{suffix}", reply_markup=back_to_main())
    await call.answer("Открытие списано")


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
        f"Заявка на выплату #{payout_id} создана.\n\nСумма зафиксирована в рублях по курсам конкретных продаж.\n\n"
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
            lines.append(f"#{row['id']} · {row['amount_stars']} Stars · {int(row['amount_minor'] or 0) / 100:.2f} ₽ · {row['status']} · {row['requested_at'][:16]}")
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
        f"Сумма начислений: <b>{row['amount_stars']} Stars</b>\n"
        f"К выплате: <b>{int(row['amount_minor'] or 0) / 100:.2f} ₽</b>\n"
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
    row = await get_payout_request(payout_id)
    if not row:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    note = "Выплата одобрена"
    ok = await set_payout_request_status(payout_id, "approved", actor_user_id, note)
    if ok:
        await add_audit(actor_user_id, "payout_approved", "payout", str(payout_id))
        await _notify_finance_action(
            call,
            actor_user_id=int(actor_user_id),
            event="payout_approved",
            target_type="payout",
            target_id=payout_id,
            app_user_id=int(row["author_user_id"]),
            telegram_id=int(row["telegram_id"]),
            text=payout_message("approved", row["amount_stars"], note),
        )
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
    row = await get_payout_request(payout_id)
    if not row:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    note = "Выплата выполнена"
    ok = await set_payout_request_status(payout_id, "paid", actor_user_id, note)
    if ok:
        await add_audit(actor_user_id, "payout_paid", "payout", str(payout_id))
        await _notify_finance_action(
            call,
            actor_user_id=int(actor_user_id),
            event="payout_paid",
            target_type="payout",
            target_id=payout_id,
            app_user_id=int(row["author_user_id"]),
            telegram_id=int(row["telegram_id"]),
            text=payout_message("paid", row["amount_stars"], note),
        )
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
    row = await get_payout_request(payout_id)
    if not row:
        await call.answer("Заявка не найдена", show_alert=True)
        return
    note = "Выплата не прошла проверку"
    ok = await set_payout_request_status(payout_id, "rejected", actor_user_id, note)
    if ok:
        await add_audit(actor_user_id, "payout_rejected", "payout", str(payout_id))
        await _notify_finance_action(
            call,
            actor_user_id=int(actor_user_id),
            event="payout_rejected",
            target_type="payout",
            target_id=payout_id,
            app_user_id=int(row["author_user_id"]),
            telegram_id=int(row["telegram_id"]),
            text=payout_message("rejected", row["amount_stars"], note),
        )
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
    note = "Выплата приостановлена до дополнительной проверки"
    ok = await set_payout_request_status(payout_id, "frozen", actor_user_id, note)
    if ok:
        await set_author_payout_frozen(int(row["author_id"]), True, note, actor_user_id)
        await add_audit(actor_user_id, "payout_frozen", "payout", str(payout_id))
        await _notify_finance_action(
            call,
            actor_user_id=int(actor_user_id),
            event="payout_frozen",
            target_type="payout",
            target_id=payout_id,
            app_user_id=int(row["author_user_id"]),
            telegram_id=int(row["telegram_id"]),
            text=payout_message("frozen", row["amount_stars"], note),
        )
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
        "Срок удержания и резерв на споры меняются здесь же. Эти настройки видит только владелец платформы.",
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
