from motor.motor_asyncio import AsyncIOMotorClient
import orchestrator.services.config as config

# Initialize a singleton MongoDB client
_mongo_client: AsyncIOMotorClient = None

def get_mongo_client() -> AsyncIOMotorClient:
    global _mongo_client
    if _mongo_client is None:
        uri = config.MONGODB_URI
        _mongo_client = AsyncIOMotorClient(uri)
    return _mongo_client
