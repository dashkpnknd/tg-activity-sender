from aiogram.fsm.state import State, StatesGroup


class SequenceStates(StatesGroup):
    waiting_name = State()
    waiting_step = State()


class AccountStates(StatesGroup):
    waiting_2fa_password = State()


class CampaignStates(StatesGroup):
    waiting_name = State()
    waiting_chat_sequence = State()
    waiting_private_sequence = State()
    waiting_folder = State()
    waiting_activity_mode = State()
    waiting_days = State()
    waiting_schedule = State()


class BlacklistStates(StatesGroup):
    waiting_entry = State()
