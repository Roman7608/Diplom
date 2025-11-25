from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List
import pandas as pd
import numpy as np
from loguru import logger
from app.config import Settings


@dataclass
class Car:
    """Модель автомобиля из каталога."""
    brand: str
    model: str
    trim: str               # Комплектация
    body: str               # Тип кузова
    drive: str              # Привод (AWD/4WD/FWD и т.п.)
    transmission: str       # Тип трансмиссии (МКПП, АКПП, РКПП, Вариатор)
    transmission_details: str  # Тип трансмиссии подробно (6DCT, CVT и т.д.)
    gears: Optional[int]    # Число передач
    engine_type: str        # Тип двигателя
    power: int              # Мощность, л.с.
    final_price: int        # Цена итого, руб (с учётом всех скидок)
    base_price: int         # Базовая цена
    discount_tradein: int   # Скидка трейд-ин
    discount_credit: int    # Скидка кредит
    discount_gov: int       # Скидка госпрограмма
    discount_other: int     # Скидка иная
    available_colors: List[str]  # Список цветов в наличии
    delivery_days: Optional[int] # Срок поставки


class CarCatalog:
    def __init__(self, settings: Optional[Settings] = None, catalog_path: Optional[str] = None):
        if catalog_path is None:
            if settings is None:
                settings = Settings()
            catalog_path = settings.AUTO_CATALOG_PATH
        
        catalog_path = Path(catalog_path)
        logger.info(f"🔍 Looking for catalog file at: {catalog_path.absolute()}")
        
        if not catalog_path.exists():
            logger.error(f"❌ Catalog file not found: {catalog_path.absolute()}")
            self._cars: List[Car] = []
            raise FileNotFoundError(f"Catalog file not found: {catalog_path.absolute()}")
        
        self._cars = self._load_from_excel(catalog_path)
        logger.info(f"✅ Loaded {len(self._cars)} cars from catalog")

    def _load_from_excel(self, path: Path) -> List[Car]:
        try:
            try:
                df_specs = pd.read_excel(path, sheet_name="характеристики")
            except ValueError:
                df_specs = pd.read_excel(path, sheet_name="Характеристики")
            
            df_prices = pd.read_excel(path, sheet_name="цены_скидки")
            df_availability = pd.read_excel(path, sheet_name="наличие")
            
            # Merge specs and prices
            df_merged = pd.merge(
                df_specs,
                df_prices,
                on=["Бренд", "Модель", "Комплектация"],
                how="inner"
            )
            
            # Merge availability
            df_merged = pd.merge(
                df_merged,
                df_availability,
                on=["Бренд", "Модель", "Комплектация"],
                how="left"
            )
            
            cars = []
            for idx, row in df_merged.iterrows():
                try:
                    prices = self._calculate_prices(row)
                    if not prices: continue
                    
                    final_price, base_price, d_tradein, d_credit, d_gov, d_other = prices
                    
                    color_col = row.get("Цвет") or row.get("цвет в наличии") or row.get("цвет") or ""
                    available_colors = self._parse_colors(color_col)
                    
                    delivery_col = row.get("Срок поставки") or row.get("поставки, дней") or row.get("поставки") or row.get("Срок поставки, дней")
                    delivery_days = self._parse_delivery_days(delivery_col)
                    
                    power = self._parse_int(row.get("Мощность, л.с."))
                    if pd.isna(power) or power <= 0: continue
                    
                    car = Car(
                        brand=str(row["Бренд"]).strip().title(),  # Нормализуем бренд (JETOUR -> Jetour)
                        model=str(row["Модель"]).strip(),
                        trim=str(row["Комплектация"]).strip(),
                        body=str(row.get("Тип кузова") or row.get("Кузов", "")).strip(),
                        drive=str(row.get("Привод", "")).strip(),
                        transmission=str(row.get("Тип трансмиссии", "")).strip(),
                        transmission_details=str(row.get("Тип трансмиссии подробно", "")).strip(),
                        gears=self._parse_int(row.get("Число передач")),
                        engine_type=str(row.get("Тип двигателя", "")).strip(),
                        power=int(power),
                        final_price=int(final_price),
                        base_price=int(base_price),
                        discount_tradein=int(d_tradein),
                        discount_credit=int(d_credit),
                        discount_gov=int(d_gov),
                        discount_other=int(d_other),
                        available_colors=available_colors,
                        delivery_days=delivery_days,
                    )
                    cars.append(car)
                except Exception as e:
                    logger.warning(f"⚠️  Error parsing row {idx}: {e}")
                    continue
            
            return cars
        except Exception as e:
            logger.exception(f"Error loading catalog from {path}: {e}")
            return []

    def _calculate_prices(self, row: pd.Series) -> Optional[tuple]:
        """Возвращает (final, base, tradein, credit, gov, other)"""
        # 1. Base price
        base_price_cols = ["цена базовая, руб", "Цена, руб", "Цена базовая, руб"]
        base_price = 0.0
        for col in base_price_cols:
            if col in row and not pd.isna(row[col]):
                try:
                    base_price = float(row[col])
                    break
                except: continue
        
        if base_price <= 0: return None

        # 2. Discounts
        def get_val(cols):
            for c in cols:
                if c in row and not pd.isna(row[c]):
                    try: return float(row[c])
                    except: pass
            return 0.0

        d_tradein = get_val(["скидка по трейд-ин, руб", "Скидка trade-in, руб"])
        d_credit = get_val(["скидка кредит, руб", "Скидка кредит, руб"])
        d_gov = get_val(["скидка господдержка, руб", "Скидка господдержка, руб"])
        d_other = get_val(["скидка иная, руб", "Скидка иная, руб"])
        
        total_discount = d_tradein + d_credit + d_gov + d_other
        
        # 3. Final price (check if explicit column exists, else calc)
        final_price = 0.0
        final_price_cols = ["Цена итого, руб (с учетом всех скидок)", "Цена итого, руб", "Цена итого"]
        found_final = False
        for col in final_price_cols:
            if col in row and not pd.isna(row[col]):
                try:
                    final_price = float(row[col])
                    if final_price > 0:
                        found_final = True
                        break
                except: continue
        
        if not found_final:
            final_price = base_price - total_discount
            
        return (final_price, base_price, d_tradein, d_credit, d_gov, d_other)

    def _parse_colors(self, colors_str: str) -> List[str]:
        if pd.isna(colors_str) or not str(colors_str).strip(): return []
        colors = [c.strip().lower() for c in str(colors_str).split(",")]
        return [c for c in colors if c]

    def _parse_delivery_days(self, value) -> Optional[int]:
        return self._parse_int(value)

    def _parse_int(self, value) -> Optional[int]:
        if pd.isna(value): return None
        try: return int(float(value))
        except: return None

    def search(
        self,
        dealer_brands: set[str],
        body: Optional[str] = None,
        drive: Optional[str] = None,
        price_max: Optional[int] = None,
        power_min: Optional[int] = None,
        transmission: Optional[str] = None,
        gears: Optional[int] = None,
        engine_type: Optional[str] = None,
        price_min: Optional[int] = None,
    ) -> List[Car]:
        results = []
        for car in self._cars:
            if car.brand not in dealer_brands: continue
            
            if body and body != "любой" and car.body.lower() != body.lower(): continue
            
            if drive:
                d_lower = drive.lower()
                car_d_lower = car.drive.lower()
                is_awd_req = any(x in d_lower for x in ["4x4", "4wd", "awd", "полн"])
                is_fwd_req = any(x in d_lower for x in ["fwd", "передн"])
                is_awd_car = any(x in car_d_lower for x in ["4x4", "4wd", "awd", "полн"])
                is_fwd_car = any(x in car_d_lower for x in ["fwd", "передн"])
                if is_awd_req and not is_awd_car: continue
                if is_fwd_req and not is_fwd_car: continue
            
            if price_max and car.final_price > price_max: continue
            if price_min and car.final_price < price_min: continue
            
            if power_min:
                 if car.power < (power_min * 0.9): continue

            if transmission:
                t_req = transmission.lower()
                t_car = car.transmission.lower()
                is_mt_req = any(x in t_req for x in ["механ", "мкпп", "mt", "manual"])
                is_at_req = any(x in t_req for x in ["автомат", "акпп", "at", "automatic"])
                is_cvt_req = any(x in t_req for x in ["вариатор", "cvt"])
                is_robot_req = any(x in t_req for x in ["робот", "dct", "dsg", "ркпп"])
                
                is_mt_car = "мкпп" in t_car or "mt" in t_car
                is_at_car = "акпп" in t_car or "at" in t_car
                is_cvt_car = "вариатор" in t_car or "cvt" in t_car
                is_robot_car = "робот" in t_car or "dct" in t_car or "ркпп" in t_car
                
                if is_mt_req and not is_mt_car: continue
                if is_at_req and not is_at_car: continue 
                if is_cvt_req and not is_cvt_car: continue
                if is_robot_req and not is_robot_car: continue
                
            if gears:
                if not car.gears or car.gears != gears: continue
                
            if engine_type:
                e_req = engine_type.lower()
                e_car = car.engine_type.lower()
                # Простая проверка вхождения
                if e_req not in e_car: continue
            
            results.append(car)
        return results

    def get_all_cars(self) -> List[Car]:
        return self._cars.copy()
        
    def find_models(self, text: str, dealer_brands: set[str]) -> List[Car]:
        results = []
        text_lower = text.lower()
        for car in self._cars:
            if car.brand not in dealer_brands: continue
            full_name = f"{car.brand} {car.model}".lower()
            if full_name in text_lower:
                results.append(car)
                continue
            model_lower = car.model.lower()
            if len(model_lower) > 2 and model_lower in text_lower:
                if re_search_word(model_lower, text_lower):
                    results.append(car)
        return results

def re_search_word(word: str, text: str) -> bool:
    import re
    pattern = r'(^|\s|[^a-zA-Z0-9а-яА-Я])' + re.escape(word) + r'($|\s|[^a-zA-Z0-9а-яА-Я])'
    return bool(re.search(pattern, text))

def pick_top3_offers(
    cars: List[Car],
    price_target: Optional[int] = None,
    is_approximate: bool = False,
    sort_by: str = "price_mix" # "price_mix" or "power_desc"
) -> List[Car]:
    if not cars: return []
    
    # Filter by price first if needed (already done in search, but safeguard for fallback logic)
    filtered = [c for c in cars]
    if price_target:
        if is_approximate:
            center = price_target
            band = int(center * 0.10)
            lower = center - band
            upper = center + band
            filtered = [c for c in cars if lower <= c.final_price <= upper]
            # Fallback inside pick
            if not filtered:
                 filtered = sorted(cars, key=lambda c: abs(c.final_price - center))[:5]
        else:
             filtered = [car for car in cars if car.final_price <= price_target]
             if not filtered: # Fallback for strict price
                  filtered = [car for car in cars if car.final_price <= price_target * 1.2]
    
    if not filtered:
        filtered = cars # Ultimate fallback
        
    # --- SORTING STRATEGY ---
    if sort_by == "power_desc":
        # Сортируем по мощности (убывание), затем по цене (возрастание)
        sorted_cars = sorted(filtered, key=lambda c: (-c.power, c.final_price))
        unique_models = []
        seen_models = set()
        for car in sorted_cars:
            if car.model not in seen_models:
                unique_models.append(car)
                seen_models.add(car.model)
            if len(unique_models) == 3:
                break
        return unique_models
    
    elif sort_by == "price_desc":
        # Сортируем по цене (убывание)
        sorted_cars = sorted(filtered, key=lambda c: -c.final_price)
        unique_models = []
        seen_models = set() # Чтобы не показывать одну и ту же машину с разницей в 1 рубль
        for car in sorted_cars:
            # Можно показывать разные комплектации одной модели?
            # ТЗ: "3 самых дорогих автомобиля". Лучше разные модели или разные комплектации.
            # Давайте уникальные (модель, комплектация).
            key = (car.model, car.trim) 
            if key not in seen_models:
                unique_models.append(car)
                seen_models.add(key)
            if len(unique_models) == 3:
                break
        return unique_models

    else: # "price_mix" (default)
        sorted_cars = sorted(filtered, key=lambda c: c.final_price)
        if len(sorted_cars) <= 3: return sorted_cars
        
        # 2 cheapest + 1 most expensive
        result = sorted_cars[:2]
        most_expensive = sorted_cars[-1]
        
        # Ensure we don't duplicate if list is small (handled by <=3 check but double check)
        if most_expensive not in result: 
            result.append(most_expensive)
            
        return result
