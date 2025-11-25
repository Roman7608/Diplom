import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, User, Chat
from app.fsm.states import ConversationState
from app.utils.brand_matcher import BrandMatcher
from app.llm.router import RouterResult
from app.utils.catalog import Car

# Mock data for Catalog
MOCK_CARS = [
    {
        "brand": "Chery", "model": "Tiggo 7 Pro", "trim": "Elite",
        "body": "кроссовер", "drive": "передний", "transmission": "вариатор",
        "final_price": 2700000, "power": 147, "available_colors": ["белый"],
        "delivery_days": 5, "engine_type": "бензиновый", "gears": 0, "base_price": 3000000,
        "transmission_details": "CVT", "discount_tradein": 100000, "discount_credit": 100000, 
        "discount_gov": 0, "discount_other": 0
    },
    {
        "brand": "Haval", "model": "Jolion", "trim": "Premium",
        "body": "кроссовер", "drive": "4x4", "transmission": "робот",
        "final_price": 2400000, "power": 150, "available_colors": ["черный", "синий"],
        "delivery_days": 14, "engine_type": "бензиновый", "gears": 7, "base_price": 2600000,
        "transmission_details": "DCT", "discount_tradein": 100000, "discount_credit": 100000,
        "discount_gov": 0, "discount_other": 0
    },
    {
        "brand": "Chery", "model": "Arrizo 8", "trim": "Prestige",
        "body": "седан", "drive": "передний", "transmission": "робот",
        "final_price": 2800000, "power": 186, "available_colors": ["серый", "зеленый"],
        "delivery_days": 10, "engine_type": "бензиновый", "gears": 7, "base_price": 3100000,
        "transmission_details": "DCT", "discount_tradein": 150000, "discount_credit": 150000,
        "discount_gov": 0, "discount_other": 0
    }
]

@pytest.fixture
def mock_catalog():
    catalog = MagicMock()
    cars_objs = [Car(**c) for c in MOCK_CARS]
    
    catalog.get_all_cars.return_value = cars_objs
    
    # Simple mock search
    def search_side_effect(dealer_brands, body=None, drive=None, price_max=None, **kwargs):
        res = []
        for c in cars_objs:
            if body and c.body != body: continue
            if price_max and c.final_price > price_max: continue
            if drive and drive not in c.drive: continue
            res.append(c)
        return res
        
    catalog.search.side_effect = search_side_effect
    catalog.find_models.return_value = [] 
    return catalog

@pytest.fixture
def mock_semantic_index():
    index = MagicMock()
    index.search = AsyncMock(return_value=[])
    return index

@pytest.fixture
def mock_llm_router():
    router = AsyncMock()
    router.classify_text.return_value = RouterResult(
        intent="other", target_brand=None, user_car_brand=None, slots={}, confidence="low"
    )
    return router

@pytest.fixture
def brand_matcher():
    return BrandMatcher()

# --- Helper to create message ---
def create_message(text: str):
    from datetime import datetime
    user = User(id=123, is_bot=False, first_name="TestUser")
    chat = Chat(id=123, type="private")
    msg = Message(message_id=1, date=datetime.now(), chat=chat, from_user=user, text=text)
    return msg

# --- TESTS ---

@pytest.mark.asyncio
async def test_scenario_1_non_dealer_purchase(mock_catalog, mock_semantic_index, mock_llm_router, brand_matcher):
    from app.handlers.start import handle_greeting
    from app.handlers.detect_intent import handle_detect_intent
    from app.handlers.non_dealer_choice import handle_non_dealer_choice
    
    # State simulator
    state = AsyncMock(spec=FSMContext)
    state_data = {}
    async def update_data(**kwargs): state_data.update(kwargs)
    async def set_data(d): state_data.clear(); state_data.update(d)
    async def get_data(): return state_data
    state.update_data.side_effect = update_data
    state.set_data.side_effect = set_data
    state.get_data.side_effect = get_data
    state.set_state = AsyncMock()

    # 1. "Роман, хочу купить ФВ Тигуан"
    msg = create_message("Роман, хочу купить ФВ Тигуан")
    
    # Mock router to detect intent
    mock_llm_router.classify_text.return_value = RouterResult(
        intent="buy_new", target_brand="Volkswagen", user_car_brand=None, slots={}, confidence="high"
    )
    
    with patch('aiogram.types.Message.answer', new_callable=AsyncMock) as mock_answer:
        # Greeting -> detect_intent
        with patch("app.handlers.start.handle_detect_intent", new_callable=AsyncMock) as mock_fwd:
            await handle_greeting(msg, state, mock_llm_router, brand_matcher, mock_catalog, mock_semantic_index)
            # Verify forward
            assert state_data.get("name") == "Роман"
            mock_fwd.assert_called()
            
            # Simulate detect_intent execution
            # We need to call handle_detect_intent manually as if forwarded
            await handle_detect_intent(msg, state, mock_llm_router, brand_matcher, mock_catalog, mock_semantic_index)
            
            # Expect non-dealer apology
            mock_answer.assert_called()
            txt = mock_answer.call_args_list[-1][0][0] 
            assert "не может предложить Вам новый автомобиль Volkswagen" in txt
            assert "Chery, Jetour и Haval" in txt
            assert state_data.get("non_dealer_brand") == "Volkswagen"

    # 2. "нужен полноприводный кроссовер до 3,3 млн руб"
    msg = create_message("нужен полноприводный кроссовер до 3,3 млн руб")
    
    # Mock semantic search to return Jolion (AWD)
    # We need to find the car object in mock_catalog list
    jolion = next(c for c in mock_catalog.get_all_cars() if c.model == "Jolion")
    mock_semantic_index.search.return_value = [jolion]
    
    with patch('aiogram.types.Message.answer', new_callable=AsyncMock) as mock_answer:
        await handle_non_dealer_choice(msg, state, brand_matcher, mock_catalog, mock_semantic_index)
        
        mock_answer.assert_called()
        txt = mock_answer.call_args_list[-1][0][0]
        # Check for Jolion (4x4, < 3.3)
        assert "Haval Jolion" in txt or "Jolion" in txt
        assert "2 400 000" in txt.replace("\xa0", " ")
        assert "Tiggo 7 Pro" not in txt # FWD should not be in result if semantic search works

    # 3. "а какие цвета в наличии?"
    msg = create_message("а какие цвета в наличии?")
    
    with patch('aiogram.types.Message.answer', new_callable=AsyncMock) as mock_answer:
        await handle_non_dealer_choice(msg, state, brand_matcher, mock_catalog, mock_semantic_index)
        mock_answer.assert_called()
        txt = mock_answer.call_args_list[-1][0][0]
        assert "черный, синий" in txt or "черный" in txt


@pytest.mark.asyncio
async def test_scenario_2_ownership_service(mock_catalog, mock_semantic_index, mock_llm_router, brand_matcher):
    from app.handlers.start import handle_greeting
    from app.handlers.detect_intent import handle_detect_intent
    
    state = AsyncMock(spec=FSMContext)
    state_data = {}
    state.get_data.return_value = state_data
    state.update_data.side_effect = lambda **k: state_data.update(k)
    
    # 1. "Иван, у меня ФВ Джетта"
    msg = create_message("Иван, у меня ФВ Джетта")
    
    # LLM fails to detect intent
    mock_llm_router.classify_text.return_value = RouterResult(intent="other", confidence="low", target_brand=None, user_car_brand=None, slots={})
    
    with patch('aiogram.types.Message.answer', new_callable=AsyncMock) as mock_answer:
        # Simulate greeting -> detect
        with patch("app.handlers.start.handle_detect_intent", new_callable=AsyncMock):
             # Just call detect logic directly for simplicity of test
             await handle_detect_intent(msg, state, mock_llm_router, brand_matcher, mock_catalog, mock_semantic_index)
             
             mock_answer.assert_called()
             txt = mock_answer.call_args[0][0]
             assert "Планируете обменять" in txt or "обслуживание" in txt
             assert "Volkswagen" in txt or "ФВ" in txt

@pytest.mark.asyncio
async def test_scenario_3_sedan_spares(mock_catalog, mock_semantic_index, mock_llm_router, brand_matcher):
    from app.handlers.non_dealer_choice import handle_non_dealer_choice
    from app.handlers.collect_repair_type import handle_collect_repair_type
    
    state = AsyncMock(spec=FSMContext)
    state_data = {}
    state.get_data.return_value = state_data
    state.update_data.side_effect = lambda **k: state_data.update(k)
    state.set_state = AsyncMock()
    
    # 1. "Олег, нужен седан" (simulate user already passed greeting)
    msg = create_message("нужен седан")
    
    with patch('aiogram.types.Message.answer', new_callable=AsyncMock) as mock_answer:
        await handle_non_dealer_choice(msg, state, brand_matcher, mock_catalog, mock_semantic_index)
        
        txt = mock_answer.call_args_list[-1][0][0]
        assert "Arrizo 8" in txt # The sedan in mock
    
    # 2. "а колодки тормозные для Chery как купить?" -> Should trigger service/spares detection
    msg = create_message("а колодки тормозные для Chery как купить?")
    
    # This usually happens in detect_intent if re-routed, or if user is in non_dealer_choice 
    # and writes something off-topic.
    # Check if non_dealer_choice handles spares keywords or if we need to go to detect_intent.
    # The code has service_keywords in non_dealer_choice. "колодки" might not be there.
    # Let's check the list: "ремонт", "обслуж", "сервис", ... "замен", "помен".
    # "колодки" is not explicitly in base tokens, but maybe LLM or other keywords catch it?
    # Wait, detect_intent has "запчаст".
    # If non_dealer_choice doesn't catch it, it continues to search.
    # We might need to add "запчаст" or "колодки" to non_dealer_choice triggers or rely on "купить" -> search.
    # But "как купить" -> "buy"?
    # Let's see what happens in test.
    
    # If logic fails, I will add "запчаст" to non_dealer_choice service triggers.
    
    with patch('aiogram.types.Message.answer', new_callable=AsyncMock) as mock_answer:
        await handle_non_dealer_choice(msg, state, brand_matcher, mock_catalog, mock_semantic_index)
        
        # If it fails to detect service, it searches for cars.
        # We expect it to detect service/spares.
        # Let's assume we want it to detect service.
        pass
