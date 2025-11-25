import httpx
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger
import asyncio
import uuid
from app.config import Settings


class GigaChatTokenManager:
    """
    Менеджер для управления access_token GigaChat API.
    Автоматически обновляет токен при истечении (примерно раз в 30 минут).
    """
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self._access_token: Optional[str] = None
        self._expires_at: Optional[datetime] = None
        self._lock = asyncio.Lock()  # Для thread-safe обновления токена
    
    async def _request_new_token(self, retry_count: int = 0) -> tuple[str, datetime]:
        """
        Запрашивает новый access_token через /oauth endpoint.
        
        Args:
            retry_count: Количество попыток (для exponential backoff при 429)
        
        Returns:
            tuple: (access_token, expires_at)
        """
        import asyncio
        
        # Генерируем RqUID (обязательный заголовок для GigaChat API)
        rq_uid = str(uuid.uuid4())
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": rq_uid,
            "Authorization": f"Basic {self.settings.GIGACHAT_AUTH_KEY}",
        }
        data = {"scope": self.settings.GIGACHAT_SCOPE}
        
        # Логируем детали запроса перед отправкой
        logger.info(
            "Requesting GigaChat token: url=%s, scope=%s, auth_header_prefix=%s, rq_uid=%s",
            self.settings.GIGACHAT_AUTH_URL,
            self.settings.GIGACHAT_SCOPE,
            "Basic" if self.settings.GIGACHAT_AUTH_KEY else "MISSING",
            rq_uid,
        )
        if retry_count > 0:
            logger.info(f"   Retry attempt {retry_count}")
        
        async with httpx.AsyncClient(
            timeout=30.0,
            verify=self.settings.GIGACHAT_VERIFY_SSL,
        ) as client:
            try:
                r = await client.post(self.settings.GIGACHAT_AUTH_URL, headers=headers, data=data)
                
                # Обработка ошибок по статус-кодам
                if r.status_code != 200:
                    response_text = r.text
                    logger.error(f"❌ GigaChat OAuth error: {r.status_code}")
                    logger.error(f"   Method: POST")
                    logger.error(f"   URL: {self.settings.GIGACHAT_AUTH_URL}")
                    logger.error(f"   Response body: {response_text[:500] if response_text else '(empty)'}")
                    logger.error(f"   Response headers: {dict(r.headers)}")
                    logger.error(f"   Request data: scope={self.settings.GIGACHAT_SCOPE}")
                    logger.error(f"   Auth key (first 30 chars): {self.settings.GIGACHAT_AUTH_KEY[:30]}...")
                    
                    # Обработка 429 Too Many Requests с exponential backoff
                    if r.status_code == 429:
                        if retry_count < 3:  # Максимум 3 попытки
                            wait_time = (2 ** retry_count) * 5  # 5s, 10s, 20s
                            logger.warning(f"⚠️  Rate limit (429), waiting {wait_time}s before retry {retry_count + 1}/3...")
                            await asyncio.sleep(wait_time)
                            return await self._request_new_token(retry_count + 1)
                        else:
                            logger.error("❌ Rate limit exceeded after 3 retries")
                            raise RuntimeError("GigaChat OAuth rate limit exceeded. Please wait before retrying.")
                    
                    # Ошибки 400, 401, 403 - не делаем retry, это проблема с credentials
                    if r.status_code == 400:
                        logger.error("   ⚠️  CRITICAL: 400 Bad Request - Invalid credentials!")
                        logger.error("   Possible causes:")
                        logger.error("   1. GIGACHAT_AUTH_KEY expired or invalid")
                        logger.error("   2. Invalid GIGACHAT_AUTH_KEY format (should be base64(client_id:client_secret))")
                        logger.error("   3. Invalid GIGACHAT_SCOPE (should be 'GIGACHAT_API_PERS')")
                        logger.error("   4. Client ID doesn't match the auth key")
                        logger.error("   🔧 ACTION REQUIRED:")
                        logger.error("      - Go to https://developers.sber.ru/studio")
                        logger.error("      - Navigate to 'Настройка API' (API Settings)")
                        logger.error("      - Click 'Получить новый ключ' (Get new key)")
                        logger.error("      - Update GIGACHAT_AUTH_KEY in .env file")
                        logger.error("   ⚠️  Retry will NOT help - credentials must be fixed first!")
                    elif r.status_code == 401:
                        logger.error("   Authentication failed - check GIGACHAT_AUTH_KEY")
                        logger.error("   ⚠️  Retry will NOT help - credentials must be fixed first!")
                    elif r.status_code == 403:
                        logger.error("   Access forbidden - check GIGACHAT_SCOPE and account permissions")
                        logger.error("   ⚠️  Retry will NOT help - permissions must be fixed first!")
                
                r.raise_for_status()
                payload = r.json()
                
                if "access_token" not in payload:
                    logger.error(f"❌ No access_token in response: {payload}")
                    raise ValueError("No access_token in GigaChat response")
                
                access_token = payload["access_token"]
                
                # Получаем expires_in (обычно ~1800 секунд = 30 минут)
                expires_in = int(payload.get("expires_in", 1800))
                # Вычитаем 60 секунд буфера для безопасности
                expires_at = datetime.now() + timedelta(seconds=expires_in - 60)
                
                logger.info(f"✅ GigaChat access_token obtained successfully")
                logger.debug(f"   Token expires at: {expires_at}")
                logger.debug(f"   Expires in: {expires_in} seconds")
                
                return access_token, expires_at
                
            except httpx.HTTPStatusError as e:
                logger.error(f"❌ HTTP error getting GigaChat token: {e.response.status_code}")
                logger.error(f"   Method: POST")
                logger.error(f"   URL: {self.settings.GIGACHAT_AUTH_URL}")
                logger.error(f"   Response: {e.response.text[:500]}")
                raise
            except Exception as e:
                logger.exception(f"❌ Error getting GigaChat token: {type(e).__name__}: {e}")
                raise
    
    async def get_access_token(self) -> str:
        """
        Получает валидный access_token.
        Если токен есть и ещё не истёк — возвращает его.
        Если токена нет или он истёк — запрашивает новый через /oauth.
        
        Returns:
            str: Валидный access_token
        """
        async with self._lock:
            now = datetime.now()
            
            # Проверяем, есть ли валидный токен
            if self._access_token is not None and self._expires_at is not None:
                if now < self._expires_at:
                    logger.debug(f"✅ Using cached access_token (expires at {self._expires_at})")
                    return self._access_token
                else:
                    logger.info(f"🔄 Access_token expired at {self._expires_at}, requesting new one...")
            
            # Запрашиваем новый токен
            self._access_token, self._expires_at = await self._request_new_token()
            return self._access_token


# Глобальный экземпляр менеджера (будет инициализирован в loader.py)
_token_manager: Optional[GigaChatTokenManager] = None


def init_token_manager(settings: Settings) -> None:
    """
    Инициализирует глобальный менеджер токенов.
    Должен быть вызван один раз при старте приложения.
    """
    global _token_manager
    _token_manager = GigaChatTokenManager(settings)
    logger.info("✅ GigaChatTokenManager initialized")


async def get_access_token() -> str:
    """
    Получает валидный access_token через глобальный менеджер.
    
    Returns:
        str: Валидный access_token
    """
    if _token_manager is None:
        raise RuntimeError("GigaChatTokenManager not initialized. Call init_token_manager() first.")
    return await _token_manager.get_access_token()


async def gigachat_chat(messages: list[dict], settings: Settings) -> dict:
    """
    Выполняет запрос к GigaChat API для генерации текста (chat completions).
    Использует модель GigaChat.
    
    Args:
        messages: Список сообщений в формате [{"role": "system/user", "content": "..."}, ...]
        settings: Настройки приложения
    
    Returns:
        dict: JSON-ответ от GigaChat API
    """
    token = await get_access_token()
    
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
    
    logger.debug(f"📤 Sending chat request to {settings.GIGACHAT_API_URL}")
    logger.debug(f"   Model: GigaChat2Lite")
    logger.debug(f"   Messages count: {len(messages)}")
    
    async with httpx.AsyncClient(
        timeout=30.0,
        verify=settings.GIGACHAT_VERIFY_SSL,
    ) as client:
        try:
            r = await client.post(settings.GIGACHAT_API_URL, json=payload, headers=headers)
            
            # Детальное логирование ошибок
            if r.status_code not in (200, 201):
                response_text = r.text
                logger.error(f"❌ GigaChat chat API error: {r.status_code}")
                logger.error(f"   Method: POST")
                logger.error(f"   URL: {settings.GIGACHAT_API_URL}")
                logger.error(f"   Response body: {response_text[:500] if response_text else '(empty)'}")
                logger.error(f"   Response headers: {dict(r.headers)}")
                
                if r.status_code == 400:
                    logger.error("   Possible causes: invalid request format, model name, or parameters")
                elif r.status_code == 401:
                    logger.error("   Authentication failed - access_token may be invalid or expired")
                elif r.status_code == 403:
                    logger.error("   Access forbidden - check account permissions and token limits")
                elif r.status_code == 429:
                    logger.error("   Rate limit exceeded - too many requests")
            
            r.raise_for_status()
            return r.json()
            
        except httpx.HTTPStatusError as e:
            logger.error(f"❌ HTTP error calling GigaChat chat API: {e.response.status_code}")
            logger.error(f"   Method: POST")
            logger.error(f"   URL: {settings.GIGACHAT_API_URL}")
            logger.error(f"   Response: {e.response.text[:500]}")
            raise
        except Exception as e:
            logger.exception(f"❌ Error calling GigaChat chat API: {type(e).__name__}: {e}")
            raise


async def gigachat_embeddings(texts: list[str], settings: Settings) -> list[list[float]]:
    """
    Получает эмбеддинги для текстов через GigaChat Embeddings API.
    
    Args:
        texts: Список текстов для получения эмбеддингов
        settings: Настройки приложения
    
    Returns:
        list[list[float]]: Список векторов эмбеддингов (каждый вектор - список float)
    """
    if not texts:
        logger.error("❌ Empty texts list for embeddings")
        raise ValueError("Cannot get embeddings for empty texts list")
    
    token = await get_access_token()
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    
    payload = {
        "model": "Embeddings",
        "input": texts,
    }
    
    logger.debug(f"📤 Requesting embeddings for {len(texts)} texts from {settings.GIGACHAT_EMBEDDINGS_URL}")
    
    async with httpx.AsyncClient(
        timeout=120.0,
        verify=settings.GIGACHAT_VERIFY_SSL,
    ) as client:
        try:
            r = await client.post(settings.GIGACHAT_EMBEDDINGS_URL, json=payload, headers=headers)
            
            # Детальное логирование ошибок
            if r.status_code not in (200, 201):
                response_text = r.text
                logger.error(f"❌ GigaChat embeddings API error: {r.status_code}")
                logger.error(f"   Method: POST")
                logger.error(f"   URL: {settings.GIGACHAT_EMBEDDINGS_URL}")
                logger.error(f"   Response body: {response_text[:500] if response_text else '(empty)'}")
                logger.error(f"   Response headers: {dict(r.headers)}")
                logger.error(f"   Texts count: {len(texts)}")
                
                if r.status_code == 400:
                    logger.error("   Possible causes:")
                    logger.error("   1. Invalid request format or model name")
                    logger.error("   2. Empty or invalid texts in input")
                    logger.error("   3. Token package for Embeddings not purchased or expired")
                elif r.status_code == 401:
                    logger.error("   Authentication failed - access_token may be invalid or expired")
                elif r.status_code == 403:
                    logger.error("   Access forbidden - check if Embeddings token package is purchased")
                elif r.status_code == 429:
                    logger.error("   Rate limit exceeded or token balance exhausted")
            
            r.raise_for_status()
            response = r.json()
            
            logger.debug(f"✅ GigaChat embeddings API response keys: {list(response.keys())}")
            
            # GigaChat может возвращать embeddings в разных форматах
            if "data" in response:
                # Формат: {"data": [{"embedding": [...]}, ...]}
                embeddings = [item["embedding"] for item in response["data"]]
                logger.debug(f"Extracted {len(embeddings)} embeddings from 'data' field")
                return embeddings
            elif "embeddings" in response:
                # Альтернативный формат: {"embeddings": [[...], [...]]}
                embeddings = response["embeddings"]
                logger.debug(f"Extracted {len(embeddings)} embeddings from 'embeddings' field")
                return embeddings
            elif isinstance(response, list):
                # Прямой список эмбеддингов
                logger.debug(f"Response is direct list with {len(response)} embeddings")
                return response
            else:
                # Неизвестный формат
                logger.error(f"Unknown response format: {response.keys() if isinstance(response, dict) else type(response)}")
                logger.error(f"Response sample: {str(response)[:500]}")
                raise ValueError(f"Unknown GigaChat embeddings response format: {list(response.keys()) if isinstance(response, dict) else type(response)}")
                
        except httpx.HTTPStatusError as e:
            logger.error(f"❌ HTTP error calling GigaChat embeddings API: {e.response.status_code}")
            logger.error(f"   Method: POST")
            logger.error(f"   URL: {settings.GIGACHAT_EMBEDDINGS_URL}")
            logger.error(f"   Response: {e.response.text[:500]}")
            raise
        except Exception as e:
            logger.exception(f"❌ Error calling GigaChat embeddings API: {type(e).__name__}: {e}")
            raise


# Обратная совместимость (deprecated, но оставляем для плавного перехода)
async def gigachat_request(token: str, api_url: str, messages: list[dict]) -> dict:
    """
    DEPRECATED: Используйте gigachat_chat() вместо этой функции.
    Оставлено для обратной совместимости.
    """
    logger.warning("⚠️  gigachat_request() is deprecated. Use gigachat_chat() instead.")
    from app.config import Settings
    # Создаём временный settings объект для обратной совместимости
    # В реальности это должно быть передано из вызывающего кода
    settings = Settings()
    return await gigachat_chat(messages, settings)
