# TECHNICAL SPECIFICATION — “Neuro-AutoConsultant” Telegram Bot (GigaChat Edition)

## Goal
Create an educational Telegram bot demonstrating clean conversational logic:
- FSM dialog (aiogram 3.x)
- LLM-router using **GigaChat API**
- BrandMatcher for car brand extraction
- Slot-filling approach
- Leads stored to JSON file
- Modular architecture, no monolithic handlers

Focus: clarity, structure, readability.

---

# 1. TECHNOLOGY STACK

- Python 3.11
- aiogram 3.x
- pydantic 2.x
- httpx
- loguru
- **GigaChat API OAuth**
- JSON file storage
- No Docker required

Run:
```bash
python -m app.main
```

---

# 2. PROJECT STRUCTURE

```text
project/
│
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── loader.py
│   ├── main.py
│
│   ├── fsm/
│   │   ├── __init__.py
│   │   ├── states.py
│
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── start.py
│   │   ├── detect_intent.py
│   │   ├── collect_brand.py
│   │   ├── collect_specs.py
│   │   ├── collect_repair_type.py
│   │   ├── collect_phone.py
│   │   ├── confirm.py
│
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── gigachat_client.py
│   │   ├── router.py
│   │   ├── prompts.py
│
│   ├── utils/
│   │   ├── brand_matcher.py
│   │   ├── phone.py
│   │   ├── logging.py
│   │   ├── leads_file.py
│
│   ├── models/
│       ├── __init__.py
│       ├── dto.py
│
├── requirements.txt
└── README.md
```

All files must contain working code (no empty stubs in final project).

---

# 3. CONFIG (GIGACHAT)

`app/config.py`:

```python
from pydantic import BaseSettings

class Settings(BaseSettings):
    BOT_TOKEN: str

    # GigaChat access
    GIGACHAT_CLIENT_ID: str
    GIGACHAT_AUTH_KEY: str
    GIGACHAT_SCOPE: str = "GIGACHAT_API_PERS"
    GIGACHAT_AUTH_URL: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    GIGACHAT_API_URL: str = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

    class Config:
        env_file = ".env"
```

`.env` example:

```env
BOT_TOKEN=...
GIGACHAT_CLIENT_ID=...
GIGACHAT_AUTH_KEY=...
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_AUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_API_URL=https://gigachat.devices.sberbank.ru/api/v1/chat/completions
```

---

# 4. FSM STATES

`app/fsm/states.py`:

```python
from aiogram.fsm.state import StatesGroup, State

class ConversationState(StatesGroup):
    greeting = State()           # ask name
    detect_intent = State()      # run router, decide branch
    collect_brand = State()      # ask for brand/model if missing
    collect_specs = State()      # ask for budget/specs
    collect_repair_type = State()# слесарный/кузовной
    collect_phone = State()      # ask & validate phone
    confirm = State()            # show summary + save lead
    finished = State()           # end of dialog
```

---

# 5. DIALOG LOGIC (HIGH-LEVEL)

## /start → greeting
- Clear FSM.
- Ask: “Как к Вам обращаться?”

## greeting → detect_intent
- Save name to FSM.
- Ask: “Что Вас интересует: покупка/продажа авто, ремонт, запчасти, бухгалтерия?”

## detect_intent
Handler in `handlers/detect_intent.py`:

1. Call `LLMRouter.classify_text(text)` → `RouterResult`.
2. Optionally apply `BrandMatcher.find_brand(text)` if brand still None.
3. Save to FSM: `intent`, `target_brand`, `user_car_brand`, `slots`.

Branching:

- If `intent in ["buy_new", "buy_used"]`:
  - If brand unknown → `collect_brand`
  - Else if important specs (budget_max) missing → `collect_specs`
  - Else → `collect_phone`

- If `intent == "repair"`:
  - If brand unknown → `collect_brand`
  - Else if `slots["repair_type"]` not set → `collect_repair_type`
  - Else → `collect_phone`

- If `intent == "spares"`:
  - If brand unknown → `collect_brand`
  - Then → `collect_phone`

- If `intent == "sell"`:
  - If brand unknown → `collect_brand`
  - Then → `collect_phone`

- If `intent == "accounting"`:
  - Immediately → `collect_phone`

- If `intent == "other"` or `confidence == "low"`:
  - Ask one clarifying question (e.g. “Правильно ли я понимаю, что Вас интересует ремонт автомобиля?”),
  - Call router again on next user input,
  - If still unclear → treat as generic + go to `collect_phone`.

---

# 6. HANDLERS DETAILS

## 6.1 start.py
- `/start` handler, sets `ConversationState.greeting`.

## 6.2 collect_brand.py
- In `collect_brand` state:
  - If intent in `["buy_new", "buy_used"]` → ask: “Какую марку/модель рассматриваете?”
  - If intent in `["repair", "spares", "sell"]` → ask: “Какой у Вас автомобиль (марка и модель)?”
- Use `BrandMatcher.find_brand(text)` to set `target_brand` or `user_car_brand`.
- Save original text to `slots["raw_model"]`.
- Then follow branching rules from Section 5.

## 6.3 collect_specs.py
- Only for `buy_new` / `buy_used`.
- Ask for `budget_max`:
  - e.g. “До какой суммы рассматриваете автомобиль?”
  - Extract integer from text.
- Optionally ask:
  - Body type: кроссовер/седан/хэтчбек/универсал.
  - Drive: 4x4 / передний / задний.
- After at least `budget_max` set → go to `collect_phone`.

## 6.4 collect_repair_type.py
- Ask: “Это слесарный ремонт (двигатель, подвеска, ТО) или кузовной (вмятины, покраска, после ДТП)?”
- Parse answer to:
  - `slots["repair_type"] = "слесарный"` or `"кузовной"`.
- Then → `collect_phone`.

## 6.5 collect_phone.py
- Ask: “Оставьте, пожалуйста, номер телефона, по которому Вам удобно принять звонок.”
- Validate using `utils/phone.py`:
  - Normalize to `+7XXXXXXXXXX`.
- If invalid → попросить повторить (до 3 попыток).
- Save `phone` in FSM, then → `confirm`.

## 6.6 confirm.py
- Read from FSM:
  - name, intent, brand (target or user_car_brand), slots, phone.
- Build human-friendly summary in Russian:
  - e.g. “Роман, правильно понял: Вы ищете новый кроссовер Chery до 2.5 млн. Ваш номер +7… Менеджер свяжется с Вами.”
- Send summary to user.
- Convert data into `LeadDTO`, then call `append_lead()` to save in `leads.json`.
- Set state to `finished`.

## 6.7 finished state
- Any new text:
  - Reply: “Чтобы начать новый запрос — напишите /start.”
  - On `/start` → clear FSM → `greeting`.

---

# 7. BRANDMATCHER

`utils/brand_matcher.py`:

- Mapping of normalized brand names to variants:

```python
BRANDS = {
    "Chery": ["chery", "черри", "чери"],
    "Haval": ["haval", "хавал", "хаовал"],
    "Lada": ["lada", "ваз", "лада"],
    "Kia":  ["kia", "киа"],
    # etc.
}
```

- Case-insensitive search.
- Avoid accidental matches inside longer words (e.g. use word boundaries for short tokens).
- Method:

```python
from typing import Optional

class BrandMatcher:
    def __init__(self):
        self.map = BRANDS

    def find_brand(self, text: str) -> Optional[str]:
        ...
```

---

# 8. LEAD STORAGE (JSON FILE)

`utils/leads_file.py`:

- Use `leads.json` in project root.
- Lead structure:

```json
{
  "user_id": 123456,
  "name": "Роман",
  "intent": "buy_new",
  "brand": "Chery",
  "phone": "+79991234567",
  "slots": {
    "budget_max": 2500000,
    "body": "кроссовер",
    "drive": "4x4"
  },
  "created_at": "2025-11-19T12:34:56"
}
```

- Implement:

```python
from pathlib import Path
import json
from datetime import datetime

LEADS_FILE = Path("leads.json")

def append_lead(lead: dict) -> None:
    if LEADS_FILE.exists():
        data = json.loads(LEADS_FILE.read_text(encoding="utf-8"))
    else:
        data = []
    lead["created_at"] = datetime.utcnow().isoformat()
    data.append(lead)
    LEADS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
```

---

# 9. DTO MODELS

`models/dto.py`:

```python
from pydantic import BaseModel
from typing import Any, Dict, Optional, Literal

class RouterResult(BaseModel):
    intent: Literal["buy_new","buy_used","sell","repair","spares","accounting","other"]
    target_brand: Optional[str] = None
    user_car_brand: Optional[str] = None
    slots: Dict[str, Any] = {}
    confidence: Literal["high","medium","low"] = "low"

class LeadDTO(BaseModel):
    user_id: int
    name: str
    intent: str
    brand: Optional[str]
    phone: str
    slots: Dict[str, Any]
```

---

# 10. GIGACHAT CLIENT

`llm/gigachat_client.py`:

```python
import httpx

async def get_gigachat_token(auth_key: str, auth_url: str, scope: str) -> str:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "Authorization": f"Basic {auth_key}",
    }
    data = {"scope": scope}

    async with httpx.AsyncClient(verify=False, timeout=20) as client:
        r = await client.post(auth_url, headers=headers, data=data)
        r.raise_for_status()
        return r.json()["access_token"]

async def gigachat_request(token: str, api_url: str, messages: list[dict]) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "model": "GigaChat",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 800,
    }

    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        r = await client.post(api_url, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()
```

---

# 11. LLM ROUTER

`llm/router.py`:

```python
import json
from loguru import logger
from app.config import Settings
from app.models.dto import RouterResult
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.gigachat_client import get_gigachat_token, gigachat_request

class LLMRouter:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def classify_text(self, text: str) -> RouterResult:
        try:
            token = await get_gigachat_token(
                self.settings.GIGACHAT_AUTH_KEY,
                self.settings.GIGACHAT_AUTH_URL,
                self.settings.GIGACHAT_SCOPE,
            )
        except Exception:
            logger.exception("Error getting GigaChat token")
            return RouterResult(intent="other", confidence="low")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]

        try:
            resp = await gigachat_request(
                token, self.settings.GIGACHAT_API_URL, messages
            )
            raw = resp["choices"][0]["message"]["content"]
        except Exception:
            logger.exception("Error calling GigaChat")
            return RouterResult(intent="other", confidence="low")

        try:
            json_text = extract_json(raw)
            data = json.loads(json_text)
            return RouterResult(**data)
        except Exception:
            logger.exception(f"Error parsing router JSON: {raw!r}")
            return RouterResult(intent="other", confidence="low")
```

Helper:

```python
def extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found")
    return text[start:end+1]
```

---

# 12. SYSTEM PROMPT

`llm/prompts.py` must define `SYSTEM_PROMPT`:

- Domain: автосалон, покупка/продажа авто, ремонт, запчасти, бухгалтерия.
- Must instruct:

> Отвечай ТОЛЬКО одним JSON-объектом по схеме RouterResult, без текста до или после.

Include few-shot examples for:
- покупка нового авто,
- ремонт,
- продажа,
- бухгалтерия,
- непонятный запрос.

---

# 13. LOGGING

`utils/logging.py` must configure loguru:
- console sink (+ optional file sink).
- log:
  - incoming messages,
  - FSM state changes,
  - router results,
  - GigaChat errors,
  - saved leads.

---

# 14. LOADER & MAIN

`loader.py`:
- load Settings
- create Bot, Dispatcher
- create LLMRouter, BrandMatcher
- register handlers.

`main.py`:
- run `dp.start_polling(bot)` inside `asyncio.run`.

---

# 15. REQUIREMENTS

`requirements.txt`:

```text
aiogram>=3.4
loguru
pydantic>=2
httpx
python-dotenv
```

---

# END OF SPEC
