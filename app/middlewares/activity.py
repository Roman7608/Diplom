from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from app.utils.scheduler import reschedule_timeout
from loguru import logger

class UserActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        # Получаем необходимые объекты из data
        bot = data.get("bot")
        state: FSMContext = data.get("state")
        
        if bot and state and event.from_user:
            try:
                # Обновляем таймеры
                reschedule_timeout(event.from_user.id, bot, state)
            except Exception as e:
                logger.error(f"Failed to reschedule timeout for user {event.from_user.id}: {e}")

        return await handler(event, data)

