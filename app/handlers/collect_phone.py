import re
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from app.fsm.states import ConversationState
from app.utils.phone import normalize_phone
from app.utils.brand_matcher import BrandMatcher
from app.llm.router import LLMRouter
from loguru import logger

router = Router()


@router.message(ConversationState.collect_phone)
async def handle_collect_phone(
    message: Message, 
    state: FSMContext,
    router_llm: LLMRouter,
    brand_matcher: BrandMatcher
):
    """
    Collect and validate phone number.
    Supports text input and Contact object.
    """
    # 0. Handle Contact object (e.g. from "Share Contact" button)
    if message.contact:
        raw_phone = message.contact.phone_number
        logger.info(f"Received contact object: {raw_phone}")
        normalized = normalize_phone(raw_phone)
        
        if normalized:
            logger.info(f"Phone recognized from contact: {normalized}")
            data = await state.get_data()
            data["phone"] = normalized
            data["phone_attempts"] = 0
            await state.set_data(data)
            
            from app.handlers.confirm import ask_confirm
            await state.set_state(ConversationState.confirm)
            await ask_confirm(message, state)
            return
        else:
            await message.answer("Не удалось распознать номер из контакта.")
            return

    text = message.text or ""
    text_lower = text.lower()
    logger.info(f"Collecting phone from text: {text[:50]}")
    
    # 0.1 Check if user says "already provided"
    has_action_kw = any(kw in text_lower for kw in ["оставлял", "давал", "писал", "уже есть", "знаете", "сообщал", "оставил", "дал", "написал"])
    has_object_kw = any(kw in text_lower for kw in ["номер", "телефон", "контакт"])
    
    if has_action_kw and (has_object_kw or len(text.split()) <= 4):
        data = await state.get_data()
        if data.get("phone"):
            logger.info("User indicated phone already provided. Proceeding to confirm.")
            from app.handlers.confirm import ask_confirm
            await state.set_state(ConversationState.confirm)
            await ask_confirm(message, state)
            return
        else:
            await message.answer("Извините, не нашел Ваш номер в текущей сессии. Пожалуйста, напишите его.")
            return

    # 1. Normalize phone from text
    normalized = normalize_phone(text)
    
    if normalized:
        logger.info(f"Phone recognized from text: {normalized}")
        data = await state.get_data()
        data["phone"] = normalized
        data["phone_attempts"] = 0
        await state.set_data(data)
        
        from app.handlers.confirm import ask_confirm
        await state.set_state(ConversationState.confirm)
        await ask_confirm(message, state)
        return

    # 2. If not phone, check if it's a query/question
    is_phone_attempt = False
    digit_count = sum(c.isdigit() for c in text)
    if digit_count >= 7:
        is_phone_attempt = True
    
    has_cyrillic = bool(re.search(r'[а-яА-Я]', text))
    
    keywords = ["цена", "стоит", "наличи", "купит", "авто", "машин", "haval", "chery", "jetour", "tiggo", "pro", "max", "кроссовер", "привод"]
    has_keyword = any(kw in text.lower() for kw in keywords)
    
    if not is_phone_attempt and (has_cyrillic or has_keyword or len(text) > 20):
        logger.info("Input does not look like a phone, treating as query.")
        await state.set_state(ConversationState.detect_intent)
        from app.handlers.detect_intent import handle_detect_intent
        await handle_detect_intent(message, state, router_llm, brand_matcher)
        return

    # 3. Failed attempt
    data = await state.get_data()
    phone_attempts = data.get("phone_attempts", 0)
    phone_attempts += 1
    data["phone_attempts"] = phone_attempts
    await state.set_data(data)
    
    if phone_attempts >= 3:
        await message.answer("Не удалось распознать номер телефона. Пожалуйста, напишите /start для начала нового запроса.")
        await state.set_state(ConversationState.finished)
    else:
        await message.answer("Не удалось распознать номер телефона. Пожалуйста, укажите номер в формате +7XXXXXXXXXX или 8XXXXXXXXXX.")
