from collections import defaultdict
from typing import List
from app.utils.catalog import Car
from app.models.dto import LeadDTO

def format_lead_message(lead: LeadDTO) -> str:
    """
    Формирует сообщение для менеджера в телеграм-группу.
    """
    intent_map = {
        "buy_new": "Покупка НОВОГО",
        "buy_used": "Покупка С ПРОБЕГОМ",
        "sell": "Продажа (выкуп)",
        "repair": "Сервис/Ремонт",
        "spares": "Запчасти",
        "accounting": "Бухгалтерия",
        "other": "Другое",
        "fallback_other": "Непонятный запрос (требует уточнения)"
    }
    
    intent_str = intent_map.get(lead.intent, lead.intent)
    
    lines = [
        "🔔 <b>НОВАЯ ЗАЯВКА</b>",
        f"👤 <b>Клиент:</b> {lead.name}",
        f"📞 <b>Телефон:</b> {lead.phone}",
        f"🎯 <b>Интерес:</b> {intent_str}"
    ]
    
    if lead.brand:
        lines.append(f"🚗 <b>Марка:</b> {lead.brand}")
    
    if lead.slots:
        details = []
        if "model" in lead.slots: details.append(f"Модель: {lead.slots['model']}")
        if "body" in lead.slots: details.append(f"Кузов: {lead.slots['body']}")
        if "budget_max" in lead.slots: details.append(f"Бюджет: {lead.slots['budget_max']}")
        if "repair_type" in lead.slots: details.append(f"Тип ремонта: {lead.slots['repair_type']}")
        if "repair_details" in lead.slots: details.append(f"Детали: {lead.slots['repair_details']}")
        
        if details:
            lines.append("📝 <b>Подробности:</b>")
            lines.extend([f"- {d}" for d in details])
    
    lines.append(f"🆔 User ID: {lead.user_id}")
    
    return "\n".join(lines)

def format_model_response(found_models: List[Car]) -> str:
    """
    Формирует текстовый ответ с вариантами модели.
    """
    # Сортируем по цене
    found_models.sort(key=lambda x: x.final_price)
    
    # Группируем по модели для заголовка
    first_car = found_models[0]
    brand_model_name = f"{first_car.brand} {first_car.model}"
    
    response_text = f"Да, {brand_model_name} есть в наличии.\n\n"
    
    # Показываем топ-5 уникальных комплектаций (чтобы не дублировать одинаковые машины разных цветов)
    seen_trims = set()
    shown_count = 0
    
    for car in found_models:
        trim_key = (car.trim, car.engine_type, car.power, car.drive, car.transmission)
        if trim_key in seen_trims:
            continue
        seen_trims.add(trim_key)
        
        price_fmt = f"{car.final_price:,}".replace(",", " ")
        
        # Формируем строку описания скидок, если они есть
        discounts = []
        if car.discount_tradein: discounts.append(f"Трейд-ин: {car.discount_tradein:,}")
        if car.discount_credit: discounts.append(f"Кредит: {car.discount_credit:,}")
        if car.discount_gov: discounts.append(f"Гос: {car.discount_gov:,}")
        if car.discount_other: discounts.append(f"Спец: {car.discount_other:,}")
        
        price_details = f"{price_fmt} руб"
        if car.base_price and car.base_price > car.final_price:
             base_fmt = f"{car.base_price:,}".replace(",", " ")
             price_details += f" (РРЦ: {base_fmt} руб"
             if discounts:
                 price_details += f", Скидки: {', '.join(discounts)}"
             price_details += ")"
        elif discounts:
             price_details += f" (Скидки: {', '.join(discounts)})"

        response_text += f"• {car.trim} {car.engine_type} {car.power}л.с. {car.drive} — {price_details}\n"
        shown_count += 1
        if shown_count >= 5:
            break
    
    remaining = len(found_models) - shown_count
    if remaining > 0:
        response_text += f"... и ещё {remaining} вариантов.\n"
    
    # Цвета
    all_colors = set()
    for car in found_models:
        all_colors.update(car.available_colors)
    
    if all_colors:
        colors_str = ", ".join(sorted(list(all_colors)))
        response_text += f"\nВ наличии цвета: {colors_str}."
    
    # Срок поставки
    delivery_days = max((c.delivery_days for c in found_models if c.delivery_days), default=14)
    response_text += f"\nЕсли хотите другой цвет — срок поставки {delivery_days} дней.\n"
    
    response_text += "\nХотите рассчитать точную цену со всеми скидками или оформить бронирование?"
    
    return response_text


def build_non_dealer_prompt(brand: str) -> str:
    """
    Формирует фразу для клиентов, интересующихся недилерскими марками.
    """
    return (
        f"Компания \"АвтоЛидер\" не может предложить Вам новый автомобиль {brand}. "
        "Зато у нас есть новые автомобили Chery, Jetour и Haval. "
        "Что из этого Вас интересует? "
        f"Если же Вы всё-таки хотите купить автомобиль {brand}, могу организовать Вам звонок из отдела автомобилей с пробегом."
    )


def format_color_overview(cars: List[Car]) -> str:
    """
    Формирует ответ по доступным цветам для перечисленных моделей.
    """
    grouped: dict[tuple[str, str], List[Car]] = defaultdict(list)
    for car in cars:
        grouped[(car.brand, car.model)].append(car)
    
    lines: List[str] = []
    for (brand, model), model_cars in grouped.items():
        colors = sorted({color for c in model_cars for color in c.available_colors if color})
        if colors:
            color_str = ", ".join(colors)
            lines.append(f"{brand} {model}: {color_str}")
        else:
            delivery_days = min((c.delivery_days or 14) for c in model_cars) if model_cars else 14
            lines.append(f"{brand} {model}: в наличии нет. Срок поставки {delivery_days} дней.")
    
    if not lines:
        return "По указанным моделям сейчас нет информации о цветах."
    
    response = "По вашим моделям доступны такие цвета:\n"
    response += "\n".join(lines)
    return response
