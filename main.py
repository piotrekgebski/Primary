from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

# Model danych (request body)
class Item(BaseModel):
    name: str
    price: float
    is_available: bool = True

# Root endpoint
@app.get("/")
def read_root():
    return {"message": "Hello FastAPI 🚀"}

# GET endpoint
@app.get("/items/{item_id}")
def read_item(item_id: int):
    return {"item_id": item_id}

# POST endpoint
@app.post("/items/")
def create_item(item: Item):
    return {
        "message": "Item created",
        "item": item
    }

# Healthcheck (przydatne w produkcji)
@app.get("/health")
def health_check():
    return {"status": "ok"}
