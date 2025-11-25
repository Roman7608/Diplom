import re
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from app.fsm.states import ConversationState
from app.utils.brand_matcher import BrandMatcher
from app.utils.catalog import CarCatalog
from app.utils.semantic_search import SemanticCarIndex
from app.handlers.non_dealer_choice import handle_non_dealer_choice
from app.llm.router import LLMRouter
from app.utils.text_parsers import is_search_query, is_power_query, is_expensive_query
from loguru import logger
from typing import Optional

router = Router()


@router.message(ConversationState.collect_specs)
async def handle_collect_specs(
    message: Message, 
    state: FSMContext,
    brand_matcher: BrandMatcher,
    router_llm: LLMRouter,
    catalog: Optional[CarCatalog] = None,
    semantic_index: Optional[SemanticCarIndex] = None
):
    """
    Collect budget and optional specs (body type, drive).
    If specs are collected, transition to search (non_dealer_choice) to show cars.
    """
    text = message.text or ""
    logger.info(f"Collecting specs from: {text[:100]}")

    # --- Escape Hatch: Intent Switch Check ---
    # Если пользователь задает новый вопрос вместо бюджета/кузова (например "самый мощный какой?", "Haval есть?")
    # Или упоминает другой бренд.
    # Важно не спутать "до 5 млн" с поисковым запросом. (В is_search_query есть проверка на параметры, это риск).
    # is_search_query возвращает True, если есть параметры (цена, кузов).
    # Нам нужно отличить "просто параметры" от "вопроса".
    # Если есть "какой", "есть", "мощный", "почем" - это вопрос.
    
    is_question = any(kw in text.lower() for kw in ["какой", "какие", "есть ли", "мощн", "дорог", "дешев", "почем", "сколько"])
    found_brand = brand_matcher.find_brand(text)
    
    if (is_question and not re.search(r'\d', text)) or found_brand: # Если есть вопрос без цифр (чтобы не спутать с "есть 5 млн") или бренд
         logger.info("Detected intent switch inside collect_specs. Redirecting to detect_intent.")
         await state.set_state(ConversationState.detect_intent)
         from app.handlers.detect_intent import handle_detect_intent
         await handle_detect_intent(message, state, router_llm, brand_matcher, catalog, semantic_index)
         return
    
    data = await state.get_data()
    slots = data.get("slots", {})
    
    # --- Budget Parsing ---
    budget = None
    
    # Сначала проверим "неважно/любой" для цены
    skip_keywords = ["неважно", "не важно", "любой", "любая", "нет", "пропусти", "далее", "без разницы", "потом", "не знаю"]
    if any(kw in text.lower() for kw in skip_keywords):
        budget = 100_000_000 # Ставим очень большой бюджет
        logger.info("User skipped budget. Setting 100M limit.")

    if not budget:
        # Поиск явного указания "до X" или просто числа
        budget_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:млн|миллион)', text, re.IGNORECASE)
        if budget_match:
            budget = float(budget_match.group(1).replace(',', '.')) * 1_000_000
        else:
            # Remove spaces from digits (e.g. "5 000 000" -> "5000000")
            clean_text = re.sub(r'(?<=\d)\s(?=\d)', '', text)
            numbers = re.findall(r'\d+(?:[.,]\d+)?', clean_text)
            for num_str in numbers:
                num = float(num_str.replace(',', '.'))
                if num > 100_000:  # Likely a budget
                    budget = int(num)
                    break
    
    # Retry counter check
    retry_count = data.get("specs_retry", 0)
    
    if budget:
        slots["budget_max"] = int(budget)
        logger.info(f"Extracted budget: {budget}")
        data["specs_retry"] = 0 # Reset on success
    elif not slots.get("budget_max"):
        # Только если бюджет еще не был установлен, считаем это попыткой ввода бюджета
        retry_count += 1
        data["specs_retry"] = retry_count
    
    # --- Body Type Parsing ---
    body_keywords = {
        "кроссовер": ["кроссовер", "кросс", "suv", "внедорожник", "джип", "паркетник"],
        "седан": ["седан", "sedan"],
        "хэтчбек": ["хэтчбек", "хетчбек", "hatchback", "хетч"],
        "универсал": ["универсал", "wagon", "вагон"],
        "пикап": ["пикап", "pickup"],
        "купе": ["купе", "coupe"],
        "кабриолет": ["кабриолет", "cabriolet", "кабрио"],
        "лифтбек": ["лифтбек", "liftback"],
        "минивэн": ["минивэн", "minivan", "вэн"],
    }
    text_lower = text.lower()
    
    if any(kw in text_lower for kw in ["можно разные", "любой", "не важно", "без разницы", "все равно"]):
        slots["body"] = "любой"
    else:
        for body_type, keywords in body_keywords.items():
            if any(kw in text_lower for kw in keywords):
                slots["body"] = body_type
                break
    
    # --- Drive Type Parsing ---
    drive_keywords = {
        "4x4": ["4x4", "полный", "полный привод", "awd", "4wd"],
        "передний": ["передний", "передний привод", "fwd"],
        "задний": ["задний", "задний привод", "rwd"],
    }
    for drive_type, keywords in drive_keywords.items():
        if any(kw in text_lower for kw in keywords):
            slots["drive"] = drive_type
            break

    # Сохраняем слоты
    data["slots"] = slots
    await state.set_data(data)

    # --- Logic Flow ---
    
    has_budget = slots.get("budget_max") is not None
    has_body = slots.get("body") is not None
    
    # Если бюджет не найден после 2 попыток - ставим безлимит
    if not has_budget and retry_count >= 2:
        slots["budget_max"] = 100_000_000
        has_budget = True
        logger.info("Budget retry limit reached. Setting max budget.")
    
    # Если бюджет есть (или пропущен), проверяем кузов
    if has_budget:
        if not has_body:
             # Простейшая логика: если кузова нет, спрашиваем.
             await message.answer(
                "Нужен какой-то определённый тип кузова (кроссовер, седан, пикап) или можно разные варианты?"
             )
             return

        # Если все собрали: Бюджет и Кузов есть.
        # Переходим в режим поиска (non_dealer_choice умеет искать по слотам)
        
        search_query_parts = []
        if slots.get("body") and slots["body"] != "любой":
            search_query_parts.append(slots["body"])
        if slots.get("drive"):
            search_query_parts.append(f"{slots['drive']} привод")
        if slots.get("budget_max") and slots["budget_max"] < 100_000_000:
            search_query_parts.append(f"до {slots['budget_max']} рублей")
        
        # Добавляем бренд
        target_brand = data.get("target_brand")
        if target_brand:
            search_query_parts.append(target_brand)
            
        search_text = " ".join(search_query_parts)
        
        # Важно: переключаем стейт
        await state.set_state(ConversationState.non_dealer_choice)
        
        # Создаем копию сообщения с параметрами
        new_message = message.model_copy(update={"text": search_text})
        
        logger.info(f"Redirecting to search with synthesized query: {search_text}")
        await handle_non_dealer_choice(new_message, state, brand_matcher, catalog, semantic_index)
        return

    else:
        # Нет бюджета
        data["slots"] = slots # save updates
        await state.set_data(data)
        await message.answer("Пожалуйста, укажите бюджет (например, 'до 2.5 млн' или '2500000 рублей').")
