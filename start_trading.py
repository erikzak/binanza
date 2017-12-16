#!/usr/bin/python3
# -*- coding: utf-8 -*-
import json
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
			gmail_user, gmail_pass, recipient_list = [s.strip() for s in line.split(",")]
			recipients = recipient_list.split(";")
		f.close()

		# Read config
		config_file = open(os.path.join(sys.path[0], "config.txt"))
		f = open(os.path.join(sys.path[0], "config.txt"))
		contents = ""
		for line in f:
			# Remove comment lines
			if not (line.strip().startswith("#")):
				contents += line
		f.close()
		config = json.loads(contents)

		# Create Binanza object: key and secret is required, all other params are optional and can be left out
		binanza = Binanza(
			key,
			secret,
			config_file = config_file,
			kline_interval = config["kline_interval"],
			min_balance = config["min_balance"],
			continuous = config["continuous"],
			sleep_duration = config["sleep_duration"],
			# Send errors or order info using a gmail account
			gmail = {
				"username": gmail_user,
				"password": gmail_pass,
				"errors_to": recipients[:-1]
				#"orders_to": recipients
			}
		)

		# Start trading
		binanza.trade(config["symbol_pairs"])

	except:
		print(traceback.format_exc())

if (__name__ == '__main__'):
	main()
