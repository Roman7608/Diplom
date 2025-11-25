from aiogram.fsm.state import StatesGroup, State


class ConversationState(StatesGroup):
    greeting = State()           # ask name
    detect_intent = State()      # run router, decide branch
    collect_brand = State()      # ask for brand/model if missing
    collect_specs = State()      # ask for budget/specs
    collect_repair_type = State()# слесарный/кузовной
    collect_phone = State()      # ask & validate phone
    confirm = State()            # show summary + wait for yes/no
    confirm_final = State()      # process yes/no
    finished = State()           # end of dialog
    non_dealer_choice = State()  # выбор при недилерской марке

