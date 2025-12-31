# Kalshi Dead Certain Grinder

Buy contracts at 95-99¢ on events that have already concluded. Collect $1 at settlement.

## The Strategy

When a sports game ends at 9 PM, Kalshi settles the market around 11 PM. During that window:
- Everyone knows the outcome
- But contracts still trade at 96-99¢
- Some people sell early because they want cash now
- You buy at 96-99¢, collect $1 when it settles

**Risk level: Very low** (event already happened, outcome is known)

## Quick Start

```bash
# Already have venv from v1? Just copy your .env
cp /path/to/old/.env .env

# Or create fresh
cp .env.example .env
nano .env  # Add your credentials

# Run
python bot.py
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MIN_CERTAINTY` | 95 | Only show contracts ≥95¢ |
| `MAX_BUY_PRICE` | 99 | Don't buy above 99¢ |
| `MAX_POSITION_CENTS` | 5000 | Max $50 per trade |
| `DRY_RUN` | true | Set to false to execute trades |

## Output

```
============================================================
  KALSHI DEAD CERTAIN GRINDER
============================================================

Mode: PRODUCTION
Min certainty: 95¢
Max position: $50.00

[19:30:45] Scanning for dead certains (≥95¢)...
[19:30:52] Scanned 847 markets, found 3 opportunities

🎯 EVENTS ENDED - COLLECT YOUR MONEY:

============================================================
💰 DEAD CERTAIN: NBA-LAKERS-WIN-20251230
   Will the Lakers win vs Celtics on Dec 30?
============================================================
   Side: YES @ 98¢
   Profit: 2¢/contract (2%)
   Available: 150 contracts
   Max profit: $3.00
   Event ended: 2 hours ago
============================================================

[DRY RUN] Would execute:
   Buy 51 YES @ 98¢
   Cost: $49.98
   Expected profit: $1.02 (2%)
```

## Compounding Math

Starting with $50:

| Return/Trade | Trades/Day | 1 Month | 6 Months |
|--------------|------------|---------|----------|
| 1% (99¢) | 2 | $91 | $271 |
| 2% (98¢) | 2 | $165 | $1,340 |
| 3% (97¢) | 2 | $298 | $8,870 |

Reality will be lower due to liquidity limits and inconsistent opportunities.

## Go Live

When you're ready to execute real trades:

```bash
# Edit .env
DRY_RUN=false
```

Start small. Watch it for a few trades. Scale up once confident.
