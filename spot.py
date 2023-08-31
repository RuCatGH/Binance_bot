import os
import time
import threading
import tkinter as tk

from binance.client import Client
from binance.enums import *
from binance.exceptions import *
from dotenv import load_dotenv
from art import tprint

load_dotenv()

api_key = os.getenv('API_KEY_BINANCE')
api_secret = os.getenv('API_SECRET_BINANCE')

if os.getenv('TESTNET').lower() == 'true':
    tesntet = True
elif os.getenv('TESTNET').lower() == 'false':
    tesntet = False

client = Client(api_key, api_secret, testnet=tesntet)

# Получение текущей цены
def get_current_price(symbol) -> float:
    prices = client.get_symbol_ticker(symbol=symbol)
    return float(prices['price'])

def check_fee(symbol) -> float:
    fee = client.get_trade_fee(symbol=symbol)
    return float(fee[0]['makerCommission'])

# Размещение ордера
def place_order(side, lot, current_price, symbol, take_profit, stop_loss, orders, trailing_stop, trail_distance_percent, trailing_limit) -> None:
    take_profit_price = current_price - take_profit if side == 'SHORT' else current_price + take_profit
    stop_loss_price = current_price - stop_loss if side == 'LONG' else current_price + stop_loss

    trailing_limit = round(current_price*(1+(trailing_limit/100)), 2) if side == 'LONG' else round(current_price/(1+(trailing_limit/100)), 2)
    price = trailing_limit if trailing_stop else take_profit_price

    quantity_oco_long = round(lot/stop_loss_price, 5)
    quantity_oco_short = round(lot/take_profit_price, 5)
    order = None
    print(take_profit_price, stop_loss_price, current_price, side)
    try:
        if side == 'LONG':
            order = client.order_oco_sell(
                symbol=symbol,
                quantity=quantity_oco_long,
                price= price, 
                stopPrice=stop_loss_price + 5,
                stopLimitPrice=stop_loss_price,
                trailingDelta = round(trail_distance_percent*100) if trailing_stop else None,
                stopLimitTimeInForce='GTC'
            )
        else:
            order = client.order_oco_buy(
                symbol=symbol,
                quantity=quantity_oco_short,
                price=price,
                stopPrice=stop_loss_price - 5,
                stopLimitPrice=stop_loss_price,
                trailingDelta = round(trail_distance_percent*100) if trailing_stop else None,
                stopLimitTimeInForce='GTC'
            )
    except BinanceAPIException as e:
        if 'Filter failure: NOTIONAL' == e.message:
            print(side,"Ошибка: Увеличьте LOT")
        elif 'Filter failure: MAX_NUM_ALGO_ORDERS' == e.message:
            print('Превышено максимальное количество ордеров')
        elif 'The relationship of the prices for the orders is not correct' in e.message:
            current_price = get_current_price(symbol)

            take_profit_price = current_price - take_profit if side == 'SHORT' else current_price + take_profit
            stop_loss_price = current_price - stop_loss if side == 'LONG' else current_price + stop_loss

            trailing_limit = round(current_price*(1+(trailing_limit/100)), 2) if side == 'LONG' else round(current_price/(1+(trailing_limit/100)), 2)
            price = trailing_limit if trailing_stop else take_profit_price

            quantity_oco = round(lot/current_price, 6)
            if side == 'LONG':
                order = client.order_oco_sell(
                    symbol=symbol,
                    quantity=quantity_oco,
                    price= price, 
                    stopPrice=stop_loss_price + 5,
                    stopLimitPrice=stop_loss_price,
                    trailingDelta = round(trail_distance_percent*100) if trailing_stop else None,
                    stopLimitTimeInForce='GTC'
                )
            else:
                order = client.order_oco_buy(
                    symbol=symbol,
                    quantity=quantity_oco,
                    price=price,
                    stopPrice=stop_loss_price - 5,
                    stopLimitPrice=stop_loss_price,
                    trailingDelta = round(trail_distance_percent*100) if trailing_stop else None,
                    stopLimitTimeInForce='GTC'
                )
        else:   
            print("Произошла ошибка при размещении ордера:", e)
    if order:
        print(side, 'Take', price, 'Stop', stop_loss_price, 'ID ордеров', [order['orderId'] for order in order['orderReports']])
    orders.append([order, side])

# Закрытие ордеров
def close_orders() -> None:
    try:
        open_orders = client.get_open_oco_orders()
        for order in open_orders:
            if order['symbol'] == symbol_entry.get():
                result = client.cancel_order(symbol=symbol_entry.get(), orderId=order['orders'][0]['orderId'])
                print(f"Ордер отменён", result['orderListId'])
    except Exception as ex:
        print('Ошибка при закрытии ордеров:', ex)

def get_spread(symbol) -> float:
    depth = client.get_order_book(symbol=symbol)

    # Получите цену предложения и цену спроса из стакана
    bid_price = float(depth['bids'][0][0])
    ask_price = float(depth['asks'][0][0])

    # Расчет спреда
    spread = ask_price - bid_price
    return spread
# Основная логика скрипта
def main(initial_lot, lot, take, loss, trailing_stop, trailing_limit, trail_distance_percent, martingale, lot_increment, symbol, start_balance, balance_currency, pnl_without) -> None:
    current_price = get_current_price(symbol)

    orders = []

    # Открытие позиций
    long_thread = threading.Thread(target=place_order, args=('LONG',lot, current_price, symbol, take, loss, orders, trailing_stop, trail_distance_percent, trailing_limit))
    long_thread.start()

    short_thread = threading.Thread(target=place_order, args=('SHORT',lot, current_price, symbol, take, loss, orders, trailing_stop, trail_distance_percent, trailing_limit))
    short_thread.start()


    # Ждем завершения потоков
    long_thread.join()
    short_thread.join()
    
    # Проверка на ошибки
    for order in orders:
        if None in order:
            print('Ошибка при размещении ордера')
            close_orders()
            return 
        elif not orders:
            print('Ошибка при размещении ордера')
            close_orders()
            return

    short = None
    long = None

    # Проверка на завершение ордеров
    while True:
        for order in orders:
            if order[-1] == 'LONG' and long is None:
                stop_loss_limit = client.get_order(symbol=symbol, orderId=order[0]['orderReports'][0]['orderId'])
                limit_maker = client.get_order(symbol=symbol, orderId=order[0]['orderReports'][1]['orderId'])
                if stop_loss_limit['status'] == 'FILLED' or stop_loss_limit['status'] == 'CANCELED':
                    pnl_long = (float(order[0]['orderReports'][0]['price'])-current_price)*float(order[0]['orderReports'][0]['origQty'])
                    print(order[-1],f"Ордер {stop_loss_limit['orderId']} закрыт по стоп-лимиту. Убыток:", pnl_long)
                    long = False
                elif limit_maker['status'] == 'FILLED' or limit_maker['status'] == 'CANCELED':
                    pnl_long = (float(order[0]['orderReports'][1]['price'])-current_price)*float(order[0]['orderReports'][1]['origQty'])
                    print(order[-1],f"Ордер {limit_maker['orderId']} закрыт по тэйку. Прибыль:", pnl_long)
                    long = True
            elif order[-1] == 'SHORT' and short is None:
                stop_loss_limit = client.get_order(symbol=symbol, orderId=order[0]['orderReports'][0]['orderId'])
                limit_maker = client.get_order(symbol=symbol, orderId=order[0]['orderReports'][1]['orderId'])
                if stop_loss_limit['status'] == 'FILLED' or stop_loss_limit['status'] == 'CANCELED':
                    pnl_short = (current_price-float(order[0]['orderReports'][0]['price']))*float(order[0]['orderReports'][0]['origQty'])
                    print(order[-1],f"Ордер {stop_loss_limit['orderId']} закрыт по стоп-лимиту. Убыток:", pnl_short)
                    short = False
                elif limit_maker['status'] == 'FILLED' or limit_maker['status'] == 'CANCELED':
                    pnl_short = (current_price-float(order[0]['orderReports'][1]['price']))*float(order[0]['orderReports'][1]['origQty'])
                    print(order[-1],f"Ордер {limit_maker['orderId']} закрыт по тэйку. Прибыль:", pnl_short)
                    short = True

        if short is not None and long is not None:
            break

        time.sleep(3)
    pnl_without += pnl_short+pnl_long
    print('Ордера закрыты PNL с комиссией:', get_balance(balance_currency) - start_balance)
    print('Ордера закрыты PNL без комиссии:', pnl_short+pnl_long)
    start_balance = round(get_balance(balance_currency), 2)
    print('Текущий баланс', start_balance)
    print('Spread', get_spread(symbol))
    # Закрытие ордеров
    if martingale:
        if not long and not short and limit_maker['status']=='FILLED' and stop_loss_limit['status']=='FILLED':
            lot *= lot_increment
        else:
            lot = initial_lot

    # Повторная торговля
    if not auto_stop_var.get():
        return main(initial_lot, lot, take, loss, trailing_stop, trailing_limit, trail_distance_percent, martingale, lot_increment, symbol, start_balance, balance_currency, pnl_without)
    else:
        return pnl_without
# Получение баланса
def get_balance(asset) -> float:
    balance = client.get_asset_balance(asset=asset)
    return float(balance['free'])

# Функция запуска основного скрипта
def start_trading() -> None:
    # Получение параметров из полей ввода
    try:
        take = int(take_entry.get())
        loss = int(loss_entry.get())

        pnl_without=0

        symbol = symbol_entry.get().strip()
        for symbol_info in client.get_exchange_info()['symbols']:
            if symbol_info['symbol'] == symbol:
                balance_currency = symbol_info['quoteAsset']
                break

        if float(initial_lot_entry.get())/100*get_balance(balance_currency) <10:
            initial_lot = 10
        else:
            initial_lot = float(initial_lot_entry.get())/100*get_balance(balance_currency)

        print('Текущий баланс', get_balance(balance_currency))
        print('Spread', get_spread(symbol))
        print('Текущий LOT $:', initial_lot)
    

        trailing_stop = trailing_stop_var.get()
        trail_distance_percent = float(trail_distance_entry.get())
        martingale = martingale_var.get()
        lot_increment = float(lot_increment_entry.get())
        start_balance = get_balance(balance_currency)
        lot = initial_lot
        trailing_limit = float(trail_limit_entry.get())
        # Запуск основного скрипта с заданными параметрами
        try:
            if not tesntet:
                if check_fee(symbol) >= float(fee_entry_variable.get()):
                    pnl_without = main(initial_lot,lot, take, loss, trailing_stop, trailing_limit, trail_distance_percent, martingale, lot_increment, symbol, start_balance, balance_currency, pnl_without)
                else:
                    print('Коммиссия превышена')
            else:
                pnl_without = main(initial_lot,lot, take, loss, trailing_stop, trailing_limit, trail_distance_percent, martingale, lot_increment, symbol, start_balance, balance_currency, pnl_without)
            print('Общий профит: ', get_balance(balance_currency)-start_balance)
            print('Общий профит %: ', (get_balance(balance_currency)-start_balance)/start_balance*100)
            print('Доход без коммисии', pnl_without)
        except Exception as e:
            print(e)

    except Exception as ex:
        print('Ошибка при старте трэйдинга:', ex)
    

# Запуск через поток
def start_trading_thread() -> None:
    trading_thread = threading.Thread(target=start_trading)
    trading_thread.start()

if __name__ == "__main__":
    tprint('Binance   bot   started')
    # Создание главного окна

    window = tk.Tk()
    window.title("Binance Trading Script")
    window.geometry("400x500")

    # Создание полей ввода и меток для параметров
    initial_lot_label = tk.Label(window, text="Начальное значение LOT:")
    initial_lot_label.pack()
    initial_lot_entry = tk.Entry(window)
    initial_lot_entry.pack()

    take_label = tk.Label(window, text="Тейк профит (пункты):")
    take_label.pack()
    take_entry = tk.Entry(window)
    take_entry.pack()

    loss_label = tk.Label(window, text="Стоп-лосс (пункты):")
    loss_label.pack()
    loss_entry = tk.Entry(window)
    loss_entry.pack()

    fee_label = tk.Label(window, text="Коммисия:")
    fee_label.pack()
    fee_entry_variable = tk.IntVar(value=0)
    fee_entry = tk.Entry(window, textvariable=fee_entry_variable)
    fee_entry.pack()

    trailing_stop_var = tk.BooleanVar()
    trailing_stop_checkbox = tk.Checkbutton(window, text="Активировать трейлинг стоп", variable=trailing_stop_var)
    trailing_stop_checkbox.pack()

    trail_distance_label = tk.Label(window, text="Расстояние трейл-стопа (%):")
    trail_distance_label.pack()
    trail_distance_entry_variable = tk.IntVar(value=0)
    trail_distance_entry = tk.Entry(window, textvariable=trail_distance_entry_variable)
    trail_distance_entry.pack()

    trail_limit_label = tk.Label(window, text="Стоп лимит трейл-стопа (%):")
    trail_limit_label.pack()
    trail_limit_entry_variable = tk.IntVar(value=0)
    trail_limit_entry = tk.Entry(window, textvariable=trail_limit_entry_variable)
    trail_limit_entry.pack()

    martingale_var = tk.BooleanVar()
    martingale_checkbox = tk.Checkbutton(window, text="Включить Мартингейл", variable=martingale_var)
    martingale_checkbox.pack()

    lot_increment_label = tk.Label(window, text="Множитель лота:")
    lot_increment_label.pack()
    lot_increment_variable = tk.IntVar(value=0)
    lot_increment_entry = tk.Entry(window, textvariable=lot_increment_variable)
    lot_increment_entry.pack()

    symbol_entry_variable = tk.StringVar(value='BTCUSDT')
    symbol_label = tk.Label(window, text="Пара торговли:")
    symbol_label.pack()
    symbol_entry = tk.Entry(window, textvariable=symbol_entry_variable)
    symbol_entry.pack()

    auto_stop_var = tk.BooleanVar()
    auto_stop_checkbox = tk.Checkbutton(window, text="Автоматическая остановка", variable=auto_stop_var)
    auto_stop_checkbox.pack()

    start_button = tk.Button(window, text="Старт", command=start_trading_thread)
    start_button.pack()

    stop_button = tk.Button(window, text="Остановить все ордера", command=close_orders)
    stop_button.pack()


    # Запуск главного цикла обработки событий
    window.mainloop()
