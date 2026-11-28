"""
client/config.py — Configuration for the Python Client Target Application.
"""
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_id: str = os.getenv("CLIENT_APP_ID", "python-client-target")
    app_name: str = os.getenv("CLIENT_APP_NAME", "Python Store Target")
    app_port: int = int(os.getenv("CLIENT_APP_PORT", "8090"))
    environment: str = os.getenv("CLIENT_ENV", "testing")

    # Gateway
    gateway_url: str = os.getenv("TWINOPS_GATEWAY", "http://localhost:8000")
    master_key: str = os.getenv("TWINOPS_MASTER_KEY", "twinops-gateway-master-key-change-me")

    # MongoDB Atlas Database
    mongodb_uri: str = os.getenv(
        "MONGODBURI",
        "mongodb://jashwanthkv2005_db_user:cEN9YcArPAoie2uB@ac-6kfuivh-shard-00-00.0sl7cil.mongodb.net:27017,ac-6kfuivh-shard-00-01.0sl7cil.mongodb.net:27017,ac-6kfuivh-shard-00-02.0sl7cil.mongodb.net:27017/?ssl=true&replicaSet=atlas-serf73-shard-0&authSource=admin"
    )
    mongodb_db_name: str = os.getenv("MONGODB_DATABASE", "twinops_client_db")

    # Redis Cache
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

settings = Settings()
