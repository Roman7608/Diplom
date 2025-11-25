from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BOT_TOKEN: str

    # GigaChat access
    GIGACHAT_CLIENT_ID: str
    GIGACHAT_AUTH_KEY: str
    GIGACHAT_SCOPE: str = "GIGACHAT_API_PERS"
    GIGACHAT_AUTH_URL: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    GIGACHAT_API_URL: str = "https://gigachat.devices.sberbank.ru:9443/api/v1/chat/completions"
    GIGACHAT_EMBEDDINGS_URL: str = "https://gigachat.devices.sberbank.ru:9443/api/v1/embeddings"
    GIGACHAT_VERIFY_SSL: bool = False  # Управление проверкой SSL-сертификатов

    # Auto catalog
    AUTO_CATALOG_PATH: str = "auto_catalog_ru_FINAL_ALL_2025.xlsx"

    class Config:
        env_file = ".env"
