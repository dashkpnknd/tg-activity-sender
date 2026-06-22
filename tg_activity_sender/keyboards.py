from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Аккаунты", callback_data="accounts")],
            [InlineKeyboardButton(text="Цепочки", callback_data="sequences")],
            [InlineKeyboardButton(text="Кампании", callback_data="campaigns")],
            [InlineKeyboardButton(text="ЧС", callback_data="blacklist")],
            [InlineKeyboardButton(text="Логи", callback_data="logs")],
        ]
    )


def accounts_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить по QR", callback_data="account:add_qr")],
            [InlineKeyboardButton(text="Список аккаунтов", callback_data="account:list")],
            [InlineKeyboardButton(text="Папки чатов", callback_data="account:folders")],
            [InlineKeyboardButton(text="Назад", callback_data="main")],
        ]
    )


def campaigns_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать кампанию", callback_data="campaign:create")],
            [InlineKeyboardButton(text="Активные", callback_data="campaign:list_running")],
            [InlineKeyboardButton(text="Все кампании", callback_data="campaign:list_all")],
            [InlineKeyboardButton(text="Назад", callback_data="main")],
        ]
    )


def sequences_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать цепочку", callback_data="sequence:create")],
            [InlineKeyboardButton(text="Список цепочек", callback_data="sequence:list")],
            [InlineKeyboardButton(text="Назад", callback_data="main")],
        ]
    )


def back(callback_data: str = "main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data=callback_data)]]
    )
