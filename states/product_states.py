# states/product_states.py

from aiogram.fsm.state import State, StatesGroup


class AddProduct(StatesGroup):
    name = State()
    icon = State()
    category = State()
    price = State()
    description = State()
    delivery_type = State()
    delivery_instruction = State()
    preorder = State()
    bulk_pricing = State()
    accounts = State()


class EditProduct(StatesGroup):
    select_product = State()
    edit_field = State()
    new_value = State()


class AddStock(StatesGroup):
    select_product = State()
    accounts = State()


class DeleteProduct(StatesGroup):
    confirm = State()


class AddAccounts(StatesGroup):
    select_product = State()
    accounts = State()


class EditAccounts(StatesGroup):
    select_product = State()
    accounts = State()
    replace_accounts = State()


class EditBulkPricing(StatesGroup):
    select_product = State()
    pricing = State()
    waiting_input = State()       # ← ADD THIS