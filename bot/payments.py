"""Telegram Stars payments: invoice → pre_checkout → successful_payment.

Currency XTR is Telegram Stars. start_parameter must be unique per invoice intent;
payload is opaque and echoed back in successful_payment, so we encode the plan id.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from .db import Db

log = logging.getLogger("bizlogger.payments")

router = Router()
_db: Db | None = None


def setup(db: Db) -> Router:
    global _db
    _db = db
    return router


def _payload_for(plan_id: int) -> str:
    return f"plan:{plan_id}"


def _plan_from_payload(payload: str) -> int | None:
    if not payload.startswith("plan:"):
        return None
    try:
        return int(payload.split(":", 1)[1])
    except (ValueError, IndexError):
        return None


def _format_plan(plan: dict) -> str:
    duration = plan["duration_days"]
    if duration is None:
        duration_label = "навсегда"
    else:
        duration_label = f"{duration} дн."
    return f"<b>{escape(plan['name'])}</b> — {plan['stars_price']} ⭐ · {duration_label}"


@router.message(Command("plans"))
@router.message(Command("buy"))
async def on_plans(msg: Message) -> None:
    assert _db is not None
    plans = await _db.get_active_plans()
    if not plans:
        await msg.answer("Тарифов пока нет — загляни позже.")
        return

    rows = [
        [
            InlineKeyboardButton(
                text=f"{p['name']} · {p['stars_price']} ⭐",
                callback_data=f"buy:{p['id']}",
            )
        ]
        for p in plans
    ]
    body = "💎 <b>Доступные тарифы</b>\n\n" + "\n".join(_format_plan(p) for p in plans)
    await msg.answer(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("buy:"))
async def on_buy(cb: CallbackQuery, bot: Bot) -> None:
    assert _db is not None
    if cb.data is None or cb.from_user is None:
        await cb.answer()
        return
    try:
        plan_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Некорректный тариф", show_alert=True)
        return

    plan = await _db.get_plan(plan_id)
    if plan is None:
        await cb.answer("Тариф больше не доступен", show_alert=True)
        return

    duration = plan["duration_days"]
    description = (
        f"Доступ {duration} дн." if duration is not None else "Пожизненный доступ"
    )
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=plan["name"],
        description=description,
        payload=_payload_for(plan["id"]),
        currency="XTR",
        prices=[LabeledPrice(label=plan["name"], amount=plan["stars_price"])],
        start_parameter=f"plan-{plan['id']}",
    )
    await cb.answer()


@router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery, bot: Bot) -> None:
    assert _db is not None
    plan_id = _plan_from_payload(q.invoice_payload or "")
    if plan_id is None:
        await bot.answer_pre_checkout_query(q.id, ok=False, error_message="Bad payload")
        return
    plan = await _db.get_plan(plan_id)
    if plan is None or plan["stars_price"] != q.total_amount:
        await bot.answer_pre_checkout_query(
            q.id, ok=False, error_message="Plan changed, try again"
        )
        return
    await bot.answer_pre_checkout_query(q.id, ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(msg: Message) -> None:
    assert _db is not None
    sp = msg.successful_payment
    if sp is None or msg.from_user is None:
        return
    plan_id = _plan_from_payload(sp.invoice_payload or "")
    if plan_id is None:
        log.warning("successful_payment with bad payload: %s", sp.invoice_payload)
        return
    plan = await _db.get_plan(plan_id)
    if plan is None:
        log.warning("successful_payment for missing plan id=%s", plan_id)
        return

    sub_id = await _db.record_subscription(
        user_id=msg.from_user.id,
        stars_amount=sp.total_amount,
        telegram_charge_id=sp.telegram_payment_charge_id,
        duration_days=plan["duration_days"],
    )
    log.info(
        "subscription recorded: id=%s user=%s plan=%s charge=%s",
        sub_id, msg.from_user.id, plan_id, sp.telegram_payment_charge_id,
    )
    duration_label = "навсегда" if plan["duration_days"] is None else f"{plan['duration_days']} дн."
    await msg.answer(
        f"✅ Оплата прошла. Тариф «{escape(plan['name'])}» активирован ({duration_label}).\n"
        f"⭐ Списано: {sp.total_amount}\n"
        f"id операции: <code>{escape(sp.telegram_payment_charge_id)}</code>"
    )
