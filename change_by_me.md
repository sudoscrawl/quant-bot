# Using Exponential Momentum strategy (EMA) with changes...

In /bot/strategy/momentum.py:
        momentum.py — EMA crossover + RSI strategy

        Entry:
        - fast EMA crosses above slow EMA (fresh, within confirm_ticks window)
        - RSI in the 45–58 zone (To be safe can be changed to more broader manner)
        - EMA separation >= 0.05%
        - no active loss cooldown

        Exit:
        - stop-loss at -2%  (Should be changed but dont know, since ?? dont feel like it.)
        - take-profit at +3%
        - EMA cross-down if profitable and held long enough

In /bot/risk/manager.py:
        Just some risk management, so that it should have a min of 5% in cash (USD) and should use 15% of portfolio per trade.

/bot/logger/trade_logger.py should be updated to a more elegant logging.

