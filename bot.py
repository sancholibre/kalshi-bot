"""
Kalshi Settlement Sniper v4
- Buys 95-97c contracts on events that have ENDED
- Sends Discord alerts
- Railway-ready
"""

import os
import sys
import time
import base64
import math
import datetime
import requests
from typing import Optional, List
from dataclasses import dataclass
from datetime import timezone, timedelta

try:
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding, ed25519
    from cryptography.hazmat.backends import default_backend
except ImportError:
    print("[ERROR] pip install cryptography")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# =============================================================================
# CONFIG
# =============================================================================

CONFIG = {
    "api_key_id": os.getenv("KALSHI_API_KEY_ID"),
    "private_key_path": os.getenv("KALSHI_PRIVATE_KEY_PATH"),
    "private_key_base64": os.getenv("KALSHI_PRIVATE_KEY_BASE64"),
    "min_price": int(os.getenv("MIN_PRICE", "95")),
    "max_price": int(os.getenv("MAX_PRICE", "97")),
    "max_position_cents": int(os.getenv("MAX_POSITION_CENTS", "5000")),  # $50
    "scan_interval": int(os.getenv("SCAN_INTERVAL_SECONDS", "120")),
    "lookahead_days": int(os.getenv("LOOKAHEAD_DAYS", "1")),
    "dry_run": os.getenv("DRY_RUN", "true").lower() == "true",
    "discord_webhook": os.getenv("DISCORD_WEBHOOK"),
}

# =============================================================================
# DISCORD
# =============================================================================

def discord(msg: str, emoji: str = "🤖"):
    webhook = CONFIG["discord_webhook"]
    if not webhook:
        return
    try:
        requests.post(webhook, json={"content": f"{emoji} **Kalshi Bot**\n```\n{msg[:1800]}\n```"}, timeout=5)
    except Exception as e:
        print(f"[DISCORD ERROR] {e}")

def discord_trade(action: str, ticker: str, side: str, price: int, qty: int, profit: float):
    webhook = CONFIG["discord_webhook"]
    if not webhook:
        return
    try:
        msg = {
            "embeds": [{
                "title": f"{'🎯 TRADE EXECUTED' if action == 'buy' else '📊 Opportunity Found'}",
                "color": 0x00ff00 if action == "buy" else 0xffaa00,
                "fields": [
                    {"name": "Ticker", "value": ticker, "inline": True},
                    {"name": "Side", "value": side, "inline": True},
                    {"name": "Price", "value": f"{price}¢", "inline": True},
                    {"name": "Quantity", "value": str(qty), "inline": True},
                    {"name": "Potential Profit", "value": f"${profit:.2f}", "inline": True},
                ],
                "timestamp": datetime.datetime.utcnow().isoformat()
            }]
        }
        requests.post(webhook, json=msg, timeout=5)
    except Exception as e:
        print(f"[DISCORD ERROR] {e}")

# =============================================================================
# API CLIENT
# =============================================================================

class KalshiAPI:
    BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"
    
    def __init__(self):
        self.private_key = self._load_key()
        self.session = requests.Session()
    
    def _load_key(self):
        # Try base64 first (for Railway), then file path
        if CONFIG["private_key_base64"]:
            key_bytes = base64.b64decode(CONFIG["private_key_base64"])
            return serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())
        elif CONFIG["private_key_path"]:
            with open(CONFIG["private_key_path"], "rb") as f:
                return serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
        else:
            raise ValueError("No private key configured")
    
    def _sign(self, method: str, path: str, timestamp: str) -> str:
        message = f"{timestamp}{method}{path}".encode()
        if isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            sig = self.private_key.sign(message)
        else:
            sig = self.private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode()
    
    def _request(self, method: str, path: str, params: dict = None, json_body: dict = None) -> dict:
        # Build full path with query string for signing
        if params:
            query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            full_path = f"{path}?{query}"
        else:
            full_path = path
        
        timestamp = str(int(time.time() * 1000))
        headers = {
            "KALSHI-ACCESS-KEY": CONFIG["api_key_id"],
            "KALSHI-ACCESS-SIGNATURE": self._sign(method, full_path, timestamp),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
        }
        
        # Use full URL with query string baked in (not params=)
        url = f"{self.BASE_URL}{full_path}"
        
        for attempt in range(3):
            try:
                if method == "GET":
                    resp = self.session.get(url, headers=headers, timeout=15)
                else:
                    resp = self.session.post(url, headers=headers, json=json_body, timeout=15)
                
                if resp.status_code == 429:
                    print("[RATE LIMITED] Waiting 10s...")
                    time.sleep(10)
                    continue
                
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise
        
        return {}
    
    def get_markets(self, max_close_ts: int, limit=200, cursor=None):
        params = {"limit": limit, "status": "open", "max_close_ts": max_close_ts}
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/markets", params)
    
    def get_orderbook(self, ticker: str):
        return self._request("GET", f"/markets/{ticker}/orderbook")
    
    def create_order(self, ticker: str, side: str, count: int, price: int):
        """Create a limit order. Side is 'yes' or 'no'."""
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side.lower(),
            "count": count,
            "type": "limit",
        }
        if side.lower() == "yes":
            body["yes_price"] = price
        else:
            body["no_price"] = price
        
        return self._request("POST", "/portfolio/orders", json_body=body)

# =============================================================================
# SCANNER
# =============================================================================

@dataclass
class Opportunity:
    ticker: str
    title: str
    side: str
    price: int
    quantity: int
    profit_pct: int
    close_time: datetime.datetime
    hours_since_close: float

def parse_time(s: str) -> Optional[datetime.datetime]:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except:
        return None

def scan_for_opportunities(api: KalshiAPI) -> List[Opportunity]:
    """Find contracts at 95-97c where the event has ENDED."""
    
    now = datetime.datetime.now(timezone.utc)
    max_close_ts = int((now + timedelta(days=CONFIG["lookahead_days"])).timestamp())
    
    opportunities = []
    cursor = None
    markets_scanned = 0
    
    while True:
        result = api.get_markets(max_close_ts, limit=200, cursor=cursor)
        markets = result.get("markets", [])
        
        if not markets:
            break
        
        for market in markets:
            ticker = market.get("ticker", "")
            title = market.get("title", "")[:50]
            close_time = parse_time(market.get("close_time"))
            
            # ONLY consider markets where event has ENDED
            if not close_time or close_time > now:
                continue
            
            hours_since = (now - close_time).total_seconds() / 3600
            
            # Skip if ended more than 6 hours ago (probably already settled)
            if hours_since > 6:
                continue
            
            try:
                book = api.get_orderbook(ticker).get("orderbook", {})
                
                for side, asks in [("YES", book.get("yes") or []), ("NO", book.get("no") or [])]:
                    for price, qty in asks:
                        if CONFIG["min_price"] <= price <= CONFIG["max_price"]:
                            opportunities.append(Opportunity(
                                ticker=ticker,
                                title=title,
                                side=side,
                                price=price,
                                quantity=qty,
                                profit_pct=100 - price,
                                close_time=close_time,
                                hours_since_close=hours_since,
                            ))
            except:
                continue
            
            time.sleep(0.03)
        
        markets_scanned += len(markets)
        cursor = result.get("cursor")
        
        if not cursor:
            break
    
    return opportunities

# =============================================================================
# EXECUTION
# =============================================================================

def calculate_fee(contracts: int, price: int) -> int:
    """Kalshi fee: ceil(0.07 * contracts * price * (1 - price/100))"""
    fee = 0.07 * contracts * (price / 100) * (1 - price / 100)
    return math.ceil(fee * 100)  # in cents

def execute_trade(api: KalshiAPI, opp: Opportunity) -> bool:
    """Execute a trade on an opportunity."""
    
    # Calculate position size
    max_contracts = CONFIG["max_position_cents"] // opp.price
    qty = min(opp.quantity, max_contracts, 100)  # Cap at 100 contracts per trade
    
    if qty <= 0:
        return False
    
    cost_cents = qty * opp.price
    payout_cents = qty * 100
    fee_cents = calculate_fee(qty, opp.price)
    profit_cents = payout_cents - cost_cents - fee_cents
    
    print(f"\n{'='*50}")
    print(f"TRADE: {opp.side} @ {opp.price}¢ on {opp.ticker}")
    print(f"  Qty: {qty} contracts")
    print(f"  Cost: ${cost_cents/100:.2f}")
    print(f"  Fee: ${fee_cents/100:.2f}")
    print(f"  Profit if win: ${profit_cents/100:.2f} ({opp.profit_pct}%)")
    print(f"  Event ended: {opp.hours_since_close:.1f}h ago")
    
    if CONFIG["dry_run"]:
        print(f"  [DRY RUN] Would execute trade")
        discord_trade("alert", opp.ticker, opp.side, opp.price, qty, profit_cents/100)
        return True
    
    try:
        order = api.create_order(opp.ticker, opp.side, qty, opp.price)
        order_id = order.get("order", {}).get("order_id", "unknown")
        print(f"  ✅ ORDER PLACED: {order_id}")
        discord_trade("buy", opp.ticker, opp.side, opp.price, qty, profit_cents/100)
        return True
    except Exception as e:
        print(f"  ❌ ORDER FAILED: {e}")
        discord("error", f"Order failed: {opp.ticker} {opp.side} @ {opp.price}¢\n{e}")
        return False

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("\n" + "="*60)
    print("  KALSHI SETTLEMENT SNIPER v4")
    print("="*60)
    
    if not CONFIG["api_key_id"]:
        print("[ERROR] KALSHI_API_KEY_ID not set")
        sys.exit(1)
    
    if not CONFIG["private_key_path"] and not CONFIG["private_key_base64"]:
        print("[ERROR] No private key configured")
        sys.exit(1)
    
    print(f"\nSettings:")
    print(f"  Price range: {CONFIG['min_price']}-{CONFIG['max_price']}¢")
    print(f"  Max position: ${CONFIG['max_position_cents']/100:.0f}")
    print(f"  Scan interval: {CONFIG['scan_interval']}s")
    print(f"  Lookahead: {CONFIG['lookahead_days']} day(s)")
    print(f"  Dry run: {CONFIG['dry_run']}")
    print(f"  Discord: {'configured' if CONFIG['discord_webhook'] else 'not configured'}")
    
    api = KalshiAPI()
    
    # Test connection
    try:
        status = api._request("GET", "/exchange/status")
        exchange_open = status.get("trading_active", False)
        print(f"\nExchange: {'OPEN' if exchange_open else 'CLOSED'}")
    except Exception as e:
        print(f"\n[ERROR] Connection failed: {e}")
        sys.exit(1)
    
    discord(f"Bot started!\nPrice range: {CONFIG['min_price']}-{CONFIG['max_price']}¢\nMax position: ${CONFIG['max_position_cents']/100:.0f}\nDry run: {CONFIG['dry_run']}", "🚀")
    
    print("\n" + "-"*60)
    print("Scanning for opportunities... (Ctrl+C to stop)")
    print("-"*60)
    
    seen_trades = set()
    
    while True:
        try:
            now = datetime.datetime.now(timezone.utc)
            print(f"\n[{now.strftime('%H:%M:%S')}] Scanning...")
            
            opportunities = scan_for_opportunities(api)
            
            # Filter to unseen opportunities
            new_opps = []
            for opp in opportunities:
                key = f"{opp.ticker}_{opp.side}_{opp.price}"
                if key not in seen_trades:
                    new_opps.append(opp)
                    seen_trades.add(key)
            
            if new_opps:
                print(f"Found {len(new_opps)} new opportunities!")
                
                # Sort by profit potential
                new_opps.sort(key=lambda x: -x.profit_pct)
                
                for opp in new_opps[:5]:  # Max 5 trades per scan
                    execute_trade(api, opp)
                    time.sleep(1)
            else:
                print(f"  No dead certain opportunities found")
            
            # Clear old seen trades periodically
            if len(seen_trades) > 500:
                seen_trades.clear()
            
            time.sleep(CONFIG["scan_interval"])
            
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            discord("Bot stopped", "🛑")
            break
        except Exception as e:
            print(f"\n[ERROR] {e}")
            time.sleep(CONFIG["scan_interval"])

if __name__ == "__main__":
    main()
