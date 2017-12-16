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

Configure the script through config.txt (make a copy of config_example.txt), see comments for details about parameters. See main() in start_trading.py for example usage.

Requires a Binance account and API access, go to account settings to enable then replace example API key and secret in start_trading.py.

Run start_trading.py in console or configure as a service.

Has a node package that can be used to extract statistics from the database that is created, containing order history and recognized patterns.

Dependencies
------------

* numpy
* sqlite3
* [TA-Lib](https://github.com/mrjbq7/ta-lib)
* [python-binance](https://github.com/sammchardy/python-binance)
