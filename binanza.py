#!/usr/bin/python3
# -*- coding: utf-8 -*-
import datetime
from decimal import *
from email.mime.text import MIMEText
import json
import logging
import os
import numpy as np
import smtplib
import sqlite3
import sys
import time
import traceback
from binance.client import Client
from binance.enums import *
from binance.exceptions import BinanceAPIException
from talib.abstract import *


class Log(object):
    """Utility class for logging. Will create a log file containing only the
    last trading run, along with a compiled log file of all runs.
    Also handles console output.

    Keyword arguments:
    log_name (str) -- the name to use for log files, will create <log_name>.log
        for cumulative log and <log_name>_last.log for last run log
    level -- the logging level enum to use, set this to logging.DEBUG to log
        last five pattern analysis inputs and method results
    """
    def __init__(self, log_name="binanza", level=logging.DEBUG):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s\t%(levelname)s\t- %(message)s', datefmt='%Y.%m.%d %H:%M:%S')
        self.log = logging.getLogger(log_name)
        # Configure
        self.log.setLevel(level)
        formatter = logging.Formatter('%(asctime)s\t%(levelname)s\t- %(message)s', '%Y.%m.%d %H:%M:%S')
        # Last run
        self.last_run_log = os.path.join(sys.path[0], '{}_last.log'.format(log_name))
        last_run = logging.FileHandler(self.last_run_log, mode='w')
        last_run.setLevel(level)
        last_run.setFormatter(formatter)
        self.log.addHandler(last_run)
        # Compilated log
        full_log_path = os.path.join(sys.path[0], '{}.log'.format(log_name))
        full_log = logging.FileHandler(full_log_path, mode='a')
        full_log.setLevel(level)
        full_log.setFormatter(formatter)
        self.log.addHandler(full_log)
        return

    def has_order(self):
        """Checks if the last run log included orders."""
        log_file = self.last_run_log
        f = open(log_file)
        log = f.read()
        f.close()
        if ("BUY ORDER:" in log or "SELL ORDER:" in log):
            return True
        return False

    def has_errors(self):
        """Checks if the log file for the last run encountered any errors."""
        log_file = self.last_run_log
        f = open(log_file)
        log = f.read()
        f.close()
        if ("ERROR" in log and "KeyboardInterrupt" not in log):
            return True
        return False

    def send_gmail(self, username, password, recipients, subject="Binanza", content=None):
        """Sends an email by logging in to gmail. If no content is specified
        the email body will be the contents of the last run log file.

        Keyword arguments:
        username (str) -- the gmail username
        password (str) -- the gmail password
        recipients (list) -- the list of recipient email addresses
        {subject} (str) -- a subject for the mail
        {content} (str) -- optional contents of the mail,
            defaults to last run log file
        """
        if (content is None):
            f = open(self.last_run_log)
            content = f.read()
            f.close()
        sender = '{}@gmail.com'.format(username)
        msg = MIMEText(content)
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = ", ".join(recipients)
        server = smtplib.SMTP('smtp.gmail.com:587')
        server.starttls()
        server.login(username, password)
        server.sendmail(sender, ", ".join(recipients), msg.as_string())
        server.quit()
        return


class Binanza(object):
    def __init__(self, api_key, api_secret, **kwargs):
        """Trading wrapper for the python-binance wrapper for the Binance API.

        API key and secret is required, all other parameters are assigned
        default values.

        Keyword arguments:
        api_key (str) -- your Binance API key
        api_secret (str) -- your Binance API secret
        {config_file} (str) -- a path to a config file to re-read settings from
            on continuous runs
        {min_balance} (dict) -- a dict of symbols and minimum balances as
            key-value pairs used to determine if an trade should go through.
            If no minimum balance is defined for a symbol the trader will
            always try to trade the defined fraction on pattern recognition
        {max_balance} (dict) -- same as min_balance but for a maximum balance
            the trader will stop at when buying
        {kline_interval} (str) -- a candlestick time interval to fetch for
            analysis, the last 500 bins will be fetched. Defaults to 5 minutes
        {continuous} (bool) -- a flag specifying if the trader should
            continuously keep trading with a specified break between runs.
            Defaults to a single analysis (and trade if pattern found)
        {order_lifetime} (int) -- a duration in seconds that stale orders will
            live before the trader cancels them
        {sleep_duration} (int) -- a number of seconds to sleep between runs if
            running in continuous mode
        {errors_to_mail} (list) -- a list of recipients for error info if gmail
            account specified
        {orders_to_mail} (list) -- a list of recipients for order info if gmail
            account specified
        {gmail} (dict) -- a dict with gmail authorization info and either a
            list of addresses to send order reports to or a list of addresses
            for error logs. Format: {
                "username": <gmail user>,
                "password": <gmail pass>
            }
        """
        self.api_key = api_key
        self.api_secret = api_secret
        for key, item in kwargs.items():
            setattr(self, key, item)

        # Set default values and convert supplied floats to Decimals
        self.set_default("config_file", None)
        self.set_default("min_balance", {})
        for symbol in self.min_balance:
            self.min_balance[symbol] = Decimal(self.min_balance[symbol])
        self.set_default("max_balance", {})
        for symbol in self.max_balance:
            self.max_balance[symbol] = Decimal(self.max_balance[symbol])
        self.set_default("kline_interval", KLINE_INTERVAL_5MINUTE)
        self.set_default("continuous", False)
        self.set_default("sleep_duration", 300)
        self.set_default("order_lifetime", 600)
        self.set_default("errors_to_mail", [])
        self.set_default("orders_to_mail", [])
        self.set_default("gmail", None)

        # Candlestick validation functions
        def reversal_if_trend(indication, candles, factor=1, skip=0):
            # A positive indicator relies on downwards trend, and vice versa
            latest_close = [float(c[4]) for c in candles[-1 * factor - skip:]]
            avg_latest_close = sum(latest_close) / float(len(latest_close))
            previous_close = [float(c[4]) for c in candles[-5 * factor - skip : -1 * factor - skip]]
            avg_previous_close = sum(previous_close) / len(previous_close)
            if (indication > 0.0 and avg_latest_close < avg_previous_close):
                return True
            elif (indication < 0.0 and avg_latest_close > avg_previous_close):
                return True
            return False

        def reversal_if_long_trend(indication, candles):
            return (reversal_if_trend(indication, candles, factor=2))

        def reversal_if_previous_trend_skip1(indication, candles):
            return (reversal_if_trend(indication, candles, skip=1))

        def reversal_if_previous_trend_skip3(indication, candles):
            return (reversal_if_trend(indication, candles, skip=3))

        # Candlestick pattern recognition functions
        self.patterns = {
            "Abandoned baby": {
                "f": CDLABANDONEDBABY,
                "validators": [reversal_if_trend]
            },
            "Dark cloud cover": {
                "f": CDLDARKCLOUDCOVER,
                "validators": [reversal_if_previous_trend_skip1]
            },
            "Dragonfly doji": {
                "f": CDLDRAGONFLYDOJI,
                "validators": [reversal_if_long_trend]
            },
            "Engulfing pattern": {
                "f": CDLENGULFING,
                "validators": [reversal_if_previous_trend_skip1]
            },
            "Evening doji star": {
                "f": CDLEVENINGDOJISTAR,
                "validators": [reversal_if_long_trend]
            },
            "Evening star": {
                "f": CDLEVENINGSTAR,
                "validators": [reversal_if_trend]
            },
            "Hammer": {
                "f": CDLHAMMER,
                "validators": [reversal_if_long_trend]
            },
            "Hanging man": {
                "f": CDLHANGINGMAN,
                "validators": [reversal_if_long_trend]
            },
            "Morning doji star": {
                "f": CDLMORNINGDOJISTAR,
                "validators": [reversal_if_long_trend]
            },
            "Morning star": {
                "f": CDLMORNINGSTAR,
                "validators": [reversal_if_trend]
            },
            "Shooting star": {
                "f": CDLSHOOTINGSTAR,
                "validators": [reversal_if_trend]
            },
            "Three advancing white soldiers": {
                "f": CDL3WHITESOLDIERS,
                "validators": [reversal_if_previous_trend_skip3]
            },
            "Three black crows": {
                "f": CDL3BLACKCROWS,
                "validators": [reversal_if_previous_trend_skip3]
            },
            "Three inside up/down": {
                "f": CDL3INSIDE,
                "validators": [reversal_if_previous_trend_skip3]
            },
            "Three line strike": {
                "f": CDL3LINESTRIKE,
                "validators": [reversal_if_previous_trend_skip3]
            },
            "Three outside up/down": {
                "f": CDL3OUTSIDE,
                "validators": [reversal_if_previous_trend_skip3]
            },
            "Two crows": {
                "f": CDL2CROWS,
                "validators": [reversal_if_previous_trend_skip1]
            },
            "Upside gap with two crows": {
                "f": CDLUPSIDEGAP2CROWS,
                "validators": [reversal_if_previous_trend_skip1]
            }
        }
        return

    def set_default(self, param, value):
        if not hasattr(self, param):
            setattr(self, param, value)
        return

    def read_config(self):
            """Reads a configuration file and applies settings, so that the
            user does not have to restart the binanza service on changes."""
            if (not hasattr(self, "config_file") or self.config_file is None):
                return
            f = open(os.path.join(sys.path[0], "config.txt"))
            contents = ""
            for line in f:
                # Remove comment lines
                if not (line.strip().startswith("#")):
                    contents += line
            f.close()
            config = json.loads(contents)
            for param in config:
                setattr(self, param, config[param])
            for symbol in config["min_balance"]:
                self.min_balance[symbol] = Decimal(config["min_balance"][symbol])
            for symbol in config["max_balance"]:
                self.max_balance[symbol] = Decimal(config["max_balance"][symbol])
            return

    def get_balances(self, symbols):
        """ Queries the Binance API for all account balances, and compiles
        a dict of symbols and free balance as key-value pairs.
        
        Example input: ["ETH", "IOTA"]

        Keyword arguments:
        symbols (list) -- the list of Binance symbols for which to get balances
        """
        self.balances = {}
        account = self.client.get_account()
        for symbol in symbols:
            self.balances[symbol] = Decimal(0.0)
        for balance in account["balances"]:
            for symbol in symbols:
                if (balance["asset"] == symbol):
                    self.balances[symbol] = Decimal(balance["free"])
        return

    def analyze_candles(self, candles):
        """Uses TA-Lib to analyze candlesticks, and returns
        a result dict containing the following:

        'inputs': the numpy inputs used for pattern recognition
        'analyses': a dict containing key-pair values of pattern names and
            indication value
        'indication': a float of the mean indication value across patterns
            that have been validated to be valid, used to decide buy/sell
            actions
        'patterns': a dict with key-value pairs of validated pattern names and
            their indication value

        Keyword arguments:
        candles (list) -- the list of candlesticks
            as returned by the Binance API
        """
        # Candlestick list format:
        # [open time, open, high, low, close, volume, close time, quote asset volume, number of trades, taker buy base asset volume, taker buy quote asset volume]

        # Skip last candle if volume less than 20% of previous candle
        # (indicates partial candlestick at start of Binance bin cutoff)
        if (len(candles) > 1):
            current_volume = Decimal(candles[-1][5])
            previous_volume = Decimal(candles[-2][5])
            if (current_volume < previous_volume * Decimal(0.2)):
                del candles[-1]

        # Extract candlestick values to convert to numpy arrays
        open_ = [Decimal(c[1]) for c in candles]
        high = [Decimal(c[2]) for c in candles]
        low = [Decimal(c[3]) for c in candles]
        close = [Decimal(c[4]) for c in candles]
        volume = [Decimal(c[5]) for c in candles]

        # Convert to numpy arrays
        inputs = {
            'open': np.asarray(open_, dtype=np.float64),
            'high': np.asarray(high, dtype=np.float64),
            'low': np.asarray(low, dtype=np.float64),
            'close': np.asarray(close, dtype=np.float64),
            'volume': np.asarray(volume, dtype=np.float64)
        }

        # Run all TA-Lib candlestick pattern analyses and calculate average indication value
        analyses = {}
        sum_indication = 0.0
        recognized_patterns = []
        for pattern in self.patterns:
            p = self.patterns[pattern]
            f = p["f"]
            validators = p["validators"] if ("validators" in p) else None
            analyses[pattern] = f(inputs)
            indication = analyses[pattern][-1]
            if (indication != 0.0 and (validators is None or all(v(indication, candles) for v in validators))):
                sum_indication += indication
                recognized_patterns.append({
                    "name": pattern,
                    "indication": indication
                })
        avg_indication = sum_indication / len(self.patterns)

        # Return result object
        results = {
            "inputs": inputs,
            "analyses": analyses,
            "indication": avg_indication,
            "patterns": recognized_patterns
        }
        return results

    def balance_is_ok(self, symbol, quantity):
        """Checks if a minimum balance is defined for a symbol and if the
        balance is over the limit.

        symbol (str) -- the currency symbol as defined by Binance
        quantity (Decimal) -- the order quantity
        """
        if (
            quantity > self.balances[symbol] or
            (
                symbol in self.min_balance and 
                self.balances[symbol] < self.min_balance[symbol]
            )
        ):
            return False
        return True

    def seconds_to_days(self, seconds):
        """Converts seconds to days."""
        return seconds / 60.0 / 60.0 / 24.0

    def get_order_average(self, symbol, side, days=None):
        """Gets orders of the Binance account (500 max) extracts successfull
        sell/buy orders and calculates an average price per quantity.

        Return is a dict with the following keys:
        'avg': the calculated average price
        'count': the number of orders

        Keyword arguments:
        symbol (str) -- the Binance symbol to fetch orders for
        side (str) -- the type of order to check, "BUY" or "SELL"
        {days} (int) -- the number of days since today to
            check historical orders
        """
        orders = self.client.get_all_orders(symbol=symbol, recvWindow=10000)

        # Calculate average
        order_sum = Decimal(0.0)
        quantity = Decimal(0.0)
        n_orders = 0
        now = datetime.datetime.now()
        for order in orders:
            then = datetime.datetime.fromtimestamp(Decimal(order["time"]) / Decimal(1000.0))
            delta = now - then
            age_days = self.seconds_to_days(delta.total_seconds())
            if ((days is None or age_days < days) and order["side"] == side and order["status"] in ["PARTIALLY_FILLED", "FILLED"]):
                order_sum += Decimal(order["price"]) * Decimal(order["executedQty"])
                quantity += Decimal(order["executedQty"])
                n_orders += 1

        # Return average
        avg_price = order_sum / quantity if (quantity > 0.0) else 0.0
        return {
            "avg": avg_price,
            "count": n_orders
        }

    def buy_price_is_right(self, symbol_pair, price, min_orders=5):
        """Checks if the price of a buy order is favorable by
        comparing it to the average of recent (last 500) sell orders.
        
        Keyword arguments:
        symbol_pair (dict) -- the config symbol pair to check
        price (Decimal) -- the market price per quantity
        {min_orders} (int) -- the minimum amount of orders for
            validating price against average
        """
        if ("buy_order_check" in symbol_pair and not symbol_pair["buy_order_check"]):
            return True
        days = symbol_pair["check_days"] if ("check_days" in symbol_pair) else None
        symbol = "{}{}".format(symbol_pair["base"], symbol_pair["quote"])
        order_history = self.get_order_average(symbol, "SELL", days)
        if (order_history["count"] == 0):
            return True
        if (order_history["count"] >= min_orders and price > order_history["avg"] * Decimal(1.0005)):
            # Buy price higher than average sell order, abort order
            return False
        return True

    def sell_price_is_right(self, symbol_pair, price, min_orders=5):
        """Checks if the price of a sell order is favorable by
        comparing it to the average of recent (last 500) buy orders.
        
        Keyword arguments:
        symbol_pair (dict) -- the config symbol pair to check
        price (Decimal) -- the market price per quantity
        {min_orders} (int) -- the minimum amount of orders for
            validating price against average
        """
        if ("sell_order_check" in symbol_pair and not symbol_pair["sell_order_check"]):
            return True
        days = symbol_pair["check_days"] if ("check_days" in symbol_pair) else 7
        symbol = "{}{}".format(symbol_pair["base"], symbol_pair["quote"])
        order_history = self.get_order_average(symbol, "BUY", days)
        if (order_history["count"] == 0):
            return True
        if (order_history["count"] >= min_orders and price < order_history["avg"] * Decimal(1.0005)):
            # Sell price lower than average buy order, abort order
            return False
        return True

    def set_decimal_precision(self, symbol_pair, symbol):
        """Checks exchange info to determine the correct price/quantity
        precision for a symbol.

        Keyword arguments:
        symbol_pair (str) -- the binance trading symbol pair containing the
            symbol to use for precision
        symbol (str) -- the symbol to use for precision
        """
        for s in self.exchange_info["symbols"]:
            if (s["symbol"] == symbol_pair):
                getcontext().prec = s["baseAssetPrecision"] if (symbol == s["baseAsset"]) else s["quotePrecision"]
        return

    def check_order(self, symbol, quantity, price):
        """Compares defined order quantity and price against exchange info
        filters, and adjusts them to the minimum allowable values and
        tick sizes.

        Keyword arguments:
        symbol (str) -- the Binance trade symbol
        quanitity (Decimal) -- the quantity to check
        """
        a_quantity = None
        a_price = None
        for s in self.exchange_info["symbols"]:
            if (s["symbol"] != symbol):
                continue

            # Check if accepting trades
            if (s["status"] != "TRADING"):
                return None, None

            # Check filters
            for f in s["filters"]:

                # Check if price lower than price filter
                if (f["filterType"] == "PRICE_FILTER"):
                    # Set Decimal context to quote symbol precision
                    getcontext().prec = s["quotePrecision"]
                    # Iterate price from min until desired price
                    a_price = Decimal(f["minPrice"])
                    price_tick = Decimal(f["tickSize"])
                    while (a_price < price):
                        a_price += price_tick

                # Check if quantity lower than filters
                if (f["filterType"] == "LOT_SIZE"):
                    # Set Decimal context to base symbol precision
                    getcontext().prec = s["baseAssetPrecision"]
                    # Iterate quantity from min until desired quantity
                    min_qty = Decimal(f["minQty"])
                    qty_step = Decimal(f["stepSize"])
                    if (a_quantity is None):
                        a_quantity = Decimal(min_qty)
                    while (a_quantity < quantity):
                        a_quantity += qty_step

                if (f["filterType"] == "MIN_NOTIONAL"):
                    # Set Decimal context to base symbol precision
                    getcontext().prec = s["baseAssetPrecision"]
                    min_notional = Decimal(f["minNotional"])
                    # Set quantity to min notional
                    if (a_quantity is None or a_quantity < min_notional):
                        a_quantity = min_notional

            # Try to fix MIN_NOTIONAL filter errors when selling to BTC or ETH
            if (symbol.endswith("BTC") or symbol.endswith("ETH")):
                while(a_quantity * a_price < 0.001):
                    a_quantity += qty_step

        return a_quantity, a_price

    def cancel_stale_orders(self, symbol):
        """Cancels open orders over a defined lifetime. Returns a list of
        cancelled orders in the binance order format.

        Keyword arguments:
        symbol (str) -- the Binance trade symbol to check for stale orders
        """
        cancelled_orders = []
        now = datetime.datetime.now()
        for order in self.client.get_open_orders(symbol=symbol, recvWindow=10000):
            then = datetime.datetime.fromtimestamp(Decimal(order["time"]) / Decimal(1000.0))
            delta = now - then
            age_seconds = delta.total_seconds()
            if (age_seconds > self.order_lifetime):
                self.client.cancel_order(symbol=symbol, orderId=order["orderId"], recvWindow=10000)
                self.db.delete_order(order["orderId"])
                cancelled_orders.append(order)
        return cancelled_orders

    def trade(self, symbol_pairs):
        """Initiates the trader using a list of base and quote symbol pairs,
        where the candlestick patterns of the base symbol are analyzed for
        favorable buy/sell situations and the quote symbol is used as the
        order currency.

        Symbol pairs must exist on Binance as valid order options.

        Keyword arguments:
        symbol_pairs (list) -- the list of dicts containing base and quote
            symbols. Each symbol pair will be analyzed in order each run.
            Example: [{"base": "IOTA", "quote": "ETH"}]
        """
        self.symbol_pairs = symbol_pairs
        # Log
        log_name = "binanza"
        logger = Log(log_name)

        # Run function
        run = True
        while (run):
            try:
                # Get logger
                log = logging.getLogger(log_name)

                # Re-read config
                self.read_config()

                # Init database
                self.db = DB()

                for symbol_pair in self.symbol_pairs:
                    # Client
                    self.client = Client(self.api_key, self.api_secret)

                    # Settings
                    base_symbol = symbol_pair["base"]
                    quote_symbol = symbol_pair["quote"]
                    symbol = "{}{}".format(base_symbol, quote_symbol)
                    buy_batch = Decimal(symbol_pair["buy_batch"])
                    sell_batch = Decimal(symbol_pair["sell_batch"])

                    log.info("{}/{}".format(base_symbol, quote_symbol))

                    # Get balances and exchange info
                    self.get_balances([base_symbol, quote_symbol])
                    self.exchange_info = self.client.get_exchange_info()

                    # Cancel stale orders
                    cancelled_orders = self.cancel_stale_orders(symbol)
                    for order in cancelled_orders:
                        log.info("Cancelled order {}: {} {} @ {} {}/{}".format(order["orderId"], order["origQty"], base_symbol, order["price"], quote_symbol, base_symbol))
                    
                    # Analyze trend
                    candles = self.client.get_klines(symbol=symbol, interval=self.kline_interval)
                    results = self.analyze_candles(candles)
                    inputs = results["inputs"]
                    indication = results["indication"]
                    recognized_patterns = results["patterns"]
                    #analyses = results["analyses"]

                    # Debug
                    log.debug("  Volumes: {}".format([int(v) for v in inputs["volume"][-5:]]))
                    #log.debug("  LAST 5 CANDLESTICK INPUTS:")
                    #for param in ["open", "high", "low", "close"]:
                    #    log.debug("    {}: {}".format(param, inputs[param][-5:]))
                    #log.debug("  LAST 5 CANDLESTICK ANALYSES:")
                    #for anal in sorted(analyses.keys()):
                    #    res = str(analyses[anal][-5:])
                    #    while ("  " in res):
                    #        res = res.replace("  ", " ")
                    #    res = res.replace("[ ", "[")
                    #    res = res.replace(" ]", "]")
                    #    log.debug("    {:<31}: {:<20}".format(anal, res).rstrip())

                    # Set Decimal to quote symbol
                    self.set_decimal_precision(symbol, quote_symbol)
                    price = Decimal(inputs["close"][-1])

                    # Update previously stored patterns
                    self.db.update_patterns(base_symbol, quote_symbol, price)

                    # Determine buy/sell, continue if no patterns detected
                    if (indication == 0.0):
                        log.info("  No patterns")
                        continue

                    # Print recognized patterns and available balances, and append to database
                    log.info("Average indication value: {}".format(round(indication, 2)))
                    log.info("Pattern(s): {}".format(", ".join(["{} [{}]".format(p["name"], p["indication"]) for p in recognized_patterns])))
                    for pattern in recognized_patterns:
                        self.db.add_pattern(pattern, base_symbol, quote_symbol, price)
                    log.info("Balances:")
                    for b in self.balances:
                        log.info("  {}: {}".format(b, self.balances[b]))
                    
                    if (indication > 0.0):
                        # BUY if balance, price and quantity is OK
                        base_quantity = self.balances[quote_symbol] * buy_batch
                        if (quote_symbol in self.min_balance and self.balances[quote_symbol] - base_quantity < self.min_balance[quote_symbol]):
                            base_quantity = self.balances[quote_symbol] - self.min_balance[quote_symbol]
                        # Check order for correct values according to exchange info
                        quantity, price = self.check_order(symbol, base_quantity / price, price)
                        if (quantity is None or price is None):
                            log.info("  NO BUY: Symbol closed for trading")
                        # Check balances for min/max settings
                        elif not (self.balance_is_ok(quote_symbol, base_quantity)):
                            log.warning("  NO BUY: Minimum {} balance limit reached".format(quote_symbol))
                        elif (base_symbol in self.max_balance and self.balances[base_symbol] + base_quantity > self.max_balance[base_symbol]):
                            log.warning("  NO BUY: Maximum {} balance limit reached".format(base_symbol))
                        # Check price
                        elif not (self.buy_price_is_right(symbol_pair, price)):
                            log.warning("  NO BUY: Held off buy due to high price compared to recent sell orders")
                        else:
                            # Perform buy
                            try:
                                # Send order and append to database
                                log.info("BUY ORDER: {} {} @ {} {}/{} (total: {} {})".format(quantity, base_symbol, price, quote_symbol, base_symbol, quantity * price, quote_symbol))
                                order = self.client.order_limit_buy(symbol=symbol, quantity=quantity, price=price)
                                self.db.add_order(order, base_symbol, quote_symbol, self.balances[base_symbol], self.balances[quote_symbol])
                            except BinanceAPIException as e:
                                log.error(e.status_code)
                                log.error(e.message)

                    elif (indication < 0.0):
                        # SELL if balance, price and quantity is OK
                        self.set_decimal_precision(base_symbol, quote_symbol)
                        quantity = self.balances[base_symbol] * sell_batch
                        quote_quantity = quantity * price
                        # Keep defined minimum balance 
                        if (base_symbol in self.min_balance and self.balances[base_symbol] - quantity < self.min_balance[base_symbol]):
                            quantity = self.balances[base_symbol] - self.min_balance[base_symbol]
                        quantity, price = self.check_order(symbol, quantity, price)
                        if (quantity is None or price is None):
                            log.info("  NO SELL: Symbol closed for trading")
                        elif not (self.balance_is_ok(base_symbol, quantity)):
                            log.warning("  NO SELL: Minimum {} balance limit reached".format(base_symbol))
                        elif (quote_symbol in self.max_balance and self.balances[quote_symbol] + quote_quantity > self.max_balance[quote_symbol]):
                            log.warning("  NO BUY: Maximum {} balance limit reached".format(base_symbol))
                        elif not (self.sell_price_is_right(symbol_pair, price)):
                            log.warning("  NO SELL: Held off sell due to low price compared to recent buy orders")
                        else:
                            try:
                                log.info("SELL ORDER: {} {} @ {} {}/{} (total: {} {})".format(quantity, base_symbol, price, quote_symbol, base_symbol, quantity * price, quote_symbol))
                                order = self.client.order_limit_sell(symbol=symbol, quantity=quantity, price=price)
                                self.db.add_order(order, base_symbol, quote_symbol, self.balances[base_symbol], self.balances[quote_symbol])
                            except BinanceAPIException as e:
                                log.error(e.status_code)
                                log.error(e.message)
                            except:
                                log.exception("Order error")
                                continue

                # Optionally send orders and errors by mail
                if (logger.has_order() and self.gmail is not None and len(self.orders_to_mail) > 0):
                    logger.send_gmail(self.gmail["username"], self.gmail["password"], self.orders_to_mail, subject="Binanza order")
                # Optionally send last run log by mail on errors
                if (logger.has_errors() and self.gmail is not None and len(self.errors_to_mail) > 0):
                    logger.send_gmail(self.gmail["username"], self.gmail["password"], self.errors_to_mail, subject="Binanza error")

            except:
                # Print/log errors
                print(traceback.format_exc())
                log.exception("Script error")
                # Optionally send last run log by mail
                if (logger.has_errors() and self.gmail is not None and len(self.errors_to_mail) > 0):
                    logger.send_gmail(self.gmail["username"], self.gmail["password"], self.errors_to_mail, subject="Binanza error")

            finally:
                # Shutdown logging to avoid handler chaos
                logging.shutdown()

                # Continuous running or break
                if (self.continuous):
                    time.sleep(self.sleep_duration)
                else:
                    run = False


class DB(object):
    """Database handler for storing orders, recognized patterns etc.

    Keyword arguments:
    {db} (str) -- a path to a database. Is created if it does not exist,
        defaults to binanza.db in the script folder
    """
    def __init__(self, db=os.path.join(sys.path[0], "binanza.db")):
        self.db = db
        # Database schema definition
        self.tables = [
            {
                "name": "orders",
                "fields": [
                    {
                        "name": "timestamp",
                        "type": "DATE"
                    }, {
                        "name": "type",
                        "type": "TEXT"
                    }, {
                        "name": "base",
                        "type": "TEXT"
                    }, {
                        "name": "quote",
                        "type": "TEXT"
                    }, {
                        "name": "quantity",
                        "type": "NUMERIC"
                    }, {
                        "name": "price",
                        "type": "NUMERIC"
                    }, {
                        "name": "base_balance",
                        "type": "NUMERIC"
                    }, {
                        "name": "quote_balance",
                        "type": "NUMERIC"
                    }, {
                        "name": "order_id",
                        "type": "NUMERIC"
                    }
                ]
            }, {
                "name": "patterns",
                "fields": [
                    {
                        "name": "timestamp",
                        "type": "DATE"
                    }, {
                        "name": "base",
                        "type": "TEXT"
                    }, {
                        "name": "quote",
                        "type": "TEXT"
                    }, {
                        "name": "pattern",
                        "type": "TEXT"
                    }, {
                        "name": "indication",
                        "type": "NUMERIC"
                    }, {
                        "name": "price",
                        "type": "NUMERIC"
                    }, {
                        "name": "m5",
                        "type": "NUMERIC"
                    }, {
                        "name": "m10",
                        "type": "NUMERIC"
                    }, {
                        "name": "m15",
                        "type": "NUMERIC"
                    }, {
                        "name": "m30",
                        "type": "NUMERIC"
                    }, {
                        "name": "m60",
                        "type": "NUMERIC"
                    }, {
                        "name": "m120",
                        "type": "NUMERIC"
                    }
                ]
            }
        ]
        # Create database if it does not exist
        if not (os.path.exists(db)):
            self.create_database()
        else:
            self.check_database()


    def create_database(self):
        """Create database and tables based on defined schema."""
        for table in self.tables:
            self.add_table(table)
        return
            

    def add_table(self, table):
        """Add a table to the database."""
        sql = sqlite3.connect(self.db)
        c = sql.cursor()
        name = table["name"]
        fields = ["{} {}".format(field["name"], field["type"]) for field in table["fields"]]
        query = "CREATE TABLE {} ({})".format(name, ", ".join(fields))
        c.execute(query)
        sql.commit()
        sql.close()
        return

    def add_field(self, table, field):
        sql = sqlite3.connect(self.db)
        c = sql.cursor()
        query = "ALTER TABLE {} ADD COLUMN {} {}".format(table["name"], field["name"], field["type"])
        c.execute(query)
        sql.commit()
        sql.close()
        return

    def check_database(self):
        """Adds missing columns to database if schema has changed."""
        sql = sqlite3.connect(self.db)
        c = sql.cursor()
        # Get list of tables
        c.execute("SELECT name FROM sqlite_master WHERE type='table';")
        db_tables = [t[0] for t in c.fetchall()]
        sql.close()
        for table in self.tables:
            # Add table if missing
            if (table["name"] not in db_tables):
                self.add_table(table)
            # Select a row to get cursor object
            sql = sqlite3.connect(self.db)
            c = sql.cursor()
            cursor = c.execute('SELECT * from {} LIMIT 1'.format(table["name"]))
            sql.close()
            fields = [d[0] for d in cursor.description]
            for f in table["fields"]:
                if (f["name"] not in fields):
                    self.add_field(table, f)
        return

    def insert_rows(self, table, rows):
        """Insert rows into database table.

        Keyword arguments:
        table (str) -- the name of the table
        rows (list) -- the list of rows to insert
        """
        if (len(rows) > 0):
            for i in range(0, len(rows)):
                rows[i] = self.localize(rows[i], table)
            sql = sqlite3.connect(self.db)
            c = sql.cursor()
            fields = []
            for i in range(0, len(rows[0])):
                fields.append("?")
            execute_string = "INSERT INTO {} VALUES ({})".format(table, ", ".join(fields))
            c.executemany(execute_string, rows)
            sql.commit()
            sql.close()

    def delete_rows(self, table, where):
        """Deletes a row from a database table based on a query.

        Keyword arguments:
        table (str) -- the name of the table
        where (str) -- the where query for rows to delete
        """
        sql = sqlite3.connect(self.db)
        c = sql.cursor()
        query = "DELETE FROM {} WHERE {}".format(table, where)
        c.execute(query)
        sql.commit()
        sql.close()
        return

    def add_order(self, order, base_symbol, quote_symbol, base_balance=None, quote_balance=None):
        """Adds an order to the orders table using
        the binance-python API response."""
        self.insert_rows("orders", [
            [
                self.get_timestamp(),
                order["side"].lower(),
                base_symbol,
                quote_symbol,
                order["origQty"],
                order["price"],
                base_balance,
                quote_balance,
                order["orderId"]
            ]
        ])
        return

    def delete_order(self, order_id):
        self.delete_rows("orders", "order_id = '{}'".format(order_id))
        return

    def add_pattern(self, pattern, base_symbol, quote_symbol, price):
        """Adds a pattern to the patterns table."""
        row = [self.get_timestamp(), base_symbol, quote_symbol, pattern["name"], pattern["indication"], price, None, None, None, None, None, None]
        self.insert_rows("patterns", [row])
        return

    def update_patterns(self, base_symbol, quote_symbol, price):
        """Updates pattern rows in database with price values
        at defined intervals."""
        minutes = [5, 10, 15, 30, 60, 120]
        sql = sqlite3.connect(self.db)
        c = sql.cursor()
        now = self.get_timestamp()
        for i in range(0, len(minutes)):
            current = minutes[i]
            field = "m{}".format(minutes[i])
            last_m = minutes[i - 1] if (i != 0) else 0
            next_m = minutes[i + 1] if (i != len(minutes) - 1) else current * 2
            last_s = (current - (current - last_m) / 2) * 60 if (i != 0) else 0
            next_s = (current + (next_m - current) / 2) * 60
            query = "UPDATE patterns SET {} = {} WHERE {} IS NULL AND base = '{}' AND quote = '{}' AND strftime('%s', '{}') - strftime('%s', timestamp) BETWEEN {} AND {}".format(field, price, field, base_symbol, quote_symbol, now, last_s, next_s)
            c.execute(query)
        sql.commit()
        sql.close()
        return

    def get_timestamp(self):
        """Returns current timestamp in database friendly format."""
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def localize(self, row, table_name):
        table = [t for t in self.tables if t["name"] == table_name][0]
        for i in range(0, len(row)):
            if (row[i] is not None):
                if (isinstance(row[i], str)):
                    row[i] = row[i].strip()
                field_type = table["fields"][i]["type"]
                if (field_type in ["NUMERIC", "INTEGER"]):
                    if (isinstance(row[i], str)):
                        row[i] = row[i].replace(",", ".")
                        row[i] = row[i].replace("%", "")
                        row[i] = row[i].replace(" ", "")
                    if (field_type == "NUMERIC"):
                        row[i] = float(row[i])
                    if (field_type == "INTEGER"):
                        row[i] = int(row[i])
        return row
