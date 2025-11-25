from typing import Optional
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from app.fsm.states import ConversationState
from app.llm.router import LLMRouter
from app.utils.brand_matcher import BrandMatcher
from app.utils.catalog import CarCatalog
from app.utils.semantic_search import SemanticCarIndex
from app.utils.text_parsers import is_search_query
from loguru import logger

router = Router()


PURCHASE_KEYWORDS = ["куп", "покуп", "интересует покуп", "новый", "с пробегом", "подобрать", "ищу", "нужен", "авто", "машин"]


@router.message(ConversationState.collect_repair_type)
async def handle_collect_repair_type(
    message: Message,
    state: FSMContext,
    router_llm: LLMRouter,
    brand_matcher: BrandMatcher,
    catalog: Optional[CarCatalog] = None,
    semantic_index: Optional[SemanticCarIndex] = None,
):
    """
    Collect repair type (слесарный/кузовной). Позволяет вернуться к подбору, если пользователь передумал.
    """
    text = message.text or ""
    logger.info(f"Collecting repair type from: {text[:100]}")
    
    text_lower = text.lower()
    
    if any(kw in text_lower for kw in PURCHASE_KEYWORDS) or is_search_query(text) or brand_matcher.find_brand(text):
        logger.info("User switched from repair to purchase flow while in collect_repair_type")
        await state.set_state(ConversationState.detect_intent)
        data = await state.get_data()
        data.pop("intent", None)
        await state.set_data(data)
        from app.handlers.detect_intent import handle_detect_intent
        await handle_detect_intent(message, state, router_llm, brand_matcher, catalog, semantic_index)
        return
    
    # Check if repair type was already set by detect_intent (e.g. oil change)
    data = await state.get_data()
    slots = data.get("slots", {})
    if slots.get("repair_type"):
        logger.info(f"Repair type already set: {slots['repair_type']}")
        # Проверяем бренд
        user_brand = data.get("user_car_brand")
        if user_brand:
             # Бренд уже есть, подтверждаем и просим телефон
             await message.answer(f"Понял, Вас интересует {slots.get('repair_details', 'обслуживание')}. Ваш автомобиль - {user_brand}?")
             # Здесь нужна логика ожидания "да/нет". Но collect_repair_type не предназначен для этого.
             # Проще сразу перейти к телефону, так как мы уже "поняли".
             # Или, как просил пользователь: "Понял, Вас интересует замена масла. Ваш автомобиль - Volkswagen?" -> Ждем ответа.
             # Это требует нового состояния ConfirmBrand или обработки "да" здесь.
             # Давайте обработаем "да" здесь же.
             await state.update_data(waiting_brand_confirmation=True)
             return # Ждем следующего сообщения
        else:
             # Бренда нет, спрашиваем
             await message.answer(f"Понял, Вас интересует {slots.get('repair_details', 'обслуживание')}. Какой у Вас автомобиль (марка)?")
             await state.update_data(waiting_brand_confirmation=True) # Используем тот же флаг, но ожидаем ввод марки
             return

    # Handle Yes/No for brand confirmation
    if data.get("waiting_brand_confirmation"):
        if text_lower in ["да", "верно", "ага", "yes"]:
            await state.update_data(waiting_brand_confirmation=False)
            
            if data.get("phone"):
                from app.handlers.confirm import ask_confirm
                await state.set_state(ConversationState.confirm)
                await ask_confirm(message, state)
                return

            await state.set_state(ConversationState.collect_phone)
            name = data.get("name", "Клиент")
            await message.answer(f"{name}, оставьте, пожалуйста, Ваш номер телефона, в течение 10 минут Вам перезвонит специалист.")
            return
        else:
            # Пользователь назвал другую марку или отказался
            # Пытаемся извлечь марку
            new_brand = brand_matcher.find_brand(text)
            if new_brand:
                await state.update_data(user_car_brand=new_brand, waiting_brand_confirmation=False)
                
                if data.get("phone"):
                    from app.handlers.confirm import ask_confirm
                    await state.set_state(ConversationState.confirm)
                    await ask_confirm(message, state)
                    return

                await state.set_state(ConversationState.collect_phone)
                name = data.get("name", "Клиент")
                await message.answer(f"{name}, оставьте, пожалуйста, Ваш номер телефона, в течение 10 минут Вам перезвонит специалист.")
                return
            else:
                # Не поняли марку, но считаем что он поправил
                # Просто идем дальше к телефону
                await state.update_data(waiting_brand_confirmation=False)
                
                if data.get("phone"):
                    from app.handlers.confirm import ask_confirm
                    await state.set_state(ConversationState.confirm)
                    await ask_confirm(message, state)
                    return

                await state.set_state(ConversationState.collect_phone)
                name = data.get("name", "Клиент")
                await message.answer(f"{name}, оставьте, пожалуйста, Ваш номер телефона, в течение 10 минут Вам перезвонит специалист.")
                return

    # Determine repair type
    repair_type = None
    if any(kw in text_lower for kw in ["слесарный", "двигатель", "подвеска", "то", "техобслуживание", "механика", "масло", "фильтр", "диагностика"]):
        repair_type = "слесарный"
    elif any(kw in text_lower for kw in ["кузовной", "вмятины", "покраска", "дтп", "авария", "кузов", "крыло", "бампер"]):
        repair_type = "кузовной"
    
    if repair_type:
        data = await state.get_data()
        slots = data.get("slots", {})
        slots["repair_type"] = repair_type
        data["slots"] = slots
        await state.set_data(data)
        
        if data.get("phone"):
            from app.handlers.confirm import ask_confirm
            await state.set_state(ConversationState.confirm)
            await ask_confirm(message, state)
            return
        
        await state.set_state(ConversationState.collect_phone)
        name = data.get("name", "Клиент")
        # Для сервиса более утвердительный ответ
        await message.answer(f"Да, мы можем это сделать. {name}, оставьте, пожалуйста, Ваш номер телефона, в течение 10 минут Вам перезвонит специалист.")
    else:
        await message.answer("Пожалуйста, укажите тип ремонта: слесарный (двигатель, подвеска, ТО) или кузовной (вмятины, покраска, после ДТП).")

