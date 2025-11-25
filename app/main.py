import asyncio
from app.loader import load_bot
from loguru import logger


async def main():
    """
    Main entry point - load bot and start polling.
    """
    try:
        bot, dp = load_bot()
        logger.info("Starting bot...")
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.exception(f"Bot error: {e}")
    finally:
        if 'bot' in locals():
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
