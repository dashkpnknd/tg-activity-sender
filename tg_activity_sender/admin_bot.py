from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_activity_sender.core import ActivityMode, CampaignStatus
from tg_activity_sender.db import Database
from tg_activity_sender.keyboards import accounts_menu, back, campaigns_menu, main_menu, sequences_menu
from tg_activity_sender.states import BlacklistStates, CampaignStates, SequenceStates
from tg_activity_sender.telegram_accounts import AccountManager


def build_dispatcher(db: Database, account_manager: AccountManager, *, admin_ids: frozenset[int], media_dir: Path) -> Dispatcher:
    router = Router()

    def is_admin(user_id: int | None) -> bool:
        return bool(user_id) and (not admin_ids or user_id in admin_ids)

    async def guard(message_or_callback: Message | CallbackQuery) -> bool:
        user = message_or_callback.from_user
        if is_admin(user.id if user else None):
            return True
        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.answer("Нет доступа", show_alert=True)
        else:
            await message_or_callback.answer("Нет доступа")
        return False

    @router.message(F.text == "/start")
    async def start(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.clear()
        await message.answer("Главное меню", reply_markup=main_menu())

    @router.callback_query(F.data == "main")
    async def show_main(callback: CallbackQuery, state: FSMContext) -> None:
        if not await guard(callback):
            return
        await state.clear()
        await callback.message.edit_text("Главное меню", reply_markup=main_menu())

    @router.callback_query(F.data == "accounts")
    async def show_accounts(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        await callback.message.edit_text("Аккаунты", reply_markup=accounts_menu())

    @router.callback_query(F.data == "account:list")
    async def list_accounts(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        accounts = db.list_accounts()
        if not accounts:
            text = "Аккаунты ещё не добавлены."
        else:
            lines = ["<b>Аккаунты</b>"]
            for account in accounts:
                title = account.username or account.display_name or str(account.telegram_id)
                lines.append(f"• <code>{account.telegram_id}</code> @{title}")
            text = "\n".join(lines)
        await callback.message.edit_text(text, reply_markup=back("accounts"), parse_mode=ParseMode.HTML)

    @router.callback_query(F.data == "account:add_qr")
    async def add_account_qr(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        token = uuid.uuid4().hex
        await callback.message.edit_text("Создаю QR для входа...")
        ticket = await account_manager.begin_qr_login(token)
        await callback.message.answer_photo(
            FSInputFile(ticket.qr_png_path),
            caption=(
                "Открой Telegram на аккаунте, который добавляем:\n"
                "Настройки -> Устройства -> Подключить устройство.\n\n"
                f"Ссылка: <code>{ticket.url}</code>\n\n"
                "Жду сканирование до 2 минут."
            ),
            parse_mode=ParseMode.HTML,
        )
        try:
            telegram_id = await account_manager.finish_qr_login(token)
            await callback.message.answer(f"Аккаунт <code>{telegram_id}</code> добавлен.", parse_mode=ParseMode.HTML)
        except Exception as exc:
            await callback.message.answer(f"Не удалось добавить аккаунт: {exc}")

    @router.callback_query(F.data == "sequences")
    async def show_sequences(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        await callback.message.edit_text("Цепочки сообщений", reply_markup=sequences_menu())

    @router.callback_query(F.data == "sequence:create")
    async def create_sequence_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not await guard(callback):
            return
        await state.set_state(SequenceStates.waiting_name)
        await callback.message.edit_text("Напиши название цепочки.", reply_markup=back("sequences"))

    @router.message(SequenceStates.waiting_name)
    async def create_sequence_name(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        sequence = db.create_sequence(message.text.strip())
        await state.update_data(sequence_id=sequence.id)
        await state.set_state(SequenceStates.waiting_step)
        await message.answer("Цепочка создана. Пришли первый шаг: текст, фото, видео, документ, голосовое или кружок.")

    @router.message(SequenceStates.waiting_step)
    async def add_sequence_step(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        data = await state.get_data()
        sequence_id = int(data["sequence_id"])
        steps = db.get_sequence_steps(sequence_id)
        payload = await capture_message_payload(message, media_dir)
        db.add_sequence_step(sequence_id, order=len(steps) + 1, payload=payload, delay_after_seconds=0)
        await message.answer("Шаг добавлен. Пришли следующий шаг или нажми /start, чтобы выйти в меню.")

    @router.callback_query(F.data == "sequence:list")
    async def list_sequences(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        sequences = db.list_sequences()
        if not sequences:
            text = "Цепочек ещё нет."
        else:
            text = "<b>Цепочки</b>\n" + "\n".join(f"• #{item.id} {item.name}" for item in sequences)
        await callback.message.edit_text(text, reply_markup=back("sequences"), parse_mode=ParseMode.HTML)

    @router.callback_query(F.data == "campaigns")
    async def show_campaigns(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        await callback.message.edit_text("Кампании", reply_markup=campaigns_menu())

    @router.callback_query(F.data == "campaign:create")
    async def campaign_create_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not await guard(callback):
            return
        sequences = db.list_sequences()
        if not sequences:
            await callback.answer("Сначала создай цепочку сообщений.", show_alert=True)
            return
        await state.update_data(sequence_id=sequences[0].id)
        await state.set_state(CampaignStates.waiting_name)
        await callback.message.edit_text("Напиши название кампании.", reply_markup=back("campaigns"))

    @router.message(CampaignStates.waiting_name)
    async def campaign_name(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.update_data(name=message.text.strip())
        await state.set_state(CampaignStates.waiting_days)
        await message.answer("Сколько дней порог активности? Например: 10")

    @router.message(CampaignStates.waiting_days)
    async def campaign_days(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        if not message.text.isdigit():
            await message.answer("Нужно число дней.")
            return
        await state.update_data(days=int(message.text))
        await state.set_state(CampaignStates.waiting_schedule)
        await message.answer("В какие часы слать? Формат: 10:00-20:00")

    @router.message(CampaignStates.waiting_schedule)
    async def campaign_schedule(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        data = await state.get_data()
        campaign = db.create_campaign(
            name=data["name"],
            sequence_id=int(data["sequence_id"]),
            activity_mode=ActivityMode.INACTIVE,
            days_threshold=int(data["days"]),
            include_chats=True,
            include_private=True,
            schedule_window=message.text.strip(),
            delay_between_recipients_seconds=300,
        )
        await state.clear()
        await message.answer(
            f"Кампания #{campaign.id} создана. По умолчанию: неактивные диалоги, чаты + лички, пауза 5 минут."
        )

    @router.callback_query(F.data.in_({"campaign:list_all", "campaign:list_running"}))
    async def list_campaigns(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        status = CampaignStatus.RUNNING if callback.data == "campaign:list_running" else None
        campaigns = db.list_campaigns(status)
        if not campaigns:
            text = "Кампаний нет."
            keyboard = back("campaigns")
        else:
            lines = ["<b>Кампании</b>"]
            buttons = []
            for item in campaigns:
                lines.append(f"• #{item.id} {item.name}: {item.status}")
                buttons.append(
                    [
                        InlineKeyboardButton(text=f"▶ #{item.id}", callback_data=f"campaign:run:{item.id}"),
                        InlineKeyboardButton(text=f"⏸ #{item.id}", callback_data=f"campaign:pause:{item.id}"),
                        InlineKeyboardButton(text=f"■ #{item.id}", callback_data=f"campaign:stop:{item.id}"),
                    ]
                )
            buttons.append([InlineKeyboardButton(text="Назад", callback_data="campaigns")])
            text = "\n".join(lines)
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    @router.callback_query(F.data.startswith("campaign:run:"))
    async def run_campaign(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        campaign_id = int(callback.data.split(":")[-1])
        db.update_campaign_status(campaign_id, CampaignStatus.RUNNING)
        await callback.answer("Кампания запущена")

    @router.callback_query(F.data.startswith("campaign:pause:"))
    async def pause_campaign(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        campaign_id = int(callback.data.split(":")[-1])
        db.update_campaign_status(campaign_id, CampaignStatus.PAUSED)
        await callback.answer("Кампания на паузе")

    @router.callback_query(F.data.startswith("campaign:stop:"))
    async def stop_campaign(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        campaign_id = int(callback.data.split(":")[-1])
        db.update_campaign_status(campaign_id, CampaignStatus.STOPPED)
        await callback.answer("Кампания остановлена")

    @router.callback_query(F.data == "blacklist")
    async def blacklist_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not await guard(callback):
            return
        await state.set_state(BlacklistStates.waiting_entry)
        values = db.get_blacklist_values()
        text = "Пришли username, ID или ссылку t.me для ЧС."
        if values:
            text += "\n\nСейчас в ЧС:\n" + "\n".join(f"• {item}" for item in values[:30])
        await callback.message.edit_text(text, reply_markup=back("main"))

    @router.message(BlacklistStates.waiting_entry)
    async def blacklist_add(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        db.add_blacklist_entry(value=message.text.strip(), reason="manual")
        await state.clear()
        await message.answer("Добавлено в ЧС.", reply_markup=main_menu())

    @router.callback_query(F.data == "logs")
    async def logs(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        logs = db.recent_delivery_logs()
        if not logs:
            text = "Логов пока нет."
        else:
            text = "<b>Последние отправки</b>\n" + "\n".join(
                f"• campaign #{item.campaign_id} -> {item.recipient_id}: {item.status}"
                for item in logs
            )
        await callback.message.edit_text(text, reply_markup=back("main"), parse_mode=ParseMode.HTML)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp


async def capture_message_payload(message: Message, media_dir: Path) -> dict[str, Any]:
    media_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    if message.html_text:
        payload["text"] = message.html_text
    if message.caption:
        payload["text"] = message.caption

    media_fields = {
        "photo": message.photo[-1].file_id if message.photo else None,
        "video": message.video.file_id if message.video else None,
        "audio": message.audio.file_id if message.audio else None,
        "document": message.document.file_id if message.document else None,
        "voice": message.voice.file_id if message.voice else None,
        "video_note": message.video_note.file_id if message.video_note else None,
        "sticker": message.sticker.file_id if message.sticker else None,
        "animation": message.animation.file_id if message.animation else None,
    }
    for media_type, file_id in media_fields.items():
        if not file_id:
            continue
        destination = media_dir / f"{uuid.uuid4().hex}_{media_type}"
        await message.bot.download(file_id, destination=destination)
        payload.setdefault(media_type, []).append({"path": str(destination), "file_name": destination.name})
    if not payload:
        raise ValueError("Не удалось сохранить сообщение")
    return payload
