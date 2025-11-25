import re
from typing import Optional, List, Dict, Any
from dataclasses import asdict
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from app.fsm.states import ConversationState
from app.utils.brand_matcher import BrandMatcher, DEALER_BRANDS
from app.utils.catalog import CarCatalog, pick_top3_offers, Car
from app.utils.semantic_search import SemanticCarIndex
from app.utils.text_parsers import parse_car_specs, is_power_query, is_search_query, is_expensive_query
from app.utils.response_helpers import format_model_response, build_non_dealer_prompt, format_color_overview
from app.utils.phone import normalize_phone
from loguru import logger

PHONE_PROMPT = "Оставьте свой номер телефона, Вам в течение 10 минут позвонит специалист."

def get_phone_prompt(has_phone: bool) -> str:
    if has_phone:
        return 'Хотите поговорить со специалистом? ответьте "ДА", и он перезвонит Вам в течение 10 минут'
    return PHONE_PROMPT

COLOR_WORDS = {
    "белый": ["белый", "белая", "белое", "белые", "белом", "белую"],
    "черный": ["черный", "черная", "черное", "черные", "черном", "черную", "чёрный", "чёрная", "чёрное", "чёрные", "чёрном", "чёрную"],
    "серый": ["серый", "серая", "серое", "серые", "сером", "серую"],
    "синий": ["синий", "синяя", "синее", "синие", "синем", "синюю"],
    "голубой": ["голубой", "голубая", "голубое", "голубые", "голубом", "голубую"],
    "красный": ["красный", "красная", "красное", "красные", "красном", "красную"],
    "зеленый": ["зеленый", "зеленая", "зеленое", "зеленые", "зеленом", "зеленую", "зелёный", "зелёная", "зелёное", "зелёные", "зелёном", "зелёную"],
    "желтый": ["желтый", "желтая", "желтое", "желтые", "желтом", "желтую", "жёлтый", "жёлтая", "жёлтое", "жёлтые", "жёлтом", "жёлтую"],
    "оранжевый": ["оранжевый", "оранжевая", "оранжевое", "оранжевые", "оранжевом", "оранжевую"],
    "фиолетовый": ["фиолетовый", "фиолетовая", "фиолетовое", "фиолетовые", "фиолетовом", "фиолетовую"],
    "розовый": ["розовый", "розовая", "розовое", "розовые", "розовом", "розовую"],
    "бордовый": ["бордовый", "бордовая", "бордовое", "бордовые", "бордовом", "бордовую"],
    "коричневый": ["коричневый", "коричневая", "коричневое", "коричневые", "коричневом", "коричневую"],
    "бежевый": ["бежевый", "бежевая", "бежевое", "бежевые", "бежевом", "бежевую"],
    "серебристый": ["серебристый", "серебристая", "серебристое", "серебристые", "серебристом", "серебристую"],
    "золотой": ["золотой", "золотая", "золотое", "золотые", "золотом", "золотую"],
}


def detect_requested_color(text_lower: str) -> Optional[str]:
    normalized = text_lower.replace("ё", "е")
    for color, variants in COLOR_WORDS.items():
        for variant in variants:
            if variant in normalized:
                return color
    return None


router = Router()

@router.message(ConversationState.non_dealer_choice)
async def handle_non_dealer_choice(
    message: Message,
    state: FSMContext,
    brand_matcher: BrandMatcher,
    catalog: Optional[CarCatalog] = None,
    semantic_index: Optional[SemanticCarIndex] = None,
):
    """
    Обрабатывает запросы на подбор авто и контекстные уточнения.
    """
    text = message.text or ""
    logger.info(f"Processing non_dealer_choice/search: {text[:100]}")
    
    data = await state.get_data()
    non_dealer_brand = data.get("non_dealer_brand")
    non_dealer_notice_sent = data.get("non_dealer_notice_sent", False)
    last_results: List[Dict[str, Any]] = data.get("last_search_results", [])
    last_price_max: Optional[int] = data.get("last_price_max")
    text_lower = text.lower()
    
    has_phone = bool(data.get("phone"))
    current_phone_prompt = get_phone_prompt(has_phone)
    
    # --- 0. IMMEDIATE PHONE CHECK ---
    # Проверяем, не ввел ли пользователь номер телефона (даже если мы в режиме поиска)
    potential_phone = normalize_phone(text)
    if potential_phone:
        logger.info(f"Phone number detected in search mode: {potential_phone}")
        data["phone"] = potential_phone
        await state.set_data(data)
        
        # Корректировка роутинга (если до этого показывали дилерские авто)
        if last_results and len(last_results) > 0:
             first_car = last_results[0]
             result_brand = first_car.get("brand")
             if result_brand in DEALER_BRANDS:
                 data["intent"] = "buy_new"
                 data["target_brand"] = result_brand
                 data["non_dealer_brand"] = None
                 await state.set_data(data)
                 logger.info(f"Routing fix (direct_phone): switching intent to buy_new for {result_brand}")
        
        from app.handlers.confirm import ask_confirm
        await state.set_state(ConversationState.confirm)
        await ask_confirm(message, state)
        return

    body, drive, price_target, is_approximate, power_target, transmission, gears, engine_type = parse_car_specs(text)
    has_explicit_filters = any([body, drive, price_target, power_target, transmission, gears, engine_type])
    search_like = is_search_query(text)
    
    # --- Check for "already provided phone" context ---
    has_action_kw = any(kw in text_lower for kw in ["оставлял", "давал", "писал", "уже есть", "знаете", "сообщал", "оставил", "дал", "написал"])
    has_object_kw = any(kw in text_lower for kw in ["номер", "телефон", "контакт"])
    
    if has_action_kw and has_object_kw:
        if data.get("phone"):
             if last_results and len(last_results) > 0:
                 first_car = last_results[0]
                 result_brand = first_car.get("brand")
                 if result_brand in DEALER_BRANDS:
                     data["intent"] = "buy_new"
                     data["target_brand"] = result_brand
                     data["non_dealer_brand"] = None
                     await state.set_data(data)
                     logger.info(f"Routing fix (already_phone): switching intent to buy_new for {result_brand}")

             from app.handlers.confirm import ask_confirm
             await state.set_state(ConversationState.confirm)
             await ask_confirm(message, state)
             return
        else:
             await state.set_state(ConversationState.collect_phone)
             await message.answer("Извините, не смог найти Ваш номер в текущей сессии. Напишите его еще раз, пожалуйста.")
             return

    # --- -1. ПРОВЕРКА НА ЗАПРОС МЕНЕДЖЕРА ИЛИ БРОНИРОВАНИЕ ---
    manager_keywords = ["менеджер", "позвони", "связаться", "звонок", "перезвон", "набери", "телефон", "бронир", "забронир", "оформи"]
    
    is_manager_request = any(kw in text_lower for kw in manager_keywords)
    
    if not is_manager_request and len(text.split()) <= 3:
        consent_keywords = ["да", "yes", "хочу", "конечно", "давай"]
        if any(kw == text_lower or kw in text_lower.split() for kw in consent_keywords):
             is_manager_request = True

    if is_manager_request:
        used_keywords_check = ["пробег", "с пробегом", "б/у", "бу", "подержан"]
        if any(ukw in text_lower for ukw in used_keywords_check):
             data["intent"] = "buy_used"
             if non_dealer_brand:
                 data["target_brand"] = non_dealer_brand
             await state.set_data(data)
        
        elif last_results and len(last_results) > 0:
             first_car = last_results[0]
             result_brand = first_car.get("brand")
             if result_brand in DEALER_BRANDS:
                 data["intent"] = "buy_new"
                 data["target_brand"] = result_brand
                 await state.set_data(data)
                 logger.info(f"Routing fix: switching intent to buy_new for {result_brand} based on search results.")

        if data.get("phone"):
            from app.handlers.confirm import ask_confirm
            await state.set_state(ConversationState.confirm)
            await ask_confirm(message, state)
        else:
            await state.set_state(ConversationState.collect_phone)
            data = await state.get_data()
            name = data.get("name", "Клиент")
            await message.answer(f"Хорошо, передам запрос менеджеру.\n{name}, {PHONE_PROMPT.replace('Оставьте свой', 'оставьте, пожалуйста, Ваш')}")
        return
    
    # --- -0. СЛУЖЕБНЫЕ/СЕРВИСНЫЕ ЗАПРОСЫ ---
    def looks_like_service_request() -> bool:
        base_tokens = [
            "ремонт",
            "обслуж",
            "сервис",
            "техобслуж",
            "замен",
            "помен",
            "масло",
            "диагност",
            "шумит",
            "стук",
            "кузов",
            "вмятин",
        ]
        if re.search(r"\bсто\b", text_lower):
            return True
        if " на сто" in text_lower or " в сто" in text_lower:
            return True
        return any(token in text_lower for token in base_tokens)
    
    if not (has_explicit_filters or search_like) and looks_like_service_request():
        logger.info("Detected service/maintenance request inside non_dealer_choice")
        service_brand = brand_matcher.find_brand(text)
        data["intent"] = "repair"
        if service_brand:
            data["user_car_brand"] = service_brand
        await state.set_data(data)
        await state.set_state(ConversationState.collect_repair_type)
        await message.answer(
            "Понял, Вас интересует сервис/обслуживание. "
            "Уточните, пожалуйста, какой ремонт нужен: слесарный (двигатель, подвеска, ТО) или кузовной?"
        )
        return
    
    # --- -0.5 ЗАПРОСЫ ЦВЕТОВ ПО КОНКРЕТНЫМ МОДЕЛЯМ ---
    # Если спрашивают ТОЛЬКО про цвет (нет слов про цену)
    price_keywords = ["стоит", "цена", "почем", "сколько", "стоимость"]
    is_price_question = any(kw in text_lower for kw in price_keywords)
    
    if "цвет" in text_lower and not is_price_question and catalog:
        model_matches = catalog.find_models(text, DEALER_BRANDS)
        if model_matches:
            overview = format_color_overview(model_matches)
            await message.answer(f"{overview}\n\n{current_phone_prompt}")
            return
    
    # --- 0. ОБРАБОТКА КОНТЕКСТНЫХ ЗАПРОСОВ (Follow-up) ---
    
    last_variant_idx = data.get("last_variant_idx")
    requested_color = detect_requested_color(text_lower)

    if requested_color:
        if last_results:
            target_idx = last_variant_idx if isinstance(last_variant_idx, int) else 0
            target_idx = max(0, min(target_idx, len(last_results) - 1))
            car = Car(**last_results[target_idx])
            available_colors = [c.replace("ё", "е") for c in car.available_colors]
            has_color = any(requested_color in color or color in requested_color for color in available_colors)
            
            if has_color:
                msg = (
                    f"{car.brand} {car.model} {car.trim} есть в наличии в цвете {requested_color}. "
                    f"Хотите забронировать или посмотреть другие варианты?\n\n{current_phone_prompt}"
                )
            else:
                eta = car.delivery_days or 14
                msg = (
                    f"Если цвета или комплектации нет в наличии, сможем привезти за {eta} дней. "
                    f"{current_phone_prompt}"
                )
            await state.update_data(last_variant_idx=target_idx)
            await message.answer(msg)
        else:
            eta = 14
            await message.answer(
                f"Если цвета или комплектации нет в наличии, сможем привезти за {eta} дней. {current_phone_prompt}"
            )
        return
    
    # 0.0 Дополнительные цвета
    color_followup_triggers = [
        "другие цвет", "другой цвет", "еще цвет", "ещё цвет", "есть ли другие цвета", "а другие цвета"
    ]
    if any(trigger in text_lower for trigger in color_followup_triggers):
        if isinstance(last_variant_idx, int) and 0 <= last_variant_idx < len(last_results):
            car = Car(**last_results[last_variant_idx])
            colors = ", ".join(car.available_colors) if car.available_colors else "сейчас отсутствуют"
            eta = car.delivery_days or 14
            color_msg = (
                f"{car.brand} {car.model} {car.trim} сейчас есть в цветах: {colors}.\n"
                f"Другие оттенки сможем привезти под заказ примерно за {eta} дней."
            )
            await message.answer(f"{color_msg}\n\n{current_phone_prompt}")
            return
        elif last_results:
             await message.answer(f"Для указанных моделей, если нужного цвета нет в наличии, мы можем привезти автомобиль под заказ (срок поставки ~14 дней).\n\n{current_phone_prompt}")
             return
    
    # 0.1 Поиск ссылки на номер варианта
    variant_number = None
    match = re.search(r'(?:вариант|варианта|варианте|номер|позиция|#)\s*(\d+)', text_lower)
    if match:
        variant_number = int(match.group(1)) - 1
    else:
        match = re.search(r'(\d+)\s*(?:-?\s*(?:вариант|варианта|варианте))', text_lower)
        if match:
            variant_number = int(match.group(1)) - 1

    if last_results:
        target_idx = -1
        if variant_number is not None:
            target_idx = variant_number
        else:
            if "первый" in text_lower or "1-й" in text_lower: target_idx = 0
            elif "второй" in text_lower or "2-й" in text_lower: target_idx = 1
            elif "третий" in text_lower or "3-й" in text_lower: target_idx = 2
        
        # Check if it's a new search (filters present OR brand mentioned)
        body, drive, price, _, power, trans, gears, engine = parse_car_specs(text)
        found_brand_context = brand_matcher.find_brand(text) # Check for brand
        
        is_new_search = any([body, drive, price, power, trans, gears, engine]) or found_brand_context
        
        context_keywords = ["цвет", "скидк", "акци", "наличи", "стоит", "цена", "сколько", "почем"]
        is_context_question = any(kw in text_lower for kw in context_keywords)
        
        # Если указан бренд, это не контекстный вопрос к старому списку
        if (target_idx >= 0 or (is_context_question and not is_new_search)):
            logger.info(f"Detected context follow-up. Index: {target_idx}")
            
            if 0 <= target_idx < len(last_results):
                car_data = last_results[target_idx]
                car = Car(**car_data)
                
                colors = ", ".join(car.available_colors) if car.available_colors else "уточняйте у менеджера"
                
                discounts = []
                if car.discount_tradein: discounts.append(f"Трейд-ин: {car.discount_tradein:,}")
                if car.discount_credit: discounts.append(f"Кредит: {car.discount_credit:,}")
                if car.discount_gov: discounts.append(f"Госпрограмма: {car.discount_gov:,}")
                if car.discount_other: discounts.append(f"Спец: {car.discount_other:,}")
                
                price_details = f"💰 **Цена итого:** {car.final_price:,} ₽\n"
                if car.base_price and car.base_price > car.final_price:
                    price_details += f"🏷 **РРЦ (базовая):** {car.base_price:,} ₽\n"
                
                discounts_str = ""
                if discounts:
                    discounts_str = f"📉 **Включенные скидки:**\n" + "\n".join([f"- {d}" for d in discounts]) + "\n"

                detail_text = (
                    f"🚙 **{car.brand} {car.model} {car.trim}**\n\n"
                    f"🎨 **Цвета в наличии:** {colors}\n"
                    f"{price_details}"
                    f"{discounts_str}\n"
                    f"⏱ **Срок поставки (если нет цвета):** {car.delivery_days or 14} дней.\n\n"
                    f"Хотите забронировать этот автомобиль или оформить заявку?"
                )
                await state.update_data(last_variant_idx=target_idx)
                await message.answer(f"{detail_text}\n\n{current_phone_prompt}")
                return

            elif target_idx == -1 and is_context_question:
                response = "По Вашим вариантам:\n\n"
                for i, c_data in enumerate(last_results, 1):
                    c = Car(**c_data)
                    colors = ", ".join(c.available_colors[:3]) + ("..." if len(c.available_colors)>3 else "")
                    max_discount = (c.discount_tradein or 0) + (c.discount_credit or 0) + (c.discount_gov or 0) + (c.discount_other or 0)
                    
                    response += f"{i}. **{c.brand} {c.model}**\n"
                    response += f"   🎨 Цвета: {colors}\n"
                    if max_discount > 0:
                        response += f"   📉 Скидки до: {max_discount:,} ₽\n"
                    else:
                        response += f"   Цена без скидок: {c.final_price:,} ₽\n"
                    response += "\n"
                
                await state.update_data(last_variant_idx=None)
                await message.answer(f"{response}\n{current_phone_prompt}")
                return

    # --- 1. ПРЯМОЙ ЗАПРОС МОДЕЛИ ---
    if catalog:
        found_models = catalog.find_models(text, DEALER_BRANDS)
        if found_models:
            logger.info(f"Found direct model request in non_dealer_choice: {len(found_models)} cars")
            response_text = format_model_response(found_models)
            await message.answer(response_text)
            models_to_save = [asdict(m) for m in found_models[:5]]
            await state.update_data(last_search_results=models_to_save)
            return

    # --- 2. ЛОГИКА ПЕРЕКЛЮЧЕНИЯ БРЕНДОВ (фильтрация) ---
    
    # Определяем, по каким брендам искать
    search_brands = DEALER_BRANDS.copy()
    
    found_brand = brand_matcher.find_brand(text)
    
    # Если бренд явно назван в тексте ("Haval самый мощный")
    if found_brand and found_brand in DEALER_BRANDS:
        search_brands = {found_brand}
        # Обновляем таргет бренд, если он отличается
        if data.get("target_brand") != found_brand:
            await state.update_data(target_brand=found_brand, intent="buy_new")
            
    # Если в тексте бренда нет, но он есть в контексте (мы уже обсуждаем Haval)
    elif data.get("target_brand") and data.get("target_brand") in DEALER_BRANDS:
        # Но если пользователь хочет "альтернативы", мы не должны ограничивать
        # Проверяем, не является ли это запросом "а что есть у других?"
        # Пока считаем, что контекст сохраняется, если не сказано иное.
        target = data.get("target_brand")
        search_brands = {target}

    # Проверка на "с пробегом" (старый код был тут, но он мог конфликтовать с фильтрацией)
    # ...

    # --- 3. ПАРСИНГ И ПОИСК ---
    logger.info(f"Parsed specs: body={body}, drive={drive}, price={price_target}, power={power_target}, trans={transmission}, gears={gears}, engine={engine_type}")

    # Логика цены
    price_max_filter = None
    price_min_filter = None
    
    more_expensive_keywords = ["подороже", "дорого", "повыше", "больше", "дороже"]
    is_more_expensive = any(kw in text_lower for kw in more_expensive_keywords)
    
    if price_target:
        if is_approximate:
            price_max_filter = int(price_target * 1.10)
        else:
            price_max_filter = price_target
        await state.update_data(last_price_max=price_max_filter)
    
    if is_more_expensive and last_price_max:
        price_min_filter = last_price_max
        logger.info(f"Context: 'more expensive' -> setting price_min to {last_price_max}")

    # Вспомогательная функция поиска с учетом search_brands
    async def perform_search(p_max, p_min, d_body, d_drive, d_power, d_trans, d_gears, d_engine, force_structural=False):
        if force_structural or not (semantic_index and semantic_index.index is not None):
            if catalog:
                return catalog.search(
                    dealer_brands=search_brands, # Use detected brands
                    body=d_body,
                    drive=d_drive,
                    price_max=p_max,
                    price_min=p_min,
                    power_min=d_power,
                    transmission=d_trans,
                    gears=d_gears,
                    engine_type=d_engine
                )
            return []

        use_semantic = True
        if d_trans or d_gears or d_engine: 
            use_semantic = False
        
        if use_semantic:
            return await semantic_index.search(
                query=text,
                dealer_brands=search_brands, # Use detected brands
                body=d_body,
                drive=d_drive,
                price_max=p_max,
                price_min=p_min,
                power_min=d_power,
                top_k=50
            )
        
        if catalog:
            return catalog.search(
                dealer_brands=search_brands, # Use detected brands
                body=d_body,
                drive=d_drive,
                price_max=p_max,
                price_min=p_min,
                power_min=d_power,
                transmission=d_trans,
                gears=d_gears,
                engine_type=d_engine
            )
        return []

    cars = await perform_search(price_max_filter, price_min_filter, body, drive, power_target, transmission, gears, engine_type)
    
    if not cars:
        logger.info("⚠️ Search returned 0 results. Trying fallback with forced structural search...")
        fallback_price = int(price_max_filter * 1.15) if price_max_filter else None
        fallback_min = int(price_min_filter * 0.9) if price_min_filter else None
        fallback_drive = None 
        fallback_power = int(power_target * 0.8) if power_target else None
        fallback_gears = None
        cars = await perform_search(fallback_price, fallback_min, body, fallback_drive, fallback_power, transmission, fallback_gears, engine_type, force_structural=True)

    if not cars and catalog:
        all_cars = catalog.get_all_cars()
        # Fallback only within search_brands
        fallback_pool = [c for c in all_cars if c.brand in search_brands]
        
        if non_dealer_brand or not has_explicit_filters:
            crossovers = [c for c in fallback_pool if c.body.lower() in ["кроссовер", "suv"]]
            cars = crossovers if crossovers else fallback_pool[:10]
        elif fallback_pool and not cars:
            cars = fallback_pool[:5]

    # --- ОПРЕДЕЛЕНИЕ СТРАТЕГИИ СОРТИРОВКИ ---
    is_power_req = is_power_query(text)
    is_expensive_req = is_expensive_query(text)
    
    if is_power_req:
        sort_strategy = "power_desc"
    elif is_expensive_req:
        sort_strategy = "price_desc"
    else:
        sort_strategy = "price_mix"
    
    best_offers = pick_top3_offers(
        cars, 
        price_target=price_target, 
        is_approximate=is_approximate,
        sort_by=sort_strategy
    )
    
    if best_offers:
        await state.update_data(last_variant_idx=None)
        try:
            cars_to_save = [asdict(c) for c in best_offers]
            await state.update_data(last_search_results=cars_to_save)
        except Exception as e:
            logger.error(f"Failed to save results to state: {e}")

    # --- ФОРМИРОВАНИЕ ОТВЕТА ---
    prepend_notice = bool(non_dealer_brand and not non_dealer_notice_sent)
    
    if best_offers:
        message_blocks = []
        if prepend_notice:
            message_blocks.append(build_non_dealer_prompt(non_dealer_brand))
            await state.update_data(non_dealer_notice_sent=True)
        
        if is_power_req:
            header = "Подобрал для Вас самые мощные варианты по вашим критериям:\n"
        elif is_expensive_req:
            header = "Подобрал для Вас максимальные комплектации:\n"
        elif is_more_expensive:
            header = "Посмотрел варианты подороже:\n"
        else:
            header = "По вашим параметрам подобрал варианты:\n"
        message_blocks.append(header)
            
        response_text = ""
        for i, car in enumerate(best_offers, 1):
            drive_text = {
                "4x4": "полный",
                "передний": "передний",
                "задний": "задний",
            }.get(car.drive.lower(), car.drive)
            
            price_fmt = f"{car.final_price:,}".replace(",", " ")
            
            price_block = f"Цена с учетом скидок: {price_fmt} ₽"
            discounts_text = []
            if car.discount_tradein: discounts_text.append(f"Трейд-ин: {car.discount_tradein:,}")
            if car.discount_credit: discounts_text.append(f"Кредит: {car.discount_credit:,}")
            if car.discount_gov: discounts_text.append(f"Гос: {car.discount_gov:,}")
            if car.discount_other: discounts_text.append(f"Спец: {car.discount_other:,}")
            
            if car.base_price and car.base_price > car.final_price:
                base_fmt = f"{car.base_price:,}".replace(",", " ")
                price_block += f"\n     (РРЦ: {base_fmt} ₽"
                if discounts_text:
                    price_block += f". Скидки: {', '.join(discounts_text)}"
                price_block += ")"
            elif discounts_text:
                 price_block += f"\n     (Скидки: {', '.join(discounts_text)})"
            
            response_text += (
                f"{i}. {car.brand} {car.model} {car.trim}\n"
                f"   • {car.body}, {drive_text}, {car.engine_type} {car.power} л.с.\n"
                f"   • {car.transmission} ({car.transmission_details})\n"
                f"   • {price_block}\n\n"
            )
        
        response_text += "Хотите посмотреть подробнее (цвета, скидки) или подобрать ещё варианты?"
        data = await state.get_data()
        name = data.get("name", "Клиент")
        
        if has_phone:
             prompt = f"\n\n{name}, {current_phone_prompt}"
        else:
             prompt = f"\n\n{name}, {current_phone_prompt.replace('Оставьте свой', 'оставьте свой')}"
        
        response_text += prompt
        message_blocks.append(response_text)
        
        await message.answer("\n".join(message_blocks))
        return
    
    else:
        message_blocks = []
        if prepend_notice:
            message_blocks.append(build_non_dealer_prompt(non_dealer_brand))
            await state.update_data(non_dealer_notice_sent=True)
            
            if not catalog or not catalog.get_all_cars():
                 message_blocks.append("Сейчас каталог обновляется. Оставьте заявку, менеджер свяжется с Вами.")
            else:
                 pass 
        else:
            message_blocks.append("К сожалению, по таким параметрам сейчас нет автомобилей в наличии.")

        await message.answer("\n".join(message_blocks))
        
        data = await state.get_data()
        name = data.get("name", "Клиент")
        
        if has_phone:
             prompt = current_phone_prompt
        else:
             prompt = current_phone_prompt.replace('Оставьте свой', 'оставьте, пожалуйста, Ваш')

        await message.answer(f"Могу предложить помощь менеджера, чтобы подобрать альтернативу индивидуально.\n{name}, {prompt}")
