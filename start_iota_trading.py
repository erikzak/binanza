#!/usr/bin/python3
# -*- coding: utf-8 -*-
import os
import sys
import traceback
from binanza import Binanza

def main():
	try:
		
		# Get API key and secret (replace with own values)
		f = open(os.path.join(sys.path[0], "key.txt"))
		for line in f:
			key, secret = [s.strip() for s in line.split(",")]
			break
		f.close()

		# Get gmail auth (remove or replace with own values)
		f = open(os.path.join(sys.path[0], "gmail.txt"))
		for line in f:
			gmail_user, gmail_pass, recipient = [s.strip() for s in line.split(",")]
		f.close()

		# Create Binanza object: key and secret is required, all other params are optional and can be left out
		binanza = Binanza(
			key,
			secret,
			# Fraction of either buy or base symbol balance to trade each interval (if favorable pattern), defaults to 0.05
			trade_batch = 0.05,
			# Kline (candlestick) interval as binance enum (analysis = interval * 500), defaults to 5 minute intervals
			# Legal values: '1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M'
			kline_interval = "5m",
			# Minimum symbol balances to keep in funds, defaults to 0 for all
			min_balance = {
				"ETH": 0.1,
				"IOTA": 120,
				"XRP": 50
			},
			# Rerun function after sleep duration, will run once if not specified
			continuous = True,
			# Sleep duration between reruns in seconds, defaults to 5 minutes
			sleep_duration = 300,
			# Send errors or order info using a gmail account
			gmail = {
				"username": gmail_user,
				"password": gmail_pass,
				"errors_to": [recipient],
				"orders_to": [recipient]
			}
		)

		# Specify which symbol pairs to trade, where the candlestick patterns
		# of the buy symbol are analyzed for favorable buy/sell situations and
		# the base symbol is used as the order currency.
		# Symbol pairs must exist on Binance as valid order options.
		symbol_pairs = [{
			# Tries to favorably trade IOTA using ETH as base currency
			"buy": "IOTA",
			"base": "ETH"
		}, {
			# Tries to favorably trade XRP using ETH as base currency
			"buy": "XRP",
			"base": "ETH"
		}]

		# Start trading
		binanza.trade(symbol_pairs)

	except:
		print(traceback.format_exc())

if (__name__ == '__main__'):
	main()
