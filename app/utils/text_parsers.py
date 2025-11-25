import re
from typing import Optional

def parse_car_specs(text: str) -> tuple[Optional[str], Optional[str], Optional[int], bool, Optional[int], Optional[str], Optional[int], Optional[str]]:
    """
    Парсит характеристики автомобиля из текста.
    
    Returns:
        (body, drive, price_target, is_approximate, power_target, transmission, gears, engine_type)
    """
    text_lower = text.lower().replace('ё', 'е')
    
    # --- Парсинг типа кузова ---
    body = None
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
    for body_type, keywords in body_keywords.items():
        if any(kw in text_lower for kw in keywords):
            body = body_type
            break
    
    # --- Парсинг привода ---
    drive = None
    drive_keywords = {
        "4x4": ["полный привод", "полноприводн", "4x4", "4х4", "awd", "4wd", "полный"],
        "передний": ["передний привод", "переднеприводн", "передний", "fwd"],
        "задний": ["задний привод", "заднеприводн", "задний", "rwd"],
    }
    for drive_type, keywords in drive_keywords.items():
        if any(kw in text_lower for kw in keywords):
            drive = drive_type
            break
    
    # --- Парсинг типа двигателя ---
    engine_type = None
    engine_keywords = {
        "бензин": ["бензин", "бензиновый"],
        "дизель": ["дизель", "дизельный", "дт", "dt", "солярк"],
        "гибрид": ["гибрид", "hybrid"],
        "электро": ["электро", "electric", "ev"],
    }
    for e_type, keywords in engine_keywords.items():
        # Проверка на "не дизель" и т.п. сложна, пока ищем прямые вхождения
        if any(kw in text_lower for kw in keywords):
            engine_type = e_type
            break

    # --- Парсинг трансмиссии ---
    transmission = None
    trans_keywords = {
        "мкпп": ["механик", "механическ", "мкпп", "ручка", "manual"],
        "акпп": ["автомат", "акпп", "automatic", "гидротрансформатор"],
        "вариатор": ["вариатор", "cvt", "бесступенчаты"],
        "робот": ["робот", "dsg", "dct", "ркпп"],
    }
    for t_type, keywords in trans_keywords.items():
        if any(kw in text_lower for kw in keywords):
            transmission = t_type
            break
            
    # --- Парсинг количества передач ---
    gears = None
    # Ищем "6 ступенчатый", "8 ст", "7-ступенчатый"
    gears_match = re.search(r'(\d+)\s*[\-]?\s*(?:ст|ступ|pered)', text_lower)
    if gears_match:
        try:
            gears = int(gears_match.group(1))
        except ValueError:
            pass

    # --- Парсинг цены ---
    price_target = None
    is_approximate = False
    
    approximate_keywords = ["около", "примерно", "где-то", "где то", "порядка", "районе", "плюс минус", "+-", "~"]
    is_approximate = any(kw in text_lower for kw in approximate_keywords)
    
    # 1. "3,5 млн"
    mln_match = re.search(r'(\d+(?:[.,]\d+)?)\s*(?:млн|миллион|лям)', text_lower)
    if mln_match:
        val_str = mln_match.group(1).replace(',', '.')
        try:
            val = float(val_str)
            price_target = int(val * 1_000_000)
        except ValueError:
            pass
    
    # 2. "кк"
    if not price_target:
        kk_match = re.search(r'(\d+(?:[.,]\d+)?)\s*кк', text_lower)
        if kk_match:
            try:
                val = float(kk_match.group(1).replace(',', '.'))
                price_target = int(val * 1_000_000)
            except ValueError:
                pass

    # 3. Явные большие числа
    if not price_target:
        matches = re.findall(r'\b\d{1,3}(?:[ \.]\d{3}){1,3}\b|\b\d{5,9}\b', text)
        for m in matches:
            num_clean = m.replace(' ', '').replace('.', '')
            try:
                num = int(num_clean)
                if 500_000 <= num <= 30_000_000: 
                    price_target = num
                    break
            except ValueError:
                continue
                
    # --- Парсинг мощности (л.с.) ---
    power_target = None
    
    # Сначала проверяем явное указание "от X" (приоритет для power_min)
    from_power_match = re.search(r'от\s*(\d{2,3})', text_lower)
    if from_power_match:
         val = int(from_power_match.group(1))
         # Проверка разумности (чтобы не спутать с ценой)
         if 50 < val < 800: 
             power_target = val
    
    # Если "от" не нашли, ищем просто число рядом с единицами измерения
    if not power_target:
        power_match = re.search(r'(\d{2,3})\s*(?:л\.?с\.?|сил|hp|лошад)', text_lower)
        if power_match:
            val = int(power_match.group(1))
            if 50 < val < 800:
                power_target = val
    
    return body, drive, price_target, is_approximate, power_target, transmission, gears, engine_type


def is_search_query(text: str) -> bool:
    """Определяет, является ли текст поисковым запросом."""
    text_lower = text.lower()
    
    body, drive, price, _, power, trans, gears, engine = parse_car_specs(text)
    if any([body, drive, price, power, trans, gears, engine]):
        return True
        
    search_keywords = [
        "что есть", "что можете предложить", "подобрать", "подбери", "найди", 
        "альтернатив", "варианты", "посоветуй", "какие есть", "в наличии",
        "хочу купить", "ищу машину", "нужна машина", "покажи"
    ]
    return any(kw in text_lower for kw in search_keywords)

def is_power_query(text: str) -> bool:
    """Определяет, запрашивает ли пользователь мощные/быстрые авто."""
    text_lower = text.lower()
    keywords = ["мощн", "быстр", "динамич", "лошад", "сил", "спорт", "разгон"]
    # Исключаем случаи, когда пользователь просто задает фильтр "200 сил" (это power_target), 
    # нас интересует именно "самый мощный", "помощнее".
    # Но если он пишет "что самое мощное", это тоже должно сработать.
    
    if any(kw in text_lower for kw in keywords):
        return True
    return False

def is_expensive_query(text: str) -> bool:
    """Определяет, запрашивает ли пользователь самые дорогие авто."""
    text_lower = text.lower()
    keywords = ["дорог", "дороже", "максимальная цена", "подороже", "самый дорогой", "топ комплектаци", "full", "фулл"]
    return any(kw in text_lower for kw in keywords)
