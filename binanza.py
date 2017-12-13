#!/usr/bin/python3
# -*- coding: utf-8 -*-
import datetime
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
        {trade_batch} (float) -- a raction of either buy or base symbol
            balance to trade each interval if a favorable pattern
            is detected. Defaults to 5%
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

        # Set default values
        if not (hasattr(self, "trade_batch")):
            self.trade_batch = 0.05
        if not (hasattr(self, "min_balance")):
            self.min_balance = {}
        if not (hasattr(self, "kline_interval")):
            self.kline_interval = KLINE_INTERVAL_5MINUTE
        if not (hasattr(self, "continuous")):
            self.continuous = False
        if not (hasattr(self, "sleep_duration")):
            self.sleep_duration = 300
        if not (hasattr(self, "gmail")):
            self.gmail = None

        # Candlestick pattern functions
        self.patterns_bull = {
            "Abandoned baby": CDLABANDONEDBABY,
            "Shooting star": CDLSHOOTINGSTAR,
            "Morning star": CDLMORNINGSTAR,
            "Three line strike": CDL3LINESTRIKE,
            "Three advancing white soldiers": CDL3WHITESOLDIERS,
            "Morning Doji Star": CDLMORNINGDOJISTAR
        }
        self.patterns_bear = {
            "Evening star": CDLEVENINGSTAR,
            "Two crows": CDL2CROWS,
            "Three Black Crows": CDL3BLACKCROWS,
            "Evening Doji Star": CDLEVENINGDOJISTAR
        }
        self.patterns_neutral = {
            #"Marubozu": CDLMARUBOZU
        }
        self.patterns = [self.patterns_bull, self.patterns_bear, self.patterns_neutral]
        return

    def get_balances(self, symbols):
        """ Queries the Binance API for all account balances, and compiles
        a dict of symbols and free balance as key-value pairs.
        
        Example input: ["ETH", "IOTA"]

        Keyword arguments:
        symbols (list) -- the list of Binance symbols for which to get balances
        """
        balances = {}
        account = self.client.get_account()
        for balance in account["balances"]:
            for symbol in symbols:
                if (balance["asset"] == symbol):
                    balances[symbol] = float(balance["free"])
        return balances

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

        # Skip last candle if volume less than 10% of previous candle
        # (indicates partial candlestick at start of Binance bin cutoff)
        if (len(candles) > 1):
            current_volume = float(candles[-1][5])
            previous_volume = float(candles[-2][5])
            if (current_volume < previous_volume * 0.1):
                del candles[-1]

        # Extract candlestick values to convert to numpy arrays
        open_ = [float(c[1]) for c in candles]
        high = [float(c[2]) for c in candles]
        low = [float(c[3]) for c in candles]
        close = [float(c[4]) for c in candles]
        volume = [float(c[5]) for c in candles]

        # Convert to numpy arrays
        inputs = {
            'open': np.asarray(open_, dtype=np.float64),
            'high': np.asarray(high, dtype=np.float64),
            'low': np.asarray(low, dtype=np.float64),
            'close': np.asarray(close, dtype=np.float64),
            'volume': np.asarray(volume, dtype=np.float64)
        }

        # Run all TA-Lib candlestick pattern analyses
        analyses = {}
        for pattern_list in self.patterns:
            for pattern in pattern_list:
                f = pattern_list[pattern]
                analyses[pattern] = f(inputs)

        # Return result object
        results = {
            "inputs": inputs,
            "analyses": analyses
        }
        return results

    def check_results(self, analyses):
        """Runs through all positive, negative and neutral candlestick pattern
        analyses and returns common trend indicators for both bull and bear
        indications.

        Keyword arguments:
        analyses (dict) -- the analyses result as returned from the
            analyze_candles() function
        """
        bullish = False
        bearish = False

        # Positive/negative indicators
        if (any(analyses[pattern][-1] != 0.0 for pattern in self.patterns_bull)):
            bullish = True
        if (any(analyses[pattern][-1] != 0.0 for pattern in self.patterns_bear)):
            bearish = True

        # Neutral patterns
        if (bullish is False and any(analyses[pattern][-1] >= 50 for pattern in self.patterns_neutral)):
            bullish = True
        if (bearish is False and any(analyses[pattern][-1] <= -50 for pattern in self.patterns_neutral)):
            bearish = True
        return bullish, bearish

    def balance_is_ok(self, symbol, balances):
        """Checks if a minimum balance is defined for a symbol and if the
        balance is over the limit.

        symbol (str) -- the currency symbol as defined by Binance
        balances (dict) -- the dict containing symbols and associated balances
            as key-value pairs
        """
        if (symbol not in balances or (symbol in self.min_balance and balances[symbol] < self.min_balance[symbol])):
            return False
        return True

    def get_order_average(self, symbol, side):
        """Gets all orders of the Binance account (500 max),
        extracts successfull sell or buy orders and calculates
        an average price per quantity.

        Keyword arguments:
        symbol (str) -- the Binance symbol to fetch orders for
        side (str) -- the type of order to check, "BUY" or "SELL"
        """
        orders = self.client.get_all_orders(symbol=symbol)

        # Calculate average
        order_sum = 0.0
        n_orders = 0.0
        for order in orders:
            if (order["side"] == side and order["status"] in ["PARTIALLY_FILLED", "FILLED"]):
                order_sum += float(order["price"]) * float(order["executedQty"])
                n_orders += float(order["executedQty"])

        # Don't make assumptions when few historical orders
        if (n_orders < 5):
            return None

        return order_sum / n_orders

    def buy_price_is_right(self, symbol, price):
        """Checks if the price of a buy order is favorable by
        comparing it to the average of recent (last 500) sell orders.
        
        Keyword arguments:
        symbol (str) -- the buy order symbol to check
        price (float) -- the market price per quantity 
        """
        avg = self.get_order_average(symbol, "SELL")
        if (avg is not None and price > avg):
            # Buy price higher than average sell order
            return False
        return True

    def sell_price_is_right(self, symbol, price):
        """Checks if the price of a sell order is favorable by
        comparing it to the average of recent (last 500) buy orders.
        
        Keyword arguments:
        symbol (str) -- the sell order symbol to check
        price (float) -- the market price per quantity 
        """
        avg = self.get_order_average(symbol, "BUY")
        if (avg is not None and price < avg):
            # Sell price lower than average buy order
            return False
        return True

    def trade(self, symbol_pairs):
        """Initiates the trader using a list of buy and base symbol pairs,
        where the candlestick patterns of the buy symbol are analyzed for
        favorable buy/sell situations and the base symbol is used as the
        order currency.

        Symbol pairs must exist on Binance as valid order options.

        Keyword arguments:
        symbol_pairs (list) -- the list of dicts containing buy and base
            symbols. Each symbol pair will be analyzed in order each run.
            Example: [{"buy": "IOTA", "base": "ETH"}]
        """
        try:
            # Log
            log_name = "binanza"
            logger = Log(log_name)
            # Run function
            run = True
            while (run):
                log = logging.getLogger(log_name)
                log.info("---------------------------------------")

                # Client
                self.client = Client(self.api_key, self.api_secret)
                for symbol_pair in symbol_pairs:
                    # Settings
                    buy_symbol = symbol_pair["buy"]
                    base_symbol = symbol_pair["base"]
                    symbol = "{}{}".format(buy_symbol, base_symbol)

                    log.info("Inspecting {}/{} patterns".format(buy_symbol, base_symbol))

                    # Get balances
                    balances = self.get_balances([buy_symbol, base_symbol])
                    
                    # Analyze trend
                    candles = self.client.get_klines(symbol=symbol, interval=self.kline_interval)
                    results = self.analyze_candles(candles)
                    inputs = results["inputs"]
                    analyses = results["analyses"]

                    # Debug
                    log.debug("\nLAST 5 CANDLESTICK INPUTS:")
                    log.debug("Open: {}".format(inputs["open"][-5:]))
                    log.debug("High: {}".format(inputs["high"][-5:]))
                    log.debug("Low: {}".format(inputs["low"][-5:]))
                    log.debug("Close: {}".format(inputs["close"][-5:]))
                    log.debug("Volume: {}".format(inputs["volume"][-5:]))
                    log.debug("  LAST 5 CANDLESTICK ANALYSES:")
                    for anal in sorted(analyses.keys()):
                        log.debug("    {}: {}".format(anal, analyses[anal][-5:]))

                    # Determine buy/sell
                    bullish, bearish = self.check_results(analyses)
                    if not (bullish or bearish):
                        log.info("  No pattern found".format(symbol))
                    else:
                        log.info("Balances:")
                        for b in balances:
                            log.info("  {}: {}".format(b, balances[b]))
                        price = float(inputs["close"][-1])

                        if (bullish):
                            # BUY if balance OK
                            if not (self.balance_is_ok(base_symbol, balances)):
                                log.warning("{} - NO BUY: Lower {} limit reached.".format(symbol, base_symbol))
                            elif not (self.buy_price_is_right(symbol, price)):
                                log.info("{} - NO BUY: Held off buy due to high price compared to recent sell orders".format(symbol))
                            else:
                                quantity = int(balances[base_symbol] * self.trade_batch / price)
                                if (quantity == 0):
                                    quantity = 1
                                log.info("{} - BUY ORDER: {} {} at {} {} ({} {} total)".format(symbol, quantity, buy_symbol, price, base_symbol, quantity * price, base_symbol))
                                self.client.order_limit_buy(symbol=symbol, quantity=quantity, price=price)

                        elif (bearish):
                            # SELL if balance and price OK
                            if not (self.balance_is_ok(buy_symbol, balances)):
                                log.warning("{} - NO SELL: Lower {} limit reached".format(symbol, buy_symbol))
                            elif not (self.sell_price_is_right(symbol, price)):
                                log.info("{} - NO SELL: Held off sell due to low price compared to recent buy orders".format(symbol))
                            else:
                                quantity = int(balances[buy_symbol] * self.trade_batch)
                                if (quantity == 0):
                                    quantity = 1
                                log.info("{} - SELL ORDER: {} {} at {} {} ({} {} total)".format(symbol, quantity, buy_symbol, price, base_symbol, quantity * price, base_symbol))
                                self.client.order_limit_sell(symbol=symbol, quantity=quantity, price=price)

                        # Log recognized patterns
                        for pattern_list in self.patterns:
                            for pattern in pattern_list:
                                if (analyses[pattern][-1]):
                                    log.info("{}: {}".format(pattern, analyses[pattern][-1]))

                        # Optionally send orders by mail
                        if (self.gmail is not None):
                            logger.send_gmail(self.gmail["username"], self.gmail["password"], self.gmail["orders_to"], subject="Binanza order")

                # Shutdown logging to avoid handler chaos
                logging.shutdown()
                # Continuous running or break
                if (self.continuous):
                    time.sleep(self.sleep_duration)
                else:
                    run = False

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
