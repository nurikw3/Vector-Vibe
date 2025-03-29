from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int
    DB_USER: str
    DB_PASS: str
    DB_NAME: str
    access_token: SecretStr
    BOT_TOKEN: SecretStr
    ADMIN_ID: int

    @property
    def DATABASE_URL_asyncpg(self):
        # DSN
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
    @property
    def DATABASE_URL_psycopg(self):
        return f"postgresql+psycopg://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
    
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding="utf-8")

config = Settings()