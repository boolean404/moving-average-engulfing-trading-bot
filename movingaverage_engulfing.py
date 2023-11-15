from datetime import datetime
import time
import pandas as pd
import pytz
import schedule
import secret
import ccxt

# for binance exchange
exchange = ccxt.binance({
    "apiKey": secret.BINANCE_API_KEY,
    "secret": secret.BINANCE_SECRET_KEY,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'
    }
})

# input data for trading
name = 'Moving Average Engulfing'
symbol = 'BTCUSDT'
timeframe = '5m'
usdt_amount = 110
leverage = 20

tp_sl_ratio = 2
sl_limit_value = 100
sl_extra_added_value = 10

# input data for strategy
ema1_period = 12
ema2_period = 26

# fetch last price of symbol
ticker = exchange.fetch_ticker(symbol)
last_price = float(ticker['last'])
amount = usdt_amount / last_price

# global variables
bot_status = True
adjusted_leverage = False
in_long_position = False
in_short_position = False
tp_sl_put_for_long = True
tp_sl_put_for_short = True

# get bot start run time
def get_bot_start_run_time():
    return time.strftime('%Y-%m-%d %H:%M:%S')

# fetch your account balance
def get_balance():
    account_info = exchange.fetch_balance()
    return round(account_info['total']['USDT'], 2)

# adjust leverage
def adjust_leverage():
    global adjusted_leverage
    response = exchange.fapiprivate_post_leverage({
        'symbol': symbol,
        'leverage': leverage
    })
    adjusted_leverage = True
    print(f"\n=> Leverage adjusted successfully to: {response['leverage']}x\n")

# start check bull candle
def check_bull_candle(data):
    bull_candle = None
    
    # 3 Line Strike
    bullSig = data['prev_close3'] < data['prev_open3'] and data['prev_close2'] < data['prev_close2'] and data['open'] < data['prev_open1'] and data['close'] > data['prev_open1']
    bearSig = data['prev_close3'] > data['prev_open3'] and data['prev_close2'] > data['prev_close2'] and data['open'] > data['prev_open1'] and data['close'] < data['prev_open1']
    # Engulfing Candle
    bullishEngulfing = data['open'] < data['prev_open1'] and data['close'] > data['prev_open1']
    bearishEngulfing = data['open'] > data['prev_open1'] and data['close'] < data['prev_open1']

    if bullSig or bullishEngulfing:
        bull_candle = True
    if bearSig or bearishEngulfing:
        bull_candle = False
    return bull_candle
# end check bull candle

# simple moving average calculation
def get_simple_moving_average(df, period):
    return round(df['close'].rolling(window=period).mean(), 2)

# exponential  moving average calculation
def get_exponential_moving_average(df, period):
    return round(df['close'].ewm(span=period, adjust=False).mean(), 2)

def check_in_uptrend(df):
    # df['in_uptrend'] = None
    if df['ema1'] > df['ema2']:
        df['in_uptrend'] = True
    else:
        df['in_uptrend'] = False
    return df['in_uptrend']

def get_data_frame(df):
    df['prev_open1'] = df['open'].shift(1)
    df['prev_open2'] = df['open'].shift(2)
    df['prev_open3'] = df['open'].shift(3)
    df['prev_close1'] = df['close'].shift(1)
    df['prev_close2'] = df['close'].shift(2)
    df['prev_close3'] = df['close'].shift(3)
    df['prev_low'] = df['low'].shift(1)
    df['prev_high'] = df['high'].shift(1)
    # df['ema11'] = get_simple_moving_average(df, ema11_period)
    # df['ema12'] = get_simple_moving_average(df, ema12_period)
    df['ema1'] = get_exponential_moving_average(df, ema1_period)
    df['ema2'] = get_exponential_moving_average(df, ema2_period)
    df['bull_candle'] = df.apply(check_bull_candle, axis=1)
    df['in_uptrend'] = df.apply(check_in_uptrend, axis=1)

# Fetch open positions
def get_open_positions():
    # positions = exchange.fetch_positions_risk(symbols=[symbol])
    positions = exchange.fapiprivatev2_get_positionrisk()
    return [position for position in positions if position['symbol'] == symbol]

# Fetch open orders
def get_open_orders():
    return exchange.fetch_open_orders(symbol)

# create take profit market order
def create_take_profit_market_order(positionSide, amount, stopPrice): # take profit
    side = None
    take_profit_market_params = {
        'positionSide': positionSide,            
    }
    if positionSide == 'SHORT':
        side = 'buy'
    if positionSide == 'LONG':
        side = 'sell'
    return exchange.create_stop_order(symbol=symbol, side=side, type='take_profit_market',amount=abs(amount), stopPrice=stopPrice, params=take_profit_market_params)

# create stop market order
def create_stop_market_order(positionSide, amount, stopPrice): # stop loss
    side = None
    stop_market_params = {
        'positionSide': positionSide,
    }
    if positionSide == 'SHORT':
        side = 'buy'
    if positionSide == 'LONG':
        side = 'sell'
    return exchange.create_stop_order(symbol=symbol, side=side, type='stop_market',amount=abs(amount), stopPrice=stopPrice, params=stop_market_params)

# change default timezone
def change_datetime_zone(update_time, timezone='Asia/Yangon'):
    utc_datetime = datetime.utcfromtimestamp(update_time)
    target_timezone = pytz.timezone(timezone)  # Replace timezone with the desired timezone
    return utc_datetime.replace(tzinfo=pytz.utc).astimezone(target_timezone) # retun is updatetime

# start check buy sell orders
def check_buy_sell_orders(df):
    global in_long_position
    global in_short_position
    global tp_sl_put_for_long
    global tp_sl_put_for_short
    global tp_sl_ratio
    global sl_limit_value
    global sl_extra_added_value

    last_row_index = len(df.index) - 1
    previous_row_index = last_row_index - 1

    print(df.tail(5))

    # market changed
    if not df['in_uptrend'][previous_row_index-1] and df['in_uptrend'][previous_row_index]:
        print("\n=> Market just changed to UP-TREND!")
    if df['in_uptrend'][previous_row_index-1] and not df['in_uptrend'][previous_row_index]:
        print("\n=> Market just changed to DOWN-TREND!")
    
    # get open position 
    open_positions = get_open_positions()
    # print(open_positions)

    for position in open_positions:
        position_symbol = position['symbol']
        position_side = position['positionSide']
        position_leverage = position['leverage']
        position_entry_price = float(position['entryPrice'])
        position_mark_price = float(position['markPrice'])
        position_amount = float(position['positionAmt'])
        position_pnl = round(float(position['unRealizedProfit']), 2)
        position_liquidation_price = round(float(position['liquidationPrice']), 2)
        position_amount_usdt =  round((position_amount * position_entry_price), 2)
        position_update_time = float(position['updateTime']) / 1000.0
        
        # change default timezone to local
        position_running_time = change_datetime_zone(position_update_time).strftime('%Y-%m-%d %H:%M:%S')

        # get sl value
        def get_sl_value(positionSide):
            sl_value = 0
            prev_low = float(df['prev_low'][previous_row_index])
            prev_high = float(df['prev_high'][previous_row_index])
                
            if positionSide == 'LONG':
                sl_value =  position_entry_price - (prev_low - sl_extra_added_value)
                if sl_value > sl_limit_value:
                    sl_value = sl_limit_value

            if positionSide == 'SHORT':
                sl_value =  (prev_high + sl_extra_added_value) - position_entry_price
                if sl_value > sl_limit_value:
                    sl_value = sl_limit_value
            return sl_value

        # get sl price
        def get_sl_price(positionSide):
            sl_price = 0
            sl_value = get_sl_value(positionSide=positionSide)
            if positionSide == 'LONG':
                sl_price = position_entry_price - sl_value
            if positionSide == 'SHORT':
                sl_price = position_entry_price + sl_value
            return sl_price
        
        # get tp price
        def get_tp_price(positionSide):
            tp_price = 0
            sl_value = get_sl_value(positionSide=positionSide)
            if positionSide == 'LONG':
                tp_price = position_entry_price + (sl_value * tp_sl_ratio)
            if positionSide == 'SHORT':
                tp_price = position_entry_price - (sl_value * tp_sl_ratio)
            return tp_price

        # get long position and put tp sl
        if position_side == 'LONG' and position_amount != 0:
            in_long_position = True
            print(f"\n=> {position_side} position is running since {position_running_time}")
            print(f"=> {position_symbol} | {position_leverage}x | {position_side} | {position_amount_usdt} USDT | Entry: {position_entry_price} | Mark: {round(position_mark_price, 2)} | Liquidation: {position_liquidation_price} | PNL: {position_pnl} USDT")

            # get open orders
            open_orders = get_open_orders()

            if len(open_orders) == 0:
                tp_sl_put_for_long = False

            if len(open_orders) > 0:
                for open_order in open_orders:
                    if open_order['info']['symbol'] == position_symbol and open_order['info']['positionSide'] != position_side and open_order['side'] != 'sell':
                        tp_sl_put_for_long = False

            # put tp sl
            if not tp_sl_put_for_long:
                # get tp sl price
                tp_price_for_long = get_tp_price(positionSide=position_side)
                sl_price_for_long = get_sl_price(positionSide=position_side)
                
                take_profit_market_order_for_long = create_take_profit_market_order(positionSide=position_side, amount=position_amount, stopPrice=tp_price_for_long)
                time.sleep(2)
                stop_market_order_for_long = create_stop_market_order(positionSide=position_side, amount= position_amount, stopPrice=sl_price_for_long)
                tp_sl_put_for_long = True
                print(f"\n=> TP & SL done for {position_side} position of {position_symbol}!")
                # print(take_profit_market_order_for_long)
                # print(stop_market_order_for_long)

        # get short position and put tp sl
        if position_side == 'SHORT' and position_amount != 0:
            in_short_position = True
            print(f"\n=> {position_side} position is running since {position_running_time}")
            print(f"=> {position_symbol} | {position_leverage}x | {position_side} | {position_amount_usdt} USDT | Entry: {position_entry_price} | Mark: {round(position_mark_price, 2)} | Liquidation: {position_liquidation_price} | PNL: {position_pnl} USDT")

            # get open orders
            open_orders = get_open_orders()

            if len(open_orders) == 0:
                tp_sl_put_for_short = False

            if len(open_orders) > 0:
                for open_order in open_orders:
                    if open_order['info']['symbol'] == position_symbol and open_order['info']['positionSide'] != position_side and open_order['side'] != 'buy':
                        tp_sl_put_for_short = False

            # put tp sl
            if not tp_sl_put_for_short:
                # get tp sl price
                tp_price_for_short = get_tp_price(positionSide=position_side)
                sl_price_for_short = get_sl_price(positionSide=position_side)
                
                take_profit_market_order_for_short = create_take_profit_market_order(positionSide=position_side, amount=position_amount, stopPrice=tp_price_for_short)
                time.sleep(2)
                stop_market_order_for_short = create_stop_market_order(positionSide=position_side, amount= position_amount, stopPrice=sl_price_for_short)
                tp_sl_put_for_short = True
                print(f"\n=> TP & SL done for {position_side} position of {position_symbol}!")
                # print(take_profit_market_order_for_short)
                # print(stop_market_order_for_short)

    if not in_long_position and not in_short_position:
        print("\nThere is no LONG or SHORT position!")

    # get account balance
    account_balance = get_balance()
    print(f"\n=> Last price of {symbol} = {last_price} | Future Account Balance = {account_balance} USDT\n")

    # entry condition
    long_entry_condition = df['in_uptrend'][previous_row_index] and df['bull_candle'][previous_row_index] == True and df['low'][previous_row_index] <= df['ema1'][previous_row_index]
    short_entry_condition = not df['in_uptrend'][previous_row_index] and df['bull_candle'][previous_row_index] == False and df['high'][previous_row_index] >= df['ema1'][previous_row_index]

    # long position
    if not in_long_position:
        if df['in_uptrend'][previous_row_index]:
            print("=> [1-3] Market is in UP-TREND and waiting for BULL Candle..........")

            if long_entry_condition:
                print(f"=> [2-3] BULL Candle is occured and LONG entry conditiion confirmed at {df['timestamp'][previous_row_index]}..........")

                if account_balance > 1:
                    buy_order = exchange.create_market_buy_order(symbol=symbol, amount=amount, params={'positionSide': 'LONG'})
                    in_long_position = True
                    tp_sl_put_for_long = False
                    print(f"=> [3-3] Market BUY ordered {buy_order['info']['symbol']} | {float(buy_order['amount']) * float(buy_order['price'])} USDT at {buy_order['price']}")
                    # print(buy_order)
                else:
                    print("=> Not enough balance for LONG position!")

                    
    # short position
    if not in_short_position:
        if not df['in_uptrend'][previous_row_index]:
            print("=> [1-3] Market is in DOWN-TREND and waiting for BEAR Candle..........")

            if short_entry_condition:
                print(f"=> [2-3] BEAR Candle is occured and SHORT entry conditiion confirmed at {df['timestamp'][previous_row_index]}..........")

                if account_balance > 1:
                    sell_order = exchange.create_market_sell_order(symbol=symbol, amount=amount, params={'positionSide': 'SHORT'})
                    in_short_position = True
                    tp_sl_put_for_short = False
                    print(f"=> [3-3] Market SELL ordered {sell_order['info']['symbol']} | {abs(float(sell_order['amount'])  * float(sell_order['price']))} USDT at {sell_order['price']}")
                    # print(sell_order)
                else:
                    print("=> Not enough balance for SHORT position!")

    # in position
    if in_long_position and in_short_position:
        print("=> Both LONG and SHORT positions are already running.")
# end check buy sell orders

bot_start_run_time = get_bot_start_run_time()

def run_bot():
    try:
        print("\n\n#######################################################################################################################")
        print(f"\t\t{name} Trading Bot is running {symbol} | {timeframe} | {leverage}x | Since {bot_start_run_time}")
        print("#######################################################################################################################")
        print(f"Fetching new bars for {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        bars = exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=100)
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC')
        
        # Convert to Myanmar timezone (UTC +6:30)
        myanmar_timezone = pytz.timezone('Asia/Yangon')
        df['timestamp'] = df['timestamp'].dt.tz_convert(myanmar_timezone)       

        # change leverage
        if not adjusted_leverage:
            adjust_leverage()
            time.sleep(1)
        
        get_data_frame(df)

        # call all functions
        check_buy_sell_orders(df)
        
    except Exception as e:
        print(f"An error occurred: {e}")

schedule.every(5).seconds.do(run_bot)

while bot_status:
    schedule.run_pending()
    time.sleep(1)