from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from typing import Any, Awaitable, Callable, Optional
from app.config import Settings
from app.llm.router import LLMRouter
from app.llm.gigachat_client import init_token_manager
from app.utils.brand_matcher import BrandMatcher
from app.utils.catalog import CarCatalog
from app.utils.semantic_search import SemanticCarIndex
from app.utils.logging import setup_logging
from app.utils.scheduler import scheduler  # Import scheduler
from app.middlewares.activity import UserActivityMiddleware  # Import middleware
from app.handlers import (
    start,
    detect_intent,
    collect_brand,
    collect_specs,
    collect_repair_type,
    collect_phone,
    confirm,
    non_dealer_choice,
)
from loguru import logger


class DependencyMiddleware:
    """Middleware to inject dependencies into handlers."""
    
    def __init__(
        self,
        router_llm: LLMRouter,
        brand_matcher: BrandMatcher,
        catalog: Optional[CarCatalog] = None,
        semantic_index: Optional[SemanticCarIndex] = None,
    ):
        self.router_llm = router_llm
        self.brand_matcher = brand_matcher
        self.catalog = catalog
        self.semantic_index = semantic_index
    
    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any]
    ) -> Any:
        data["router_llm"] = self.router_llm
        data["brand_matcher"] = self.brand_matcher
        data["catalog"] = self.catalog
        data["semantic_index"] = self.semantic_index
        return await handler(event, data)


def load_bot() -> tuple[Bot, Dispatcher]:
    """
    Load bot, dispatcher, and register all handlers.
    Returns (bot, dispatcher) tuple.
    """
    # Setup logging
    setup_logging()
    
    # Load settings
    settings = Settings()
    logger.info("Settings loaded")
    
    # Инициализируем менеджер токенов GigaChat (критично для работы API)
    logger.info("🔄 Initializing GigaChatTokenManager...")
    init_token_manager(settings)
    logger.info("✅ GigaChatTokenManager initialized")
    
    # Create bot and dispatcher
    bot = Bot(token=settings.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Start scheduler
    logger.info("⏰ Starting AsyncIOScheduler...")
    scheduler.start()
    
    # Register user activity middleware (for timeouts)
    dp.message.middleware(UserActivityMiddleware())
    
    # Create services
    router_llm = LLMRouter(settings)
    brand_matcher = BrandMatcher()
    
    # Initialize catalog and semantic search
    catalog = None
    semantic_index = None
    
    try:
        logger.info("🔄 Initializing CarCatalog...")
        catalog = CarCatalog(settings=settings)
        car_count = len(catalog.get_all_cars())
        logger.info(f"✅ CarCatalog initialized: {car_count} cars loaded")
        
        if car_count == 0:
            logger.error("❌ CRITICAL: Catalog is empty!")
            raise ValueError("Catalog is empty - cannot proceed")
            
    except FileNotFoundError as e:
        logger.error(f"❌ CRITICAL: Catalog file not found: {e}")
        logger.error("❌ Bot cannot start without catalog file!")
        raise RuntimeError(f"Catalog file not found: {e}") from e
    except ValueError as e:
        logger.error(f"❌ CRITICAL: Catalog validation error: {e}")
        logger.error("❌ Bot cannot start with invalid catalog!")
        raise RuntimeError(f"Catalog validation failed: {e}") from e
    except Exception as e:
        logger.exception(f"❌ CRITICAL: Error initializing catalog: {type(e).__name__}: {e}")
        logger.error("❌ Bot cannot start without catalog!")
        raise RuntimeError(f"Failed to initialize catalog: {type(e).__name__}: {e}") from e
    
    # Инициализируем semantic search через GigaChat (с фоллбэком)
    if catalog:
        try:
            logger.info("🔄 Initializing SemanticCarIndex with GigaChat API...")
            semantic_index = SemanticCarIndex(catalog, settings)
            if semantic_index.index is not None:
                logger.info("✅✅✅ SemanticCarIndex initialized successfully - semantic search ENABLED via GigaChat")
            else:
                logger.warning("⚠️ SemanticCarIndex initialized but index is None. Semantic search DISABLED.")
                semantic_index = None
        except Exception as e:
            logger.error(f"⚠️ Failed to initialize SemanticCarIndex: {e}")
            logger.warning("⚠️ Bot will start WITHOUT semantic search (fallback to structural search).")
            semantic_index = None
    else:
        logger.error("❌ CRITICAL: Catalog is None - cannot initialize semantic search!")
        raise RuntimeError("Catalog is None - cannot start bot!")
    
    # Register dependency injection middleware (включая catalog и semantic_index)
    dp.message.middleware(DependencyMiddleware(router_llm, brand_matcher, catalog, semantic_index))
    
    # Register routers
    dp.include_router(start.router)
    dp.include_router(detect_intent.router)
    dp.include_router(collect_brand.router)
    dp.include_router(collect_specs.router)
    dp.include_router(collect_repair_type.router)
    dp.include_router(collect_phone.router)
    dp.include_router(confirm.router)
    dp.include_router(non_dealer_choice.router)
    
    logger.info("All handlers registered")
    
    return bot, dp

