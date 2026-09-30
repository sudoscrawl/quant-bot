from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Config(BaseSettings):
    api_key: str = Field(..., alias="API_KEY")
    api_secret: str = Field(..., alias="API_SECRET")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,

    )


config = Config()
