from typing import Optional
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from app.fsm.states import ConversationState
from app.llm.router import LLMRouter
from app.utils.brand_matcher import BrandMatcher
from app.utils.catalog import CarCatalog
from app.utils.semantic_search import SemanticCarIndex
from app.utils.text_parsers import is_search_query, is_expensive_query
from app.handlers.detect_intent import handle_detect_intent
from loguru import logger

router = Router()


@router.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    """
    /start handler - clears FSM and asks for name.
    """
    await state.clear()
    await state.set_state(ConversationState.greeting)
    
    logger.info(f"User {message.from_user.id} started conversation")
    
    await message.answer("Компания Автолидер приветствует Вас! Как к Вам обращаться?")


@router.message(F.text == "/id")
async def cmd_id(message: Message):
    """
    Diagnostic command to get chat ID.
    """
    chat_id = message.chat.id
    title = message.chat.title or "Private Chat"
    logger.info(f"📢 Chat ID request from '{title}': {chat_id}")
    try:
        await message.answer(f"Chat ID: {chat_id}")
    except Exception as e:
        logger.error(f"Could not send chat ID: {e}")


PURCHASE_KEYWORDS = [
    "куп", "покуп", "ищу", "нужен", "интересует", "хочу", "подобрать", "что есть", "можно купить",
    "шиномонтаж", "переобуть", "переобувка", "то ", "то,"
]


def _split_name_and_query(text: str, brand_matcher: BrandMatcher) -> tuple[str, Optional[str]]:
    stripped = (text or "").strip()
    if not stripped:
        return "", None
    
    separators = [".", "!", "?", "\n", ",", ";", ":"]
    for sep in separators:
        if sep in stripped:
            name_part, tail = stripped.split(sep, 1)
            name_part = name_part.strip()
            tail = tail.strip()
            if name_part and tail and _looks_like_query(tail, brand_matcher):
                return name_part, tail
            if name_part:
                return name_part, tail or None
    
    tokens = stripped.split(maxsplit=1)
    if len(tokens) == 2:
        name_part, tail = tokens[0].strip(), tokens[1].strip()
        if name_part and tail and _looks_like_query(tail, brand_matcher):
            return name_part, tail
    
    return stripped, None


def _looks_like_query(text: str, brand_matcher: BrandMatcher) -> bool:
    lowered = text.lower()
    if any(kw in lowered for kw in PURCHASE_KEYWORDS):
        return True
    if is_search_query(text) or is_expensive_query(text):
        return True
    if brand_matcher.find_brand(text):
        return True
    if any(kw in lowered for kw in ["ремонт", "сервис", "запчаст", "шиномонтаж", "переобу", "то "]):
        return True
    return False


@router.message(ConversationState.greeting)
async def handle_greeting(
    message: Message,
    state: FSMContext,
    router_llm: LLMRouter,
    brand_matcher: BrandMatcher,
    catalog: Optional[CarCatalog] = None,
    semantic_index: Optional[SemanticCarIndex] = None,
):
    """
    Handle greeting state - save name and move to detect_intent.
    """
    name, possible_query = _split_name_and_query(message.text or "", brand_matcher)
    if not name.strip():
        await message.answer("Пожалуйста, укажите Ваше имя.")
        return
    
    await state.update_data(name=name.strip())
    await state.set_state(ConversationState.detect_intent)
    
    logger.info(f"User {message.from_user.id} provided name: {name[:50]}")
    
    if possible_query and _looks_like_query(possible_query, brand_matcher):
        logger.info("Greeting contained immediate request -> forwarding to detect_intent")
        forwarded_message = message.model_copy(update={"text": possible_query})
        await handle_detect_intent(forwarded_message, state, router_llm, brand_matcher, catalog, semantic_index)
        return
    
    await message.answer("Что Вас интересует: покупка/продажа авто, ремонт, запчасти, бухгалтерия?")


@router.message(ConversationState.finished)
async def handle_finished(
    message: Message, 
    state: FSMContext,
    router_llm: LLMRouter,
    brand_matcher: BrandMatcher,
    catalog: Optional[CarCatalog] = None,
    semantic_index: Optional[SemanticCarIndex] = None,
):
    """
    Handle finished state - if user writes something, treat it as a new request.
    """
    if message.text == "/start":
        await state.clear()
        await state.set_state(ConversationState.greeting)
        await message.answer("Компания Автолидер приветствует Вас! Как к Вам обращаться?")
        return

    # Сохраняем имя и ТЕЛЕФОН из старого контекста, если есть
    data = await state.get_data()
    old_name = data.get("name")
    old_phone = data.get("phone")
    
    # Начинаем новый диалог
    await state.clear()
    new_data = {}
    if old_name: new_data["name"] = old_name
    if old_phone: new_data["phone"] = old_phone
    
    if new_data:
        await state.set_data(new_data)
    
    # Переходим сразу к определению интента
    await state.set_state(ConversationState.detect_intent)
    
    # Пробуем обработать как запрос
    await handle_detect_intent(message, state, router_llm, brand_matcher, catalog, semantic_index)
