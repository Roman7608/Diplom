from typing import Optional
import re
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from app.fsm.states import ConversationState
from app.llm.router import LLMRouter
from app.utils.brand_matcher import BrandMatcher, DEALER_BRANDS
from app.utils.catalog import CarCatalog
from app.utils.semantic_search import SemanticCarIndex
from app.utils.text_parsers import is_search_query, is_expensive_query, is_power_query
from app.utils.response_helpers import format_model_response, build_non_dealer_prompt, format_lead_message
from app.handlers.non_dealer_choice import handle_non_dealer_choice
from app.models.dto import LeadDTO
from app.utils.leads_file import append_lead
from loguru import logger

router = Router()


@router.message(ConversationState.detect_intent)
async def handle_detect_intent(
    message: Message,
    state: FSMContext,
    router_llm: LLMRouter,
    brand_matcher: BrandMatcher,
    catalog: Optional[CarCatalog] = None,
    semantic_index: Optional[SemanticCarIndex] = None,
):
    """
    Detect user intent using catalog check, search heuristics, and LLM router.
    """
    text = message.text or ""
    text_lower = text.lower()
    logger.info(f"Detecting intent for text: {text[:100]}")
    
    # 1.0 PRIORITY SERVICE CHECK (Moved to TOP)
    # Checks for explicit service/parts keywords to bypass purchase logic
    service_parts_keywords = [
        "ремонт", "сервис", "обслуж", "замен", "помен", "покрас", 
        "кузов", "вмятин", "бампер", "крыло", "капот", "дверь", "диагност", 
        "запчаст", "диск", "шин", "масло", "фильтр", "колодк", "свечи", "сто ", "переобу",
        "провести", "пройти" # Added verbs often used with TO
    ]
    is_potential_service = any(kw in text_lower for kw in service_parts_keywords)
    
    # Extra regex check for "то" (Technical Maintenance) as a whole word
    if not is_potential_service:
        if re.search(r'\bто\b', text_lower):
            is_potential_service = True

    # 1. ПРЯМОЙ ЗАПРОС МОДЕЛИ (Issue #2) - Now AFTER service check
    if catalog and not is_potential_service:
        # Ищем точное совпадение модели в каталоге среди дилерских марок
        found_models = catalog.find_models(text, DEALER_BRANDS)
        
        if found_models:
            logger.info(f"Found direct model request: {len(found_models)} cars")
            
            response_text = format_model_response(found_models)
            
            # Сохраняем контекст intent=buy_new
            first_car = found_models[0]
            await state.update_data(intent="buy_new", target_brand=first_car.brand, slots={"model": first_car.model})
            
            await message.answer(response_text)
            return

    # 1.0.5 SMART SEARCH CHECK FOR DEALER BRANDS (Priority over standard purchase)
    # If user asks "Chery most powerful?" or "Haval prices?", go straight to search.
    simple_brand = brand_matcher.find_brand(text)
    if simple_brand and simple_brand in DEALER_BRANDS and not is_potential_service:
        # Расширяем список триггеров для мгновенного поиска
        smart_triggers = ["какие", "есть", "наличи", "цен", "стоит", "сколько", "почем", "вариант", "модел"]
        if is_power_query(text) or is_expensive_query(text) or is_search_query(text) or any(t in text_lower for t in smart_triggers):
             logger.info(f"Detected dealer brand {simple_brand} with smart search params -> non_dealer_choice")
             await state.update_data(
                intent="buy_new",
                target_brand=simple_brand,
                non_dealer_brand=None,
                non_dealer_notice_sent=False,
             )
             await state.set_state(ConversationState.non_dealer_choice)
             await handle_non_dealer_choice(message, state, brand_matcher, catalog, semantic_index)
             return

    # 1.1 Прямой сигнал "купить <марку>" (включая "новый <марка>")
    purchase_triggers = ["куп", "есть", "налич", "прод", "хочу", "брони", "интересует", "новый", "новая", "новое", "новую", "новых"]
    
    # Condition: Found brand + Purchase keyword + NOT a service request
    if simple_brand and any(trigger in text_lower for trigger in purchase_triggers) and not is_potential_service:
        if simple_brand in DEALER_BRANDS:
            # Note: Smart query check already handled above. This is fallback for simple "Buy Chery".
            logger.info(f"Detected dealer brand request without LLM for {simple_brand}")
            await state.update_data(
                intent="buy_new",
                target_brand=simple_brand,
                non_dealer_brand=None,
                non_dealer_notice_sent=False,
            )
            await state.set_state(ConversationState.collect_specs)
            await message.answer("До какой суммы рассматриваете автомобиль?")
            return
        else:
            logger.info(f"Detected non-dealer brand purchase intent for {simple_brand}")
            await state.update_data(
                non_dealer_brand=simple_brand,
                intent="buy_new",
                target_brand=simple_brand,
                non_dealer_notice_sent=False,
            )
            await state.set_state(ConversationState.non_dealer_choice)
            await handle_non_dealer_choice(message, state, brand_matcher, catalog, semantic_index)
            return

    # 2. ПОИСКОВЫЙ ЗАПРОС (Issue #1)
    # Если это явный поисковый запрос (содержит параметры или "подбери")
    # Добавили is_power_query, чтобы "самый мощный какой" без бренда тоже срабатывал
    if (is_search_query(text) or is_expensive_query(text) or is_power_query(text)) and not is_potential_service:
        logger.info("Detected search query logic (search or expensive or power)")
        
        # Проверяем бренд
        found_brand = brand_matcher.find_brand(text)
        
        # Если это недилерский бренд, сохраняем его
        # Проверяем, не отправляли ли мы уже уведомление про этот бренд
        data = await state.get_data()
        last_non_dealer = data.get("non_dealer_brand")
        
        if found_brand and found_brand not in DEALER_BRANDS:
             should_reset_notice = (found_brand != last_non_dealer)
             await state.update_data(non_dealer_brand=found_brand)
             if should_reset_notice:
                 await state.update_data(non_dealer_notice_sent=False)
        else:
             # Если бренда нет или он дилерский, ОБЯЗАТЕЛЬНО очищаем мусор от старых запросов
             await state.update_data(non_dealer_brand=None, non_dealer_notice_sent=False)
        
        await state.set_state(ConversationState.non_dealer_choice)
        
        # Сразу запускаем поиск
        await handle_non_dealer_choice(message, state, brand_matcher, catalog, semantic_index)
        return

    # 3. LLM CLASSIFICATION
    result = await router_llm.classify_text(text)
    logger.info(f"Router result: intent={result.intent}, confidence={result.confidence}, brand={result.target_brand}")
    
    # Нормализация бренда от LLM
    if result.target_brand:
        normalized_llm = brand_matcher.find_brand(result.target_brand)
        if normalized_llm:
             result.target_brand = normalized_llm
        else:
             from_text = brand_matcher.find_brand(text)
             if from_text:
                 result.target_brand = from_text

    # Brand matcher fallback
    if not result.target_brand and not result.user_car_brand:
        found_brand = brand_matcher.find_brand(text)
        if found_brand:
            if result.intent in ["buy_new", "buy_used"]:
                result.target_brand = found_brand
            elif result.intent in ["repair", "spares", "sell"]:
                result.user_car_brand = found_brand
            elif result.intent == "other":
                # Неоднозначный запрос только с брендом
                pass 
    
    # Восстановление контекста марки для переключения интентов (например, из ремонта в покупку)
    data = await state.get_data() # Load data first
    
    if result.intent in ["buy_new", "buy_used"] and not result.target_brand:
        # Если человек говорит "купить такой же" или просто переключился на покупку, 
        # но не назвал марку, возможно он имеет в виду user_car_brand (свой текущий)
        if "такой же" in text_lower or "новую такую" in text_lower or "обновить" in text_lower:
             if data.get("user_car_brand"):
                 result.target_brand = data.get("user_car_brand")
                 logger.info(f"Context: inferred target brand {result.target_brand} from user_car_brand")

    # Если просто переключился на покупку "купить новый", а марка не названа, но есть в контексте user_car_brand
    # Спросим: "Хотите новый [Марка]?"
    # Это обрабатывается в блоке ниже, где мы сохраняем target_brand.
    
    # Инициализируем data из state в начале, чтобы избежать UnboundLocalError при обращении в блоках ниже
    # уже сделано выше: data = await state.get_data()

    # Inference fallback (spares/parts) - ПРОВЕРЯЕМ ПЕРВЫМ, чтобы "купить диски" не ушло в покупку авто
    if result.intent == "other" or result.confidence == "low":
        spares_keywords = [
            "запчаст", "запасные части", "отдел запчастей", 
            "диск", "шины", "резина", "колеса", "колодки", 
            "сцепление", "свечи", "стекло", "дворники", "стеклоочистители",
            "подвеск", "шаровая", "шрус", "двигатель", "коробка", "фильтр",
            "накладки", "прокладки", "сальники", "аккумулятор"
        ]
        if any(kw in text_lower for kw in spares_keywords):
            # Если есть слово "замена" или "поменять" или "шиномонтаж", то это скорее сервис (уже обработано или будет ниже).
            # Но если "купить диски", то это запчасти.
            is_replace = any(kw in text_lower for kw in ["замен", "помен", "установ", "шиномонтаж", "переобу"])
            if not is_replace:
                 result.intent = "spares"
                 result.confidence = "high"
                 if not result.user_car_brand:
                     found_brand = brand_matcher.find_brand(text)
                     if found_brand: result.user_car_brand = found_brand

    # Fallback для "замена масла" -> repair
    if result.intent == "other" or result.confidence == "low":
        oil_keywords = ["масло", "заменить масло", "поменять масло", "замена масла"]
        if any(kw in text_lower for kw in oil_keywords):
             result.intent = "repair"
             result.confidence = "high"
             
             # Пытаемся найти бренд в текущем тексте
             new_brand = brand_matcher.find_brand(text)
             
             # Если бренд не найден, проверяем user_car_brand из предыдущего контекста (state)
             if not result.user_car_brand:
                 if new_brand:
                     result.user_car_brand = new_brand
                 elif data.get("user_car_brand"):
                     result.user_car_brand = data.get("user_car_brand")
             
             # Сразу ставим тип ремонта
             if "slots" not in data: data["slots"] = {}
             if not result.slots: result.slots = {}
             result.slots["repair_type"] = "слесарный"
             result.slots["repair_details"] = "замена масла в двигателе"
             # Обновляем result.slots, который потом попадет в state
             
             # Переходим в collect_repair_type, но он должен сразу отработать?
             # Нет, message handler срабатывает на следующее сообщение.
             # Нам нужно вызвать handler вручную или отправить сообщение здесь.
             # Вызовем handle_collect_repair_type вручную, эмулируя пустой текст (или текущий), 
             # чтобы он увидел repair_type в стейте.
             # Но state мы обновим ниже.
             
             # Важно: data обновляется ниже.
             # Мы просто модифицируем result, и код ниже обновит state.
             # Потом переключим state на collect_repair_type
             pass

    # Inference fallback (buy_new keywords)
    if result.intent == "other" or (result.intent == "buy_new" and result.confidence == "low"):
        # Важно: если мы уже определили сервис выше (is_potential_service), то не сваливаемся в покупку
        if not is_potential_service:
            purchase_keywords = ["хочу купить", "покупка", "нужен новый", "ищу новый", "хочу новый", "купить новый", "купить новую", "забронировать", "оформить"]
            if any(kw in text_lower for kw in purchase_keywords):
                result.intent = "buy_new"
                result.confidence = "medium"
                if not result.target_brand:
                    found_brand = brand_matcher.find_brand(text)
                    if found_brand:
                        result.target_brand = found_brand
    
    if result.intent == "other" and ("купить" in text_lower or "покупка" in text_lower) and not is_potential_service:
         result.intent = "buy_new"

    # Inference fallback (repair/service keywords)
    # Если LLM не справилась, или сервис определен эвристикой выше
    if result.intent == "other" or result.confidence == "low" or is_potential_service:
        # Если эвристика сработала, принудительно ставим repair
        if is_potential_service:
             result.intent = "repair"
             result.confidence = "high"

        # 1. Keywords lists (reused for detailed classification)
        general_repair_keywords = [
            "ремонт", "отремонтировать", "заменить", "поменять", "сменить", "починить", "сломалось", 
            "неисправность", "записаться", "сервис", "обслуживание", "то ", "то,", "проблем"
        ]
        locksmith_keywords = [
            "продиагностировать", "диагностика", "найти неисправность", 
            "шиномонтаж", "отрегулировать", "установить", "переобу", "шин",
            "колодк", "свечи", "фильтр", "подвеск", "ходов", "масло", "то ", "то,"
        ]
        bodywork_keywords = [
            "покрасить", "подобрать колер", "подобрать краску", 
            "вытянуть кузов", "отрихтовать", "вмятин", "дтп", "авария", "царапин"
        ]
        body_parts = ["крыло", "бампер", "крыш", "дверь", "багажник", "капот", "кузов", "порог"]

        all_repair_triggers = set(general_repair_keywords + locksmith_keywords + bodywork_keywords + body_parts)
        
        if is_potential_service or any(kw in text_lower for kw in all_repair_triggers) or re.search(r'\bто\b', text_lower):
             result.intent = "repair"
             result.confidence = "medium"
             if not result.user_car_brand:
                 found_brand = brand_matcher.find_brand(text)
                 if found_brand: result.user_car_brand = found_brand

             # 2. Classification Logic
             if not result.slots: result.slots = {}

             # Check Bodywork first (specific keywords)
             is_bodywork = False
             if any(kw in text_lower for kw in bodywork_keywords):
                 is_bodywork = True
                 result.slots["repair_details"] = "кузовной ремонт"

             # Check "Replace" + body part
             if "замен" in text_lower or "помен" in text_lower:
                 if any(part in text_lower for part in body_parts):
                     is_bodywork = True
                     result.slots["repair_details"] = "замена кузовной детали"

             if is_bodywork:
                 result.slots["repair_type"] = "кузовной"
             
             else:
                 # If not bodywork, check locksmith
                 is_locksmith = False
                 if any(kw in text_lower for kw in locksmith_keywords) or re.search(r'\bто\b', text_lower):
                     is_locksmith = True
                     if re.search(r'\bто\b', text_lower):
                         result.slots["repair_details"] = "техническое обслуживание"
                     else:
                         result.slots["repair_details"] = "слесарный ремонт"
                 
                 # "Replace" + NOT body part implies locksmith (e.g. replace oil, pads, etc.)
                 if ("замен" in text_lower or "помен" in text_lower) and not is_bodywork:
                     is_locksmith = True
                     result.slots["repair_details"] = "замена детали/расходника"
                 
                 if is_locksmith:
                     result.slots["repair_type"] = "слесарный"

    # Inference fallback (accounting)
    if result.intent == "other" or result.confidence == "low":
        accounting_keywords = ["бухгалтер", "счет", "акт", "документ", "оплат"]
        if any(kw in text_lower for kw in accounting_keywords):
            result.intent = "accounting"
            result.confidence = "high"

    # Inference fallback (spares/parts)
    if result.intent == "other" or result.confidence == "low":
        spares_keywords = [
            "запчаст", "запасные части", "отдел запчастей", 
            "диск", "шины", "резина", "колеса", "колодки", 
            "сцепление", "свечи", "стекло", "дворники", "стеклоочистители",
            "подвеск", "шаровая", "шрус", "двигатель", "коробка", "фильтр",
            "накладки", "прокладки", "сальники", "аккумулятор"
        ]
        if any(kw in text_lower for kw in spares_keywords):
             # Исключаем конфликты, если это часть фразы "замена масла" (уже обработано выше как ремонт)
             # Но если ремонт не сработал (например, "купить диски"), то зайдем сюда.
             # Важно: "купить" может тянуть в buy_new.
             # Поэтому spares проверяем ДО buy_new fallback или переопределяем его.
             # В данном месте кода мы уже ПОСЛЕ buy_new inference (строки 167-176).
             # Значит, если там сработало "купить", мы уже buy_new.
             # Надо поднять этот блок ВЫШЕ блока buy_new или добавить условие.
             
             # Однако, код выполняется последовательно.
             # Если выше buy_new сработал (intent="buy_new"), то мы сюда не зайдем?
             # Нет, условие `result.intent == "other" or ...`
             # Если buy_new уже установлен с confidence medium, мы сюда не зайдем (если confidence не low).
             # Fallback buy_new ставит confidence medium.
             
             # Значит, надо ставить spares ВЫШЕ buy_new fallback.
             pass

    # Update state
    # data уже получен выше, обновляем его
    new_data = {
        "intent": result.intent,
        "confidence": result.confidence,
        "slots": result.slots,
    }
    
    # Intelligently update brands
    if result.target_brand:
        new_data["target_brand"] = result.target_brand
    elif result.intent in ["buy_new", "buy_used"] and not result.target_brand:
        # Если бренд не найден, но был user_car_brand, и мы не переопределили его выше -> 
        # Оставляем target_brand пустым, чтобы спросить.
        # НО: Если пользователь спросил "купить новый?", а до этого говорил про Порше (user_car_brand),
        # Мы можем предположить, что он хочет купить новый Порше (или спросить).
        # Если мы здесь оставим None, бот спросит "Какую марку?".
        # ТЗ говорит: "Вы хотите ... (названная ранее марка)?"
        
        if data.get("user_car_brand"):
             # Сохраняем этот бренд как "potential_brand" или сразу target?
             # Если сразу target, бот пойдет по ветке "buy_new" с этим брендом.
             # Для дилерского бренда - сразу спросит цену.
             # Для недилерского - покажет уведомление.
             # Если мы хотим переспросить "Вы хотите купить Порше?", нам нужно специальное состояние.
             pass
    
    if result.user_car_brand:
        new_data["user_car_brand"] = result.user_car_brand
    elif result.intent == "other":
        if "user_car_brand" in data:
            new_data["user_car_brand"] = data["user_car_brand"]
    
    # Fix loss of context for repair if brand not mentioned in current message
    if result.intent in ["repair", "spares", "sell"] and not result.user_car_brand:
        if data.get("user_car_brand"):
            new_data["user_car_brand"] = data.get("user_car_brand")
            result.user_car_brand = data.get("user_car_brand") # Update result too for logic below
            
    data.update(new_data)
    await state.set_data(data)
    
    # Проверка: если телефон уже есть в данных (и мы не обновляем его принудительно), 
    # то возможно стоит пропустить collect_phone и сразу подтвердить?
    # НО: ТЗ требует подтверждения контакта. Оставим пока collect_phone,
    # но сам collect_phone можно сделать умнее (предложить использовать старый).
    # Или можно здесь проверить phone в data.
    # Решение: если phone есть, переходим сразу в confirm (ask_confirm).
    
    has_phone = bool(data.get("phone"))
    
    # --- Branching Logic ---
    
    intent = result.intent
    
    # Ambiguous brand-only request handling
    if intent == "other":
        found_brand = brand_matcher.find_brand(text)
        if found_brand:
             # Пользователь написал только "ФВ Тигуан" или "Chery" без глаголов
             logger.info(f"Ambiguous brand request: {found_brand}")
             # Сохраняем бренд как user_car_brand на случай сервиса
             # Но также это может быть покупка.
             # Спрашиваем прямо.
             await state.update_data(user_car_brand=found_brand, target_brand=found_brand) # Ambiguous
             
             # Используем специальный стейт или просто message.answer с кнопками? 
             # По ТЗ кнопок нет, текст.
             # Остаемся в detect_intent? Нет, лучше спросить и ждать ответ.
             # Можно использовать collect_repair_type как хак, но лучше detect_intent с контекстом.
             # Проще всего: спросить и остаться в detect_intent. Следующее сообщение будет "покупка" или "ремонт".
             
             await message.answer(f"Вас интересует покупка автомобиля {found_brand} или ремонт/обслуживание этого автомобиля?")
             return

    if intent in ["buy_new", "buy_used"]:
        
        # Если бренд недилерский -> non_dealer_choice
        if intent == "buy_new" and result.target_brand and result.target_brand not in DEALER_BRANDS:
            data["non_dealer_brand"] = result.target_brand
            data["non_dealer_notice_sent"] = False
            await state.set_data(data)
            await state.set_state(ConversationState.non_dealer_choice)
            
            await handle_non_dealer_choice(message, state, brand_matcher, catalog, semantic_index)
            return
            
        if not result.target_brand:
            # Если марка не указана, но есть user_car_brand (из контекста)
            if data.get("user_car_brand"):
                prev_brand = data.get("user_car_brand")
                # Спрашиваем подтверждение
                await state.update_data(target_brand=prev_brand) # Предполагаем
                # Для подтверждения можно использовать collect_brand с вопросом
                await state.set_state(ConversationState.collect_brand)
                await message.answer(f"Вы хотите купить автомобиль {prev_brand} или рассматриваете другой вариант?")
                return

            if is_search_query(text):
                 await state.update_data(non_dealer_brand=None) # Очистка
                 await state.set_state(ConversationState.non_dealer_choice)
                 await handle_non_dealer_choice(message, state, brand_matcher, catalog, semantic_index)
                 return

            await state.set_state(ConversationState.collect_brand)
            await message.answer("Правильно понимаю, Вы хотите купить автомобиль? Подскажите, новый или с пробегом, и какую марку/модель рассматриваете?")
        
        elif intent == "buy_new" and result.target_brand in DEALER_BRANDS:
            await state.set_state(ConversationState.collect_specs)
            await message.answer("До какой суммы рассматриваете автомобиль?")
            
        else:
            if has_phone:
                from app.handlers.confirm import ask_confirm
                await state.set_state(ConversationState.confirm)
                await ask_confirm(message, state)
            else:
                await state.set_state(ConversationState.collect_phone)
                await message.answer("Оставьте, пожалуйста, номер телефона, по которому Вам удобно принять звонок.")

    elif intent in ["repair", "spares", "sell"]:
        if not result.user_car_brand:
            await state.set_state(ConversationState.collect_brand)
            await message.answer("Какой у Вас автомобиль (марка и модель)?")
        else:
            if has_phone:
                from app.handlers.confirm import ask_confirm
                await state.set_state(ConversationState.confirm)
                await ask_confirm(message, state)
            else:
                await state.set_state(ConversationState.collect_phone)
                name = data.get("name", "Клиент")
                # Для сервиса более утвердительный ответ, как просили
                await message.answer(f"Да, мы можем это сделать. {name}, оставьте, пожалуйста, Ваш номер телефона, в течение 10 минут Вам перезвонит специалист.")

    elif intent == "accounting":
        if has_phone:
            from app.handlers.confirm import ask_confirm
            await state.set_state(ConversationState.confirm)
            await ask_confirm(message, state)
        else:
            await state.set_state(ConversationState.collect_phone)
            await message.answer("Оставьте, пожалуйста, номер телефона, по которому Вам удобно принять звонок.")
        
    else: # other
        # Check retry count
        retry_count = data.get("retry_count", 0)
        if retry_count >= 2:
             # 3rd attempt (0, 1, 2) - give up
             logger.info(f"Retry count {retry_count} exceeded.")
             
             if has_phone:
                 name = data.get("name", "Клиент")
                 phone = data.get("phone")
                 
                 # Save lead
                 lead = LeadDTO(
                    user_id=message.from_user.id,
                    name=name,
                    intent="fallback_other",
                    brand=data.get("user_car_brand") or data.get("target_brand"),
                    phone=phone,
                    slots=data.get("slots", {}),
                 )
                 try:
                     append_lead(lead.model_dump())
                 except Exception as e:
                     logger.error(f"Failed to save lead: {e}")

                 # Send to Telegram Group
                 GROUP_OTHER = "-1002944317515"
                 try:
                     await message.bot.send_message(chat_id=GROUP_OTHER, text=format_lead_message(lead))
                     logger.info(f"Fallback lead sent to {GROUP_OTHER}")
                 except Exception as e:
                     logger.error(f"Failed to send fallback lead to group: {e}")
                 
                 # Final response
                 await message.answer("Извините, я Вас не понял. В течение 10 минут Вам перезвонит специалист.")
                 await state.set_state(ConversationState.finished)
                 return
             
             # Если телефона нет
             await state.update_data(intent="fallback_other") # Special intent for routing
             await state.set_state(ConversationState.collect_phone)
             name = data.get("name", "Клиент")
             await message.answer(f"Извините, я Вас не понял. {name}, оставьте, пожалуйста, свой номер телефона, в течение 10 минут Вам перезвонит специалист")
             return
        
        # Increment retry count
        await state.update_data(retry_count=retry_count + 1)

        # Эвристика для фраз владения ("у меня X", "езжу на X")
        ownership_triggers = ["у меня", "езжу на", "владею", "мой автомобиль", "моя машина"]
        if any(trigger in text_lower for trigger in ownership_triggers):
             logger.info("Detected ownership context statement.")
             found_brand = result.user_car_brand or brand_matcher.find_brand(text)
             
             if not found_brand:
                 # Если марка не найдена (опечатка или не указана), спрашиваем
                 await state.set_state(ConversationState.collect_brand)
                 await message.answer("Подскажите, какой у Вас автомобиль (марка)?")
                 return

             brand_text = found_brand
             await state.update_data(user_car_brand=found_brand)
             
             await message.answer(
                 f"Понял, у Вас {brand_text}. Планируете обменять его на новый (Trade-in), продать или нужно обслуживание?"
             )
             return

        # Instead of immediately asking for phone on first failure, we ask clarifying question
        # But if we are here, it means heuristics and LLM failed.
        # If this is the first/second time, maybe ask generic question?
        # But the requirement says: "bot should not ask leading question more than 2 times".
        # Currently, if we fall here, we were asking phone immediately.
        # We should probably ask "Could you rephrase?" if count < 2.
        
        await message.answer("Не совсем понял Ваш запрос. Пожалуйста, уточните, что Вас интересует: покупка, ремонт или другое?")
        # We stay in detect_intent state naturally as we didn't change it (unless ownership heuristic triggered return)
