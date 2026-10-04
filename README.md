# Quant Bot

Direct-execution spot momentum bot for the configured Roostoo-compatible exchange. It has no paper-trading mode.

All settings are centralized in [bot/config.py](bot/config.py). Copy `.env.example` to `.env`, add exchange credentials, set `LIVE_TRADING_ENABLED=true`, then run:

```bash
python -m bot.main
```

The bot uses an EMA/RSI momentum entry, 2.25% stop loss, 3.5% take profit, dynamic sizing, a 4-position limit, cash reserve, and a 15% peak-to-trough drawdown circuit breaker. These values are intentionally more aggressive than the reference strategy, though the hard caps remain in force. See [plan.md](plan.md) for the live-launch checklist.
