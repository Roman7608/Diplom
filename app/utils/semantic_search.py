from typing import List, Optional
import faiss
import numpy as np
from loguru import logger
from app.utils.catalog import Car, CarCatalog
from app.config import Settings
from app.llm.gigachat_client import gigachat_embeddings


class SemanticCarIndex:
    """
    Семантический поиск автомобилей по векторным представлениям.
    Использует GigaChat API для создания эмбеддингов и Faiss для поиска.
    """
    
    def __init__(self, catalog: CarCatalog, settings: Settings):
        logger.info("🔄 Initializing SemanticCarIndex with GigaChat API...")
        self.catalog = catalog
        self.settings = settings
        self.cars: List[Car] = []
        self.index: Optional[faiss.Index] = None
        
        # Строим индекс при инициализации
        self._build_index()
    
    async def _get_embeddings(self, texts: List[str]) -> np.ndarray:
        if not texts:
            logger.error("❌ Empty texts list for embeddings")
            raise ValueError("Cannot get embeddings for empty texts list")
        
        batch_size = 10
        all_embeddings = []
        total_batches = (len(texts) + batch_size - 1) // batch_size
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_num = i // batch_size + 1
            logger.info(f"📦 Processing batch {batch_num}/{total_batches} ({len(batch)} texts)...")
            
            try:
                embeddings = await gigachat_embeddings(batch, self.settings)
                
                if not embeddings:
                    logger.error(f"❌ Empty embeddings received for batch {batch_num}")
                    raise ValueError(f"Empty embeddings for batch {batch_num}")
                
                all_embeddings.extend(embeddings)
                logger.info(f"✅ Batch {batch_num}/{total_batches} processed: {len(embeddings)} embeddings")
                
            except Exception as e:
                logger.error(f"❌ Error getting embeddings for batch {batch_num}: {e}")
                try:
                    logger.info("🔄 Retrying batch...")
                    embeddings = await gigachat_embeddings(batch, self.settings)
                    if not embeddings: raise ValueError("Retry failed")
                    all_embeddings.extend(embeddings)
                    logger.info(f"✅ Batch {batch_num}/{total_batches} processed after retry")
                except Exception as retry_error:
                    raise RuntimeError(f"Failed to get embeddings for batch {batch_num}: {retry_error}") from retry_error
        
        return np.array(all_embeddings, dtype=np.float32)
    
    def _build_index(self):
        import asyncio
        import nest_asyncio
        try: nest_asyncio.apply()
        except: pass
        
        self.cars = self.catalog.get_all_cars()
        logger.info(f"📋 Found {len(self.cars)} cars in catalog")
        
        if not self.cars:
            logger.error("❌ No cars in catalog - cannot build semantic index!")
            self.index = None
            raise RuntimeError("Catalog is empty - cannot build semantic index")
        
        descriptions = []
        for car in self.cars:
            desc = self._car_to_description(car)
            descriptions.append(desc)
        
        logger.info(f"📥 Getting embeddings for {len(descriptions)} cars via GigaChat API...")
        
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(asyncio.run, self._get_embeddings(descriptions))
                        embeddings = future.result(timeout=600)
                else:
                    embeddings = loop.run_until_complete(self._get_embeddings(descriptions))
            except RuntimeError:
                embeddings = asyncio.run(self._get_embeddings(descriptions))
            
            if embeddings.shape[0] != len(self.cars):
                raise RuntimeError(f"Embeddings count mismatch")
            
            dimension = embeddings.shape[1]
            logger.info(f"🔨 Building Faiss index with dimension={dimension}...")
            self.index = faiss.IndexFlatL2(dimension)
            faiss.normalize_L2(embeddings)
            self.index.add(embeddings.astype('float32'))
            logger.info(f"✅✅✅ Built Faiss index with {self.index.ntotal} vectors")
        except Exception as e:
            logger.exception(f"❌ Error building index: {e}")
            self.index = None
            raise RuntimeError(f"Failed to build semantic index: {e}") from e
    
    def _car_to_description(self, car: Car) -> str:
        """Преобразует автомобиль в текстовое описание для эмбеддинга."""
        drive_text = {
            "4x4": "полный привод",
            "awd": "полный привод",
            "fwd": "передний привод",
            "rwd": "задний привод",
        }.get(car.drive.lower(), car.drive)
        
        return (
            f"{car.brand} {car.model} {car.trim}, {car.body}, {drive_text}, "
            f"{car.engine_type}, {car.power} л.с., {car.transmission} {car.gears} ст, "
            f"цена {car.final_price} руб"
        )
    
    async def search(
        self,
        query: str,
        dealer_brands: set[str],
        body: Optional[str] = None,
        drive: Optional[str] = None,
        price_max: Optional[int] = None,
        power_min: Optional[int] = None,
        transmission: Optional[str] = None,
        gears: Optional[int] = None,
        engine_type: Optional[str] = None,
        price_min: Optional[int] = None,
        top_k: int = 10,
    ) -> List[Car]:
        if self.index is None or not self.cars:
            logger.warning("Semantic search not available, falling back to structural search")
            return self.catalog.search(
                dealer_brands, body, drive, price_max, 
                power_min, transmission, gears, engine_type, price_min
            )
        
        logger.info(f"🔍 Semantic search: query='{query[:100]}', top_k={top_k}")
        
        query_embeddings = await self._get_embeddings([query])
        query_embedding = query_embeddings[0].astype('float32').reshape(1, -1)
        faiss.normalize_L2(query_embedding)
        
        k = min(top_k, self.index.ntotal)
        distances, indices = self.index.search(query_embedding, k)
        
        candidates = [self.cars[idx] for idx in indices[0]]
        
        # Применяем структурные фильтры к кандидатам
        filtered = []
        for car in candidates:
            if car.brand not in dealer_brands: continue
            if body and body != "любой" and car.body.lower() != body.lower(): continue
            if drive:
                # Normalize drive (simplified)
                d_req = drive.lower()
                d_car = car.drive.lower()
                is_awd_req = any(x in d_req for x in ["4x4", "awd", "полн"])
                is_awd_car = any(x in d_car for x in ["4x4", "awd", "полн"])
                if is_awd_req != is_awd_car: continue # Rough check
                
            if price_max and car.final_price > price_max: continue
            if price_min and car.final_price < price_min: continue
            
            if power_min and car.power < (power_min * 0.9): continue
            
            # Transmission/Engine strict filtering is NOT applied here intentionally 
            # because semantic search is for "fuzzy" intents. 
            # If strict filtering is needed, non_dealer_choice switches to catalog.search.
            
            filtered.append(car)
        
        logger.info(f"Semantic search found {len(filtered)} cars after filtering")
        return filtered
