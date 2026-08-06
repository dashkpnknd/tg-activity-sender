from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_activity_sender.core import ActivityMode, CampaignStatus
from tg_activity_sender.db import Database, display_blacklist_value
from tg_activity_sender.keyboards import accounts_menu, back, campaigns_menu, main_menu, sequences_menu
from tg_activity_sender.states import AccountStates, BlacklistStates, CampaignStates, SequenceStates
from tg_activity_sender.telegram_accounts import AccountManager, DeliveryClient, TwoFactorPasswordRequired


LOCALTRAFFIC_CHAT_TEXT = (
    "МЫ СОЗДАЛИ НЕЙРО-ПРОДАВЦА, который увеличит конверсию в продажу в 3 раза "
    "и кратно сократит ваши расходы! 😋\n"
    "- Бот работает в разы дешевле и эффективнее людей!\n"
    "- Дожимает каждого лида до талого!\n"
    "- Знает все актуальные цены и наличие!\n"
    "...Вам актуально, чтобы бот заменил ваших менеджеров по продажам?"
)

LOCALTRAFFIC_PRIVATE_TEXT = (
    "Доброго дня, пишу, тк мы подключаем всем магазинам, с кем работаем нейро-продавца\n\n"
    "Он обрабатывает заявки в 3 раза лучше и быстрее менеджера\n\n"
    "Ваш проект подключать?"
)

DEFAULT_TEAM_IDENTIFIERS = [
    "Ксюша",
    "Ksenia_LocalLead",
    "Оксана",
    "Даниил",
    "localTraffic",
    "LocalTraffic",
    "local трафик",
]


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

    @router.callback_query(F.data == "account:folders")
    async def list_folders(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        accounts = db.list_accounts()
        if not accounts:
            await callback.answer("Сначала добавь аккаунт.", show_alert=True)
            return
        await callback.message.edit_text("Читаю папки первого подключенного аккаунта...")
        client = await account_manager.client_for(accounts[0].session_path)
        try:
            folders = await DeliveryClient(client).list_folders()
        finally:
            await client.disconnect()
        if not folders:
            text = "Папки не найдены или Telegram не отдал список. В кампании можно указать '-' для всех групп."
        else:
            text = "<b>Папки аккаунта</b>\n" + "\n".join(
                f"• <code>{folder_id}</code> — {title}" for folder_id, title in folders
            )
        await callback.message.answer(text, reply_markup=back("accounts"), parse_mode=ParseMode.HTML)

    @router.callback_query(F.data == "account:add_qr")
    async def add_account_qr(callback: CallbackQuery, state: FSMContext) -> None:
        if not await guard(callback):
            return
        token = "<REDACTED>"
        await callback.message.edit_text("Создаю QR для входа...")
        try:
            ticket = await account_manager.begin_qr_login(token)
        except Exception as exc:
            await callback.message.answer(
                "Не удалось создать QR для входа.\n\n"
                f"Ошибка: <code>{str(exc)[:800]}</code>\n\n"
                "Чаще всего это значит, что сервер не может подключиться к Telegram напрямую; "
                "я включил поддержку TELEGRAM_PROXY_URL.",
                parse_mode=ParseMode.HTML,
            )
            return
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
        except TwoFactorPasswordRequired:
            await state.set_state(AccountStates.waiting_2fa_password)
            await state.update_data(qr_token=token)
            await callback.message.answer(
                "QR принят, но на аккаунте включён облачный пароль Telegram.\n"
                "Отправь 2FA-пароль следующим сообщением."
            )
        except Exception as exc:
            await callback.message.answer(f"Не удалось добавить аккаунт: {exc}")

    @router.message(AccountStates.waiting_2fa_password)
    async def account_2fa_password(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        data = await state.get_data()
        token = "<REDACTED>"
        if not token:
            await state.clear()
            await message.answer("Не нашёл активную QR-авторизацию. Начни добавление аккаунта заново.")
            return
        try:
            telegram_id = await account_manager.finish_2fa_login(token, message.text or "")
        except Exception as exc:
            await message.answer(
                "Не удалось завершить вход с 2FA-паролем.\n\n"
                f"Ошибка: <code>{str(exc)[:500]}</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(f"Аккаунт <code>{telegram_id}</code> добавлен.", parse_mode=ParseMode.HTML)

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
        name = (message.text or "").strip()
        if not name:
            await message.answer("Название цепочки не должно быть пустым.")
            return
        try:
            sequence = db.create_sequence(name)
        except Exception as exc:
            await message.answer(
                "Не удалось создать цепочку. Возможно, такая цепочка уже есть.\n\n"
                f"Ошибка: <code>{str(exc)[:500]}</code>",
                parse_mode=ParseMode.HTML,
            )
            await state.clear()
            return
        await state.update_data(sequence_id=sequence.id)
        await state.set_state(SequenceStates.waiting_step)
        await message.answer("Цепочка создана. Пришли первый шаг: текст, фото, видео, документ, голосовое или кружок.")

    @router.message(SequenceStates.waiting_step)
    async def add_sequence_step(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        data = await state.get_data()
        sequence_id = int(data["sequence_id"])
        try:
            steps = db.get_sequence_steps(sequence_id)
            payload = await asyncio.wait_for(capture_message_payload(message, media_dir), timeout=90)
            db.add_sequence_step(sequence_id, order=len(steps) + 1, payload=payload, delay_after_seconds=0)
        except asyncio.TimeoutError:
            await message.answer("Не удалось сохранить шаг: Telegram слишком долго отдавал файл/кружок. Попробуй отправить его ещё раз.")
            return
        except Exception as exc:
            await message.answer(
                "Не удалось сохранить шаг цепочки.\n\n"
                f"Ошибка: <code>{str(exc)[:500]}</code>",
                parse_mode=ParseMode.HTML,
            )
            return
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

    @router.callback_query(F.data == "campaign:template_localtraffic")
    async def template_localtraffic(callback: CallbackQuery) -> None:
        if not await guard(callback):
            return
        if not db.find_account_by_username("localTraffic"):
            await callback.answer("Сначала добавь аккаунт @localTraffic по QR.", show_alert=True)
            return
        chat_sequence = ensure_sequence_with_text(db, "localTraffic: чат cold", LOCALTRAFFIC_CHAT_TEXT)
        private_sequence = ensure_sequence_with_text(db, "localTraffic: ЛС cold", LOCALTRAFFIC_PRIVATE_TEXT)
        campaign = db.create_campaign(
            name=f"localTraffic cold {uuid.uuid4().hex[:6]}",
            sequence_id=chat_sequence.id,
            chat_sequence_id=chat_sequence.id,
            private_sequence_id=private_sequence.id,
            source_folder="ВСЕ КЛИЕНТЫ",
            source_account_username="localTraffic",
            target_segment="cold",
            dedupe_key="neuro-seller-localtraffic",
            team_identifiers=DEFAULT_TEAM_IDENTIFIERS,
            segment_window_days=30,
            segment_min_non_team_messages=5,
            activity_mode=ActivityMode.BOTH,
            days_threshold=9999,
            include_chats=True,
            include_private=True,
            schedule_window="10:00-20:00",
            delay_between_recipients_seconds=300,
        )
        await callback.message.edit_text(
            "Шаблон создан.\n\n"
            f"Кампания: #{campaign.id}\n"
            f"Чатовая цепочка: #{chat_sequence.id}\n"
            f"ЛС-цепочка: #{private_sequence.id}\n\n"
            "Добавь кружок вторым шагом в обе цепочки, потом запускай кампанию из списка.",
            reply_markup=back("campaigns"),
        )

    @router.callback_query(F.data == "campaign:create")
    async def campaign_create_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not await guard(callback):
            return
        sequences = db.list_sequences()
        if not sequences:
            await callback.answer("Сначала создай цепочку сообщений.", show_alert=True)
            return
        await state.set_state(CampaignStates.waiting_name)
        await callback.message.edit_text("Напиши название кампании.", reply_markup=back("campaigns"))

    @router.message(CampaignStates.waiting_name)
    async def campaign_name(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.update_data(name=message.text.strip())
        sequences = db.list_sequences()
        text = "<b>Цепочки</b>\n" + "\n".join(f"#{item.id} {item.name}" for item in sequences)
        await state.set_state(CampaignStates.waiting_chat_sequence)
        await message.answer(f"{text}\n\nВведи ID цепочки, которая уйдет в сам чат.", parse_mode=ParseMode.HTML)

    @router.message(CampaignStates.waiting_chat_sequence)
    async def campaign_chat_sequence(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        if not message.text.isdigit():
            await message.answer("Нужен ID цепочки числом.")
            return
        sequence_id = int(message.text)
        if sequence_id not in {item.id for item in db.list_sequences()}:
            await message.answer("Такой цепочки нет. Введи ID из списка выше.")
            return
        await state.update_data(chat_sequence_id=sequence_id)
        await state.set_state(CampaignStates.waiting_private_sequence)
        await message.answer("Теперь введи ID цепочки, которая уйдет в ЛС участникам беседы.")

    @router.message(CampaignStates.waiting_private_sequence)
    async def campaign_private_sequence(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        if not message.text.isdigit():
            await message.answer("Нужен ID цепочки числом.")
            return
        sequence_id = int(message.text)
        if sequence_id not in {item.id for item in db.list_sequences()}:
            await message.answer("Такой цепочки нет. Введи ID из списка выше.")
            return
        await state.update_data(private_sequence_id=sequence_id)
        accounts = db.list_accounts()
        account_lines = "\n".join(
            f"• @{item.username}" for item in accounts if item.enabled and item.username
        ) or "Нет добавленных аккаунтов."
        await state.set_state(CampaignStates.waiting_account)
        await message.answer(
            "С какого аккаунта слать? Пришли username без разницы с @ или без.\n"
            "Если можно с любого, отправь -.\n\n"
            f"{account_lines}"
        )

    @router.message(CampaignStates.waiting_account)
    async def campaign_account(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        raw = message.text.strip()
        if raw != "-" and not db.find_account_by_username(raw):
            await message.answer("Такого аккаунта нет в боте. Пришли username из списка или -.")
            return
        await state.update_data(source_account_username=None if raw == "-" else raw)
        await state.set_state(CampaignStates.waiting_folder)
        await message.answer(
            "Введи папку чатов: точное название или ID из раздела «Аккаунты -> Папки чатов».\n"
            "Если нужно пройтись по всем групповым чатам, отправь -."
        )

    @router.message(CampaignStates.waiting_folder)
    async def campaign_folder(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        folder = message.text.strip()
        await state.update_data(source_folder=None if folder == "-" else folder)
        await state.set_state(CampaignStates.waiting_activity_mode)
        await message.answer(
            "Как отбирать чаты?\n"
            "1 - где было общение за последние N дней\n"
            "2 - где не было общения больше N дней"
        )

    @router.message(CampaignStates.waiting_activity_mode)
    async def campaign_activity_mode(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        raw = message.text.strip()
        if raw not in {"1", "2"}:
            await message.answer("Отправь 1 или 2.")
            return
        await state.update_data(activity_mode=ActivityMode.ACTIVE if raw == "1" else ActivityMode.INACTIVE)
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
        await state.update_data(schedule_window=message.text.strip())
        await state.set_state(CampaignStates.waiting_dedupe_key)
        await message.answer(
            "Введи ключ антидубля. Кампании с одинаковым ключом не будут повторно писать тем же чатам и людям.\n"
            "Например: neuro-seller"
        )

    @router.message(CampaignStates.waiting_dedupe_key)
    async def campaign_dedupe_key(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        await state.update_data(dedupe_key=message.text.strip())
        await state.set_state(CampaignStates.waiting_avito_exclusion)
        await message.answer(
            "Включить защиту от действующих Avito-клиентов?\n"
            "1 — да: исключить активные Avito-чаты за 5 дней и связанные с ними чаты\n"
            "2 — нет"
        )

    @router.message(CampaignStates.waiting_avito_exclusion)
    async def campaign_avito_exclusion(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        if message.text.strip() not in {"1", "2"}:
            await message.answer("Отправь 1 или 2.")
            return
        enabled = message.text.strip() == "1"
        await state.update_data(avito_exclusion_enabled=enabled)
        if not enabled:
            await create_campaign_from_state(message, state, avito_client_names=[])
            return
        await state.set_state(CampaignStates.waiting_avito_clients)
        await message.answer(
            "Пришли дополнительный список действующих клиентов Avito: названия проектов через запятую, "
            "каждый с новой строки или '-'. Например: Apple Market, AppShop."
        )

    @router.message(CampaignStates.waiting_avito_clients)
    async def campaign_avito_clients(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        raw = message.text.strip()
        names = [] if raw == "-" else [item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()]
        await create_campaign_from_state(message, state, avito_client_names=names)

    async def create_campaign_from_state(message: Message, state: FSMContext, *, avito_client_names: list[str]) -> None:
        data = await state.get_data()
        campaign = db.create_campaign(
            name=data["name"],
            sequence_id=int(data["chat_sequence_id"]),
            chat_sequence_id=int(data["chat_sequence_id"]),
            private_sequence_id=int(data["private_sequence_id"]),
            source_folder=data.get("source_folder"),
            source_account_username=data.get("source_account_username"),
            dedupe_key=data["dedupe_key"],
            activity_mode=data["activity_mode"],
            days_threshold=int(data["days"]),
            include_chats=True,
            include_private=True,
            schedule_window=data["schedule_window"],
            delay_between_recipients_seconds=300,
            team_identifiers=DEFAULT_TEAM_IDENTIFIERS,
            avito_exclusion_enabled=bool(data.get("avito_exclusion_enabled")),
            avito_activity_days=5,
            avito_client_names=avito_client_names,
        )
        await state.clear()
        await message.answer(
            f"Кампания #{campaign.id} создана: папка чатов -> сообщение в чат -> отдельное сообщение участникам.\n"
            f"Ключ антидубля: {campaign.dedupe_key or '-'}\n"
            f"Защита Avito: {'включена (5 дней)' if campaign.avito_exclusion_enabled else 'выключена'}"
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
                lines.append(
                    f"• #{item.id} {item.name}: {item.status}; "
                    f"чат seq #{item.chat_sequence_id or item.sequence_id}, "
                    f"ЛС seq #{item.private_sequence_id or item.sequence_id}, "
                    f"папка: {item.source_folder or 'все группы'}, "
                    f"акк: @{item.source_account_username or 'любой'}, "
                    f"сегмент: {item.target_segment}, "
                    f"Avito-защита: {'да' if item.avito_exclusion_enabled else 'нет'}, "
                    f"ключ: {item.dedupe_key or '-'}"
                )
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
        text = blacklist_text(db)
        await callback.message.edit_text(text, reply_markup=back("main"))

    @router.message(BlacklistStates.waiting_entry)
    async def blacklist_add(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        db.add_blacklist_entry(value=message.text.strip(), reason="manual")
        await state.set_state(BlacklistStates.waiting_entry)
        await message.answer(blacklist_text(db, prefix="Добавлено в ЧС."), reply_markup=back("main"))

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

    @router.message()
    async def unhandled_message(message: Message, state: FSMContext) -> None:
        if not await guard(message):
            return
        current_state = await state.get_state()
        if current_state is not None:
            await message.answer("Я не смог обработать этот шаг. Нажми /start и начни этот раздел заново.")
            await state.clear()
            return
        await message.answer("Я сейчас не жду текст. Нажми /start и выбери нужный раздел.")

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
        await asyncio.wait_for(message.bot.download(file_id, destination=destination), timeout=60)
        payload.setdefault(media_type, []).append({"path": str(destination), "file_name": destination.name})
    if not payload:
        raise ValueError("Не удалось сохранить сообщение")
    return payload


def ensure_sequence_with_text(db: Database, name: str, text: str):
    sequence = db.find_sequence_by_name(name)
    if sequence is None:
        sequence = db.create_sequence(name)
    if not db.get_sequence_steps(sequence.id):
        db.add_sequence_step(
            sequence.id,
            order=1,
            payload={"text": text},
            delay_after_seconds=0,
        )
    return sequence


def blacklist_text(db: Database, *, prefix: str = "") -> str:
    values = db.get_blacklist_values()
    text = "Пришли username, ID или ссылку t.me для ЧС."
    if prefix:
        text = f"{prefix}\n\n{text}"
    if values:
        formatted = [display_blacklist_value(item) for item in values[:30]]
        text += "\n\nСейчас в ЧС:\n" + "\n".join(f"• {item}" for item in formatted)
    return text
