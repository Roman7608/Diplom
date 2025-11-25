from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from app.fsm.states import ConversationState
from app.models.dto import LeadDTO
from app.utils.leads_file import append_lead
from app.utils.brand_matcher import DEALER_BRANDS, BrandMatcher
from app.utils.response_helpers import format_lead_message
from loguru import logger

# Константы для групп
GROUP_CHERY = "-1002972087933"
GROUP_JETOUR = "-4920166613"
GROUP_HAVAL = "-1004894652862"
GROUP_PROBEG = "-1002914948690"
GROUP_OTHER = "-1002944317515"
GROUP_ACCOUNTING = "-1002746330909"

# Сервис и Запчасти
GROUP_STO_CHERY = "-1003182881943"   # Слесарный ремонт Chery, Jetour, Omoda, Tenet, Jaecoo
GROUP_KUZOV = "-1003127031679"       # Кузовной ремонт любых авто
GROUP_STO_HAVAL = "-1003159687346"   # Слесарный ремонт всех остальных
GROUP_SPARES_CHERY = "-1003186058405" # Запчасти Chery, Jetour, Omoda, Tenet, Jaecoo
GROUP_SPARES_HAVAL = "-1003056030547" # Запчасти всех остальных

CHERY_ECOSYSTEM = {"Chery", "Jetour", "Omoda", "Tenet", "Jaecoo", "Чери", "Джетур", "Омода", "Тенет", "Джеку"}

router = Router()


@router.message(ConversationState.confirm)
async def ask_confirm(message: Message, state: FSMContext):
    """
    Show summary and ask for confirmation.
    """
    data = await state.get_data()
    name = data.get("name", "Клиент")
    intent = data.get("intent", "other")
    target_brand = data.get("target_brand")
    user_car_brand = data.get("user_car_brand")
    slots = data.get("slots", {})
    phone = data.get("phone", "")
    
    brand = target_brand or user_car_brand
    
    summary_parts = [f"{name}, правильно понял:"]
    
    intent_texts = {
        "buy_new": "Вы ищете новый",
        "buy_used": "Вы ищете б/у",
        "sell": "Вы хотите продать",
        "repair": "Вам нужен ремонт",
        "spares": "Вам нужны запчасти",
        "accounting": "У Вас вопрос по бухгалтерии",
        "other": "Ваш запрос",
    }
    summary_parts.append(intent_texts.get(intent, "Ваш запрос"))
    
    if brand:
        summary_parts.append(brand)
    
    if intent in ["buy_new", "buy_used"]:
        if slots.get("body"):
            summary_parts.append(slots["body"])
        if slots.get("budget_max"):
            budget = slots["budget_max"]
            if budget >= 1_000_000:
                summary_parts.append(f"до {budget / 1_000_000:.1f} млн")
            else:
                summary_parts.append(f"до {budget:,} руб.")
        if slots.get("drive"):
            summary_parts.append(f"({slots['drive']})")
    
    if intent == "repair" and slots.get("repair_type"):
        summary_parts.append(f"({slots['repair_type']})")
    
    if phone:
        phone_masked = phone[:4] + "****" + phone[-4:]
        summary_parts.append(f"Ваш номер {phone_masked}?") # Добавили знак вопроса
    else:
        summary_parts.append("?")

    summary = " ".join(summary_parts)
    await message.answer(summary)
    
    # Ждем подтверждения
    await state.set_state(ConversationState.confirm_final)


@router.message(ConversationState.confirm_final)
async def handle_final_confirm(message: Message, state: FSMContext, brand_matcher: BrandMatcher):
    """
    Process user confirmation (Yes/No) or correction.
    """
    text = (message.text or "").lower()
    positive = ["да", "верно", "ага", "угу", "правильно", "ок", "ok", "yes", "конечно"]
    negative = ["нет", "неверно", "ошибка", "no", "не"]
    
    # 1. Positive confirmation
    if any(kw in text for kw in positive):
        data = await state.get_data()
        
        name = data.get("name", "Клиент")
        intent = data.get("intent", "other")
        brand = data.get("target_brand") or data.get("user_car_brand")
        phone = data.get("phone", "")
        slots = data.get("slots", {})
        
        # 1. Save Lead
        lead = LeadDTO(
            user_id=message.from_user.id,
            name=name,
            intent=intent,
            brand=brand,
            phone=phone,
            slots=slots,
        )
        try:
            lead_dict = lead.model_dump()
            append_lead(lead_dict)
            logger.info(f"Lead saved for user {message.from_user.id}: intent={intent}, brand={brand}")
        except Exception as e:
            logger.error(f"Failed to save lead: {e}")
        
        # 2. Route to Telegram Group
        target_group = None
        
        # Нормализуем бренд для роутинга
        routing_brand = brand
        if brand:
             # Ищем соответствие в DEALER_BRANDS (точное или case-insensitive)
             for db in DEALER_BRANDS:
                 if db.lower() == brand.lower():
                     routing_brand = db
                     break
        
        logger.info(f"Routing lead: intent={intent}, brand={brand} -> normalized={routing_brand}")

        if intent == "buy_new":
            if routing_brand in DEALER_BRANDS:
                if routing_brand == "Chery": target_group = GROUP_CHERY
                elif routing_brand == "Jetour": target_group = GROUP_JETOUR
                elif routing_brand == "Haval": target_group = GROUP_HAVAL
            else:
                target_group = GROUP_PROBEG
        elif intent == "buy_used":
            target_group = GROUP_PROBEG
        elif intent == "fallback_other":
            target_group = GROUP_OTHER
        elif intent == "spares":
            if brand and any(b.lower() in brand.lower() for b in CHERY_ECOSYSTEM):
                target_group = GROUP_SPARES_CHERY
            else:
                target_group = GROUP_SPARES_HAVAL
        elif intent == "repair":
            repair_type = slots.get("repair_type", "").lower()
            if "кузов" in repair_type:
                target_group = GROUP_KUZOV
            else:
                if brand and any(b.lower() in brand.lower() for b in CHERY_ECOSYSTEM):
                    target_group = GROUP_STO_CHERY
                else:
                    target_group = GROUP_STO_HAVAL
        elif intent == "accounting":
            target_group = GROUP_ACCOUNTING

        # Send
        if target_group and str(target_group).startswith("-"):
            try:
                await message.bot.send_message(chat_id=target_group, text=format_lead_message(lead))
                logger.info(f"Lead sent to group {target_group}")
            except Exception as e:
                logger.error(f"Failed to send lead to group {target_group}: {e}")
        else:
            logger.warning(f"No target group found or invalid group ID for intent={intent}, brand={brand}")
        
        await message.answer("Менеджер свяжется с Вами в течение 10 минут.")
        await state.set_state(ConversationState.finished)
        return

    # 2. Check for brand correction (implicit or explicit)
    new_brand = brand_matcher.find_brand(message.text)
    is_negative = any(kw in text for kw in negative)
    
    if new_brand:
        data = await state.get_data()
        if new_brand != (data.get("target_brand") or data.get("user_car_brand")):
            logger.info(f"User corrected brand to {new_brand}.")
            data["target_brand"] = new_brand
            data["user_car_brand"] = new_brand
            data["non_dealer_notice_sent"] = False
            await state.set_data(data)
            
            await message.answer(f"Понял, исправляем на {new_brand}.")
            await ask_confirm(message, state)
            return

    # 3. Explicit Negative
    if is_negative:
        if "номер" in text or "телефон" in text:
             await state.set_state(ConversationState.collect_phone)
             await message.answer("Пожалуйста, введите верный номер телефона.")
             return
        else:
             await message.answer("Хорошо, давайте начнем сначала. Напишите /start.")
             await state.set_state(ConversationState.finished)
             return

    # 4. Unknown
    await message.answer(
        f"Не совсем понял Ваш ответ. Пожалуйста, подтвердите, что все верно, написав 'Да', "
        f"или укажите марку авто для исправления."
    )
