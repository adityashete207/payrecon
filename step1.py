from pydantic import BaseModel

class Order(BaseModel):
    order_id: str
    amount: float

good_order = Order(order_id='ord_1', amount=500.0)
print('Good order worked:', good_order)