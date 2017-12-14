#!/usr/bin/python3
# -*- coding: utf-8 -*-
import datetime
from decimal import *
from email.mime.text import MIMEText
import logging
import os
import numpy as np
import smtplib
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
    level -- the logging level enum to use, set to logging.DEBUG to log last
        five pattern analysis inputs and method results
    """
    def __init__(self, log_name="binanza", level=logging.INFO):
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s -- %(message)s', datefmt='%Y.%m.%d %H:%M:%S')
        self.log = logging.getLogger(log_name)
        # Configure
        self.log.setLevel(level)
        formatter = logging.Formatter('%(asctime)s %(levelname)s -- %(message)s', '%Y.%m.%d %H:%M:%S')
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
        # Other stuff
        self.last_error_sent = None
        return

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
        {trade_batch} (float) -- a fraction to use for calculating quantity to
            to trade at each interval if a favorable pattern is detected.
            Defaults to 0.05 (= 5%)
        {min_balance} (dict) -- a dict of symbols and minimum balances as
            key-value pairs used to determine if an trade should go through.
            If no minimum balance is defined for a symbol the trader will
            always try to trade the defined fraction on pattern recognition
        {kline_interval} (str) -- a candlestick time interval to fetch for
            analysis, the last 500 bins will be fetched. Defaults to 5 minutes
        {continuous} (bool) -- a flag specifying if the trader should
            continuously keep trading with a specified break between runs.
            Defaults to a single analysis (and trade if pattern found)
        {sleep_duration} (int) -- a number of seconds to sleep between runs if
            running in continuous mode
        {gmail} (dict) -- a dict with gmail authorization info and either a
            list of addresses to send order reports to or a list of addresses
            for error logs. Format: {
                "username": <gmail user>,
                "password": <gmail pass>,
                "errors_to": <list of recipients>,
                "orders_to": <list of recipients>
            }
        """
        self.api_key = api_key
        self.api_secret = api_secret
        for key, item in kwargs.items():
            setattr(self, key, item)

        # Set default values and convert supplied floats to Decimals
        if not (hasattr(self, "trade_batch")):
            self.trade_batch = Decimal(0.05)
        else:
            self.trade_batch = Decimal(self.trade_batch)
        if not (hasattr(self, "min_balance")):
            self.min_balance = {}
        else:
            for symbol in self.min_balance:
                self.min_balance[symbol] = Decimal(self.min_balance[symbol])
        if not (hasattr(self, "kline_interval")):
            self.kline_interval = KLINE_INTERVAL_5MINUTE
        if not (hasattr(self, "continuous")):
            self.continuous = False
        if not (hasattr(self, "sleep_duration")):
            self.sleep_duration = 300
        if not (hasattr(self, "gmail")):
            self.gmail = None

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
                "validators": [reversal_if_trend]
            },
            "Engulfing pattern": {
                "f": CDLENGULFING,
                "validators": [reversal_if_previous_trend_skip1]
            },
            "Evening doji star": {
                "f": CDLEVENINGDOJISTAR,
                "validators": [reversal_if_trend]
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
                "validators": [reversal_if_trend]
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

    def get_balances(self, symbols):
        """ Queries the Binance API for all account balances, and compiles
        a dict of symbols and free balance as key-value pairs.
        
        Example input: ["ETH", "IOTA"]

        Keyword arguments:
        symbols (list) -- the list of Binance symbols for which to get balances
        """
        self.balances = {}
        account = self.client.get_account()
        for balance in account["balances"]:
            for symbol in symbols:
                if (balance["asset"] == symbol):
                    self.balances[symbol] = Decimal(balance["free"])
        return

    def analyze_candles(self, candles):
        """Uses TA-Lib to analyze candlesticks, and returns a result dict with
        inputs used and analysis results as a dict with pattern name and
        indication value as key-value pairs.

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
                recognized_patterns.append("{} [{}]".format(pattern, indication))
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
            symbol not in self.balances or
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

    def get_order_average(self, symbol, side):
        """Gets all orders of the Binance account (500 max) from the last week,
        extracts successfull sell or buy orders and calculates an average price
        per quantity.

        Keyword arguments:
        symbol (str) -- the Binance symbol to fetch orders for
        side (str) -- the type of order to check, "BUY" or "SELL"
        """
        orders = self.client.get_all_orders(symbol=symbol)

        # Calculate average
        order_sum = Decimal(0.0)
        n_orders = Decimal(0.0)
        now = datetime.datetime.now()
        for order in orders:
            then = datetime.datetime.fromtimestamp(Decimal(order["time"]) / Decimal(1000.0))
            delta = now - then
            age_days = self.seconds_to_days(delta.total_seconds())
            if (age_days < 14 and order["side"] == side and order["status"] in ["PARTIALLY_FILLED", "FILLED"]):
                order_sum += Decimal(order["price"]) * Decimal(order["executedQty"])
                n_orders += Decimal(order["executedQty"])

        # Don't make assumptions when few historical orders
        if (n_orders < 5):
            return None

        # Return average (including fee)
        avg_price = order_sum / n_orders
        avg_price = avg_price * Decimal(1.05)
        return avg_price

    def buy_price_is_right(self, symbol, price):
        """Checks if the price of a buy order is favorable by
        comparing it to the average of recent (last 500) sell orders.
        
        Keyword arguments:
        symbol (str) -- the buy order symbol to check
        price (Decimal) -- the market price per quantity 
        """
        avg = self.get_order_average(symbol, "SELL")
        if (avg is not None and price > avg * Decimal(1.5)):
            # Buy price much higher than average sell order
            return False
        return True

    def sell_price_is_right(self, symbol, price):
        """Checks if the price of a sell order is favorable by
        comparing it to the average of recent (last 500) buy orders.
        
        Keyword arguments:
        symbol (str) -- the sell order symbol to check
        price (Decimal) -- the market price per quantity 
        """
        avg = self.get_order_average(symbol, "BUY")
        if (avg is not None and price < avg):
            # Sell price lower than average buy order
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
            if (s["symbol"] == symbol):
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

        return a_quantity, a_price

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
        # Log
        log_name = "binanza"
        logger = Log(log_name)

        # Run function
        run = True
        while (run):
            try:
                # Get logger
                log = logging.getLogger(log_name)

                # Client
                self.client = Client(self.api_key, self.api_secret)
                for symbol_pair in symbol_pairs:
                    log.info("---------------------------------------")
                    # Settings
                    base_symbol = symbol_pair["base"]
                    quote_symbol = symbol_pair["quote"]
                    symbol = "{}{}".format(base_symbol, quote_symbol)

                    log.info("Inspecting {}/{}".format(base_symbol, quote_symbol))

                    # Get balances and exchange info
                    self.get_balances([base_symbol, quote_symbol])
                    self.exchange_info = self.client.get_exchange_info()
                    
                    # Analyze trend
                    candles = self.client.get_klines(symbol=symbol, interval=self.kline_interval)
                    results = self.analyze_candles(candles)
                    inputs = results["inputs"]
                    analyses = results["analyses"]
                    indication = results["indication"]
                    recognized_patterns = results["patterns"]

                    # Debug
                    #log.debug("  LAST 5 CANDLESTICK INPUTS:")
                    #for param in ["open", "high", "low", "close", "volume"]:
                    #    log.debug("    {}: {}".format(param, inputs[param][-5:]))
                    log.debug("  LAST 5 CANDLESTICK ANALYSES:")
                    for anal in sorted(analyses.keys()):
                        log.debug("    {}: {}".format(anal, analyses[anal][-5:]))
                    # EXAMPLE BUY
                    #self.set_decimal_precision(symbol, quote_symbol)
                    #price = Decimal(inputs["close"][-1])
                    #base_quantity = self.balances[quote_symbol] * self.trade_batch
                    #quantity, price = self.check_order(symbol, base_quantity / price, price)
                    #if (quantity is None or price is None):
                    #    log.debug("  No buy, symbol closed for trading")
                    #log.debug("EXAMPLE BUY ORDER: {} {} @ {} {}/{} (total: {} {})".format(quantity, base_symbol, price, quote_symbol, base_symbol, quantity * price, quote_symbol))
                    # EXAMPLE SELL
                    #quantity = self.balances[base_symbol] * self.trade_batch
                    #quantity, price = self.check_order(symbol, quantity, price)
                    #if (quantity is None or price is None):
                    #    log.debug("  No sell, symbol closed for trading")
                    #log.debug("EXAMPLE SELL ORDER: {} {} @ {} {}/{} (total: {} {})".format(quantity, base_symbol, price, quote_symbol, base_symbol, quantity * price, quote_symbol))

                    # Determine buy/sell
                    if (indication == 0.0):
                        log.info("  No pattern found")
                    else:
                        # Set Decimal to quote symbol
                        self.set_decimal_precision(symbol, quote_symbol)
                        price = Decimal(inputs["close"][-1])

                        # Print recognized patterns and available balances
                        log.info("Average indication value: {}".format(round(indication, 2)))
                        log.info("Pattern(s) found: {}".format(", ".join(recognized_patterns)))
                        log.info("Balances:")
                        for b in self.balances:
                            log.info("  {}: {}".format(b, self.balances[b]))
                        
                        if (indication > 0.0):
                            # BUY if balance, price and quantity is OK
                            base_quantity = self.balances[quote_symbol] * self.trade_batch
                            quantity, price = self.check_order(symbol, base_quantity / price, price)
                            if (quantity is None or price is None):
                                log.info("  No buy, symbol closed for trading")
                            log.info("BUY ORDER: {} {} @ {} {}/{} (total: {} {})".format(quantity, base_symbol, price, quote_symbol, base_symbol, quantity * price, quote_symbol))
                            if not (self.balance_is_ok(quote_symbol, base_quantity)):
                                log.warning("  NO BUY: Minimum {} balance limit reached.".format(quote_symbol))
                            elif not (self.buy_price_is_right(symbol, price)):
                                log.warning("  NO BUY: Held off buy due to high price compared to recent sell orders")
                            else:
                                try:
                                    self.client.order_limit_buy(symbol=symbol, quantity=quantity, price=price)
                                except BinanceAPIException as e:
                                    log.error(e.status_code)
                                    log.error(e.message)

                        elif (indication < 0.0):
                            # SELL if balance, price and quantity is OK
                            self.set_decimal_precision(base_symbol, quote_symbol)
                            quantity = self.balances[base_symbol] * self.trade_batch
                            quantity, price = self.check_order(symbol, quantity, price)
                            if (quantity is None or price is None):
                                log.info("  No sell, symbol closed for trading")
                            log.info("SELL ORDER: {} {} @ {} {}/{} (total: {} {})".format(quantity, base_symbol, price, quote_symbol, base_symbol, quantity * price, quote_symbol))
                            if not (self.balance_is_ok(base_symbol, quantity)):
                                log.warning("  NO SELL: Minimum {} balance limit reached".format(base_symbol))
                            elif not (self.sell_price_is_right(symbol, price)):
                                log.warning("  NO SELL: Held off sell due to low price compared to recent buy orders")
                            else:
                                try:
                                    self.client.order_limit_sell(symbol=symbol, quantity=quantity, price=price)
                                except BinanceAPIException as e:
                                    log.error(e.status_code)
                                    log.error(e.message)

                        # Optionally send orders by mail
                        if (self.gmail is not None):
                            logger.send_gmail(self.gmail["username"], self.gmail["password"], self.gmail["orders_to"], subject="Binanza order")

                # Shutdown logging to avoid handler chaos
                logging.shutdown()

            except:
                # Print/log errors
                print(traceback.format_exc())
                log.exception("Script error")
                # Optionally send last run log by mail
                if (logger.has_errors() and self.gmail is not None):
                    # Send one mail per day (or individual script process) to avoid spam
                    today = datetime.datetime.now().strftime("%Y.%m.%d")
                    if (logger.last_error_sent is None or logger.last_error_sent != today):
                        logger.send_gmail(self.gmail["username"], self.gmail["password"], self.gmail["errors_to"], subject="Binanza error")
                        logger.last_error_sent = today

            finally:
                # Continuous running or break
                if (self.continuous):
                    time.sleep(self.sleep_duration)
                else:
                    run = False
