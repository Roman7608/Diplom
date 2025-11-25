from pydantic import BaseModel
from typing import Any, Dict, Optional, Literal


class RouterResult(BaseModel):
    intent: Literal["buy_new", "buy_used", "sell", "repair", "spares", "accounting", "other"]
    target_brand: Optional[str] = None
    user_car_brand: Optional[str] = None
    slots: Dict[str, Any] = {}
    confidence: Literal["high", "medium", "low"] = "low"


class LeadDTO(BaseModel):
    user_id: int
    name: str
    intent: str
    brand: Optional[str]
    phone: str
    slots: Dict[str, Any]

