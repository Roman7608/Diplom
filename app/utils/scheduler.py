from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from loguru import logger
from app.fsm.states import ConversationState

scheduler = AsyncIOScheduler()

async def send_warning(bot: Bot, chat_id: int):
    """Отправляет предупреждение через 1 минуту тишины."""
    try:
        await bot.send_message(chat_id, "Могу ли я Вам еще чем-нибудь помочь?")
    except Exception as e:
        logger.error(f"Failed to send warning to {chat_id}: {e}")

async def send_goodbye(bot: Bot, chat_id: int, state: FSMContext):
    """Отправляет прощание через 2 минуты тишины и сбрасывает стейт."""
    try:
        await bot.send_message(chat_id, "До свидания!")
        await state.clear()
        # Опционально: можно вернуть в greeting, но обычно clear достаточно.
        # await state.set_state(ConversationState.greeting) 
    except Exception as e:
        logger.error(f"Failed to send goodbye to {chat_id}: {e}")

def reschedule_timeout(user_id: int, bot: Bot, state: FSMContext):
    """Сбрасывает и устанавливает таймеры заново."""
    warn_job_id = f"warn_{user_id}"
    bye_job_id = f"bye_{user_id}"

    # Удаляем старые задачи, если есть
    if scheduler.get_job(warn_job_id):
        scheduler.remove_job(warn_job_id)
    if scheduler.get_job(bye_job_id):
        scheduler.remove_job(bye_job_id)

    # Планируем новые
    warn_time = datetime.now() + timedelta(minutes=1)
    bye_time = datetime.now() + timedelta(minutes=2)

    scheduler.add_job(
        send_warning, 
        'date', 
        run_date=warn_time, 
        args=[bot, user_id], 
        id=warn_job_id
    )
    
    scheduler.add_job(
        send_goodbye, 
        'date', 
        run_date=bye_time, 
        args=[bot, user_id, state], 
        id=bye_job_id
    )

