Binanza
=======

My first Python3 script for automating cryptocurrency trading using the Binance API.

Analyzes candlestick patterns and tries to buy low and sell high.

Disclaimer
----------

Do not use this if you want to make money. I have no idea what I am doing.

<img src=https://i.imgur.com/l3v4P3s.jpg alt="Dog" title="Dog" width="200" />

Usage
-----

See main() in start_trading.py for example usage.

Requires a Binance account and API access, go to account settings to enable then replace example API key and secret.

Run in console or as a service.

If you are getting an API error code=-1013 (Filter failure: MIN_NOTIONAL) it means that your orders are too small (less than 0.001 BTC).

Dependencies
------------

* [TA-Lib](https://github.com/mrjbq7/ta-lib)
* [python-binance](https://github.com/sammchardy/python-binance)
