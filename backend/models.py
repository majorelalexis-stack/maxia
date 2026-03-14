"""MAXIA Pydantic Models V10"""
from pydantic import BaseModel, Field
from typing import Optional, List
import time, uuid


class OrderRequest(BaseModel):
    mint: str
    side: str
    qty: float = Field(gt=0)
    price_usdc: float = Field(gt=0)
    order_type: str = "LIMIT"
    escrow_tx: str
    currency: str = "USDC"

class CancelOrderRequest(BaseModel):
    order_id: str

class ListTokenRequest(BaseModel):
    mint: str
    symbol: str
    name: str
    decimals: int = 9
    initial_price: float = Field(gt=0)
    escrow_tx: str

class AuctionCreateRequest(BaseModel):
    gpu_tier_id: str
    duration_hours: float = Field(gt=0, le=720)
    floor_price_usdc: Optional[float] = None

class AuctionSettleRequest(BaseModel):
    auction_id: str
    winner: str
    tx_signature: str

class CommandRequest(BaseModel):
    service_id: str
    prompt: str = Field(min_length=1, max_length=10000)
    tx_signature: str
    amount_usdc: float = Field(gt=0)

class ListingCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    type: str
    price_usdc: float = Field(gt=0)

class DatasetListRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    category: str
    size_mb: float = Field(gt=0)
    price_usdc: float = Field(gt=0, le=100000)
    sample_hash: str
    format: str

class SubscribeRequest(BaseModel):
    plan: str
    tx_signature: str
    duration_months: int = 1

class RegisterReferralRequest(BaseModel):
    referrer_code: str

class BaseVerifyRequest(BaseModel):
    tx_hash: str
    expected_to: Optional[str] = None
    expected_amount_raw: Optional[int] = None

class AP2PaymentRequest(BaseModel):
    intent_mandate: dict
    cart_mandate: Optional[dict] = None
    payment_payload: Optional[str] = None
    network: str = "solana-mainnet"
