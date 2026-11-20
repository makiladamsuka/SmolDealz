from pydantic import BaseModel, Field
from typing import Optional

class UserIntent(BaseModel):
    item: str = Field(description="Normalized search term for the product, e.g. 'mg90s servo'")
    quantity: int = Field(default=1, ge=1, description="Quantity of items required")
    max_budget: Optional[float] = Field(default=None, ge=0, description="Max budget constraint, if specified")
    currency: str = Field(default="LKR", description="Currency code (e.g., 'LKR', 'USD')")
    region: str = Field(default="Sri Lanka", description="Target shopping region")
    category: str = Field(default="electronics", description="Product category/domain (e.g. 'electronics', 'fashion', 'groceries', 'general')")
    specs: dict[str, str] = Field(default_factory=dict, description="Key specifications like brand, model, color")


class DiscoveredStore(BaseModel):
    name: str = Field(description="Store brand name, e.g. 'Tronic.lk'")
    url: str = Field(description="Base URL of the store homepage, e.g. 'https://tronic.lk'")
    reason: Optional[str] = Field(default=None, description="Why this store matches the product & region")
    search_url_template: Optional[str] = Field(default=None, description="Direct search URL template if known")


class ProductOffer(BaseModel):
    store: str = Field(description="Store identifier or domain, e.g. 'tronic.lk', 'maker.lk'")
    title: str = Field(description="Exact product title listed on the store website")
    price: float = Field(ge=0, description="Unit price in local currency as a float")
    currency: str = Field(default="LKR", description="Currency code (e.g., 'LKR')")
    in_stock: bool = Field(default=True, description="Whether the product is currently purchasable")
    stock_quantity: Optional[int] = Field(default=None, ge=0, description="Exact stock count if available, else None")
    url: str = Field(description="Direct URL to the product page")
    snippet: Optional[str] = Field(default=None, description="Brief description or specs extracted by the scraper")
    matched_item: Optional[str] = Field(default=None, description="Original search query this offer was found for")


class BestDeal(BaseModel):
    matched_item: str = Field(description="The product name evaluated")
    store: str = Field(description="Winning store name")
    unit_price: float = Field(description="Winning unit price")
    total_price: float = Field(description="Total price for the required quantity")
    stock: Optional[int | str] = Field(default="In Stock", description="Stock available at the winning store")
    url: str = Field(description="Link to purchase")
    reasoning: Optional[str] = Field(default=None, description="Why this deal was selected")
