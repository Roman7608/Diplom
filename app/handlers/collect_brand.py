from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from app.fsm.states import ConversationState
from app.utils.brand_matcher import BrandMatcher, DEALER_BRANDS
from loguru import logger

router = Router()


@router.message(ConversationState.collect_brand)
async def handle_collect_brand(
    message: Message,
    state: FSMContext,
    brand_matcher: BrandMatcher,
):
    """
    Collect brand/model from user and branch to next state.
    """
    
    text = message.text or ""
    logger.info(f"Collecting brand from: {text[:100]}")
    
    # Find brand
    found_brand = brand_matcher.find_brand(text)
    
    # Get current data
    data = await state.get_data()
    intent = data.get("intent")
    slots = data.get("slots", {})
    purchase_intent = data.get("purchase_intent", False)
    text_lower = text.lower()
    
    # Если это purchase_intent, парсим тип покупки и марку
    if purchase_intent:
        # Парсим тип: новый или с пробегом
        if any(kw in text_lower for kw in ["новый", "новая", "новое"]):
            intent = "buy_new"
            slots["buy_type"] = "new"
        elif any(kw in text_lower for kw in ["б/у", "бу", "б у", "с пробегом", "пробег", "подержанный"]):
            intent = "buy_used"
            slots["buy_type"] = "used"
        else:
            # По умолчанию считаем новым, если не указано
            intent = "buy_new"
            slots["buy_type"] = "new"
        
        data["intent"] = intent
        data["purchase_intent"] = False  # Очищаем флаг
    
    # Save brand and raw model text
    if intent in ["buy_new", "buy_used"]:
        data["target_brand"] = found_brand
    elif intent in ["repair", "spares", "sell"]:
        data["user_car_brand"] = found_brand
    
    slots["raw_model"] = text
    data["slots"] = slots
    await state.set_data(data)
    
    # Branch to next state
    if intent in ["buy_new", "buy_used"]:
        if not found_brand:
            await message.answer("Не удалось определить марку. Пожалуйста, укажите марку и модель автомобиля.")
            return
        
        # Проверка дилерских марок для buy_new
        if intent == "buy_new" and found_brand not in DEALER_BRANDS:
            data["non_dealer_brand"] = found_brand
            await state.set_data(data)
            await state.set_state(ConversationState.non_dealer_choice)
            # Здесь НЕ НУЖНО отправлять уведомление снова, если перешли из detect_intent, который уже мог показать или сейчас покажет
            # Но collect_brand - это когда бот явно спросил "Какую марку?".
            # Значит, уведомление нужно.
            await message.answer(
                f'Компания "АвтоЛидер" не может предложить Вам новый автомобиль {found_brand}. '
                f'Зато у нас есть новые автомобили Chery, Jetour и Haval.\n'
                f'Что из этого Вас интересует?\n'
                f'Если же Вы всё-таки хотите купить автомобиль {found_brand}, '
                f'могу организовать Вам звонок из отдела автомобилей с пробегом.'
            )
        elif not slots.get("budget_max"):
            await state.set_state(ConversationState.collect_specs)
            await message.answer("До какой суммы рассматриваете автомобиль?")
        else:
            await state.set_state(ConversationState.collect_phone)
            name = data.get("name", "Клиент")
            await message.answer(f"{name}, оставьте, пожалуйста, Ваш номер телефона, в течение 10 минут Вам перезвонит специалист.")
    
    elif intent == "repair":
        if not found_brand:
            await message.answer("Не удалось определить марку. Пожалуйста, укажите марку и модель автомобиля.")
            return
        
        if not slots.get("repair_type"):
            await state.set_state(ConversationState.collect_repair_type)
            await message.answer("Это слесарный ремонт (двигатель, подвеска, ТО) или кузовной (вмятины, покраска, после ДТП)?")
        else:
            await state.set_state(ConversationState.collect_phone)
            name = data.get("name", "Клиент")
            await message.answer(f"{name}, оставьте, пожалуйста, Ваш номер телефона, в течение 10 минут Вам перезвонит специалист.")
    
    elif intent in ["spares", "sell"]:
        if not found_brand:
            await message.answer("Не удалось определить марку. Пожалуйста, укажите марку и модель автомобиля.")
            return
        
        await state.set_state(ConversationState.collect_phone)
        name = data.get("name", "Клиент")
        await message.answer(f"{name}, оставьте, пожалуйста, Ваш номер телефона, в течение 10 минут Вам перезвонит специалист.")

