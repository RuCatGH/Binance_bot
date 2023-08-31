import os
import time
import threading
import tkinter as tk

from binance.client import Client
from binance.enums import *
from binance.exceptions import *
from dotenv import load_dotenv


load_dotenv()

api_key = os.getenv('API_KEY_BINANCE_FUTURE')
api_secret = os.getenv('API_SECRET_BINANCE_FUTURE')
testnet = bool(os.getenv('TESTNET'))

client = Client(api_key, api_secret, testnet=testnet)

# Получение текущей цены фьючерса
def get_current_price(symbol) -> float:
    prices = client.futures_mark_price(symbol=symbol)
    return float(prices['markPrice'])

# Проверка комиссии
def check_fee(symbol) -> float:
    fee = client.futures_trade_fee(symbol=symbol)
    return float(fee['tradeFee'][0]['maker'])

# Размещение ордера на фьючерсы
def place_order(side, lot, symbol, take_profit, stop_loss, orders, trailing_stop, trail_distance_percent, trailing_limit, start_price) -> None:

    quantity = round(lot / start_price, 3)
    
    order = None
    stop = None
    take = None
    
    try:
        if side == 'LONG':
            order = client.futures_create_order(
            symbol=symbol,
            side='BUY',
            type=FUTURE_ORDER_TYPE_LIMIT,
            quantity=quantity,
            positionSide=side,
            timeInForce=TIME_IN_FORCE_GTC,
            price = start_price
            )
        else:
            order = client.futures_create_order(
                symbol=symbol,
                side='SELL',
                type=FUTURE_ORDER_TYPE_LIMIT,
                quantity=quantity,
                positionSide=side,
                timeInForce=TIME_IN_FORCE_GTC,
                price = start_price
            )
    except BinanceAPIException as e:
        if 'Quantity less than or equal to zero.' == e.message:
            print(side, "Ошибка: Увеличьте LOT")
        elif 'Filter failure: MAX_NUM_ALGO_ORDERS' == e.message:
            print('Превышено максимальное количество ордеров')
        else:
            print("Произошла ошибка при размещении ордера:", e)


    take_profit_price = round(start_price - take_profit if side == 'SHORT' else start_price + take_profit, 2)
    stop_loss_price = round(start_price - stop_loss if side == 'LONG' else start_price + stop_loss, 2)

    try:
        if trailing_stop:
            take = client.futures_create_order(
                symbol=symbol,
                side='BUY' if side == 'SHORT' else 'SELL',
                type='TRAILING_STOP_MARKET',
                positionSide=side,
                quantity=quantity,
                activationprice=round(start_price*(1+(trailing_limit/100)), 2) if side == 'LONG' else round(start_price/(1+(trailing_limit/100)), 2),
                callbackRate=trail_distance_percent,
            )
        else:
            take = client.futures_create_order(
            symbol=symbol,
            side='BUY' if side == 'SHORT' else 'SELL',
            type=FUTURE_ORDER_TYPE_TAKE_PROFIT_MARKET,
            positionSide=side,  
            stopPrice=take_profit_price,
            closePosition=True,
                )   
    except BinanceAPIException as e:
        if 'Order would immediately trigger.' == e.message:
            print(side, "Увеличьте Take")
        elif 'Invalid callBack rate.' == e.message:
            print('Выставлен неправильный callBack rate')
        else:
            print(e)
    try:
        stop = client.futures_create_order(
            symbol=symbol,
            side='BUY' if side == 'SHORT' else 'SELL',
            type=FUTURE_ORDER_TYPE_STOP_MARKET,
            positionSide=side,
            stopPrice=stop_loss_price,
            closePosition=True,
        )
    except BinanceAPIException as e:
        if 'Order would immediately trigger.' == e.message:
            print(side, "Увеличьте Stop")
        else:
            print(e)
    if order and take and stop:
        print(side, 'Take', take_profit_price, 'Stop', stop_loss_price, 'ID ордера', order['orderId'])
    orders.append([order, take, stop, side])

# Закрытие ордеров на фьючерсы
def close_orders():
    try:
        close_orders = client.futures_cancel_all_open_orders(symbol=symbol_entry.get())
        print(f'Ордера отменены')
    except Exception as ex:
        print('Ошибка при закрытии ордеров:', ex)

def get_spread(symbol):
    depth = client.futures_order_book(symbol=symbol, limit=100)

    # Получите цену предложения и цену спроса из стакана
    bid_price = float(depth['bids'][0][0])
    ask_price = float(depth['asks'][0][0])

    # Расчет спреда
    spread = ask_price - bid_price
    return spread

# Основная логика скрипта
def main(initial_lot, lot, take, loss, trailing_stop, trailing_limit, trail_distance_percent, martingale, lot_increment, symbol, start_balance, balance_currency) -> None:
    orders = []
    start_price = round(get_current_price(symbol))
    # Открытие позиций
    long_thread = threading.Thread(target=place_order, args=('LONG', lot, symbol, take, loss, orders, trailing_stop, trail_distance_percent, trailing_limit, start_price))
    short_thread = threading.Thread(target=place_order, args=('SHORT', lot, symbol, take, loss, orders, trailing_stop, trail_distance_percent, trailing_limit, start_price))

    long_thread.start()
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
                stop_loss = client.futures_get_order(symbol=symbol, orderId=order[2]['orderId'])
                take_order = client.futures_get_order(symbol=symbol, orderId=order[1]['orderId'])
                if stop_loss['status'] == 'FILLED' or stop_loss['status'] == 'CANCELED':
                    client.futures_cancel_order(symbol=symbol, orderId=order[1]['orderId'])
                    long = False
                elif take_order['status'] == 'FILLED' or  take_order['status'] == 'CANCELED':
                    client.futures_cancel_order(symbol=symbol, orderId=order[2]['orderId'])
                    long = True
            elif order[-1] == 'SHORT' and short is None:
                stop_loss = client.futures_get_order(symbol=symbol, orderId=order[2]['orderId'])
                take_order = client.futures_get_order(symbol=symbol, orderId=order[1]['orderId'])
                if stop_loss['status'] == 'FILLED' or stop_loss['status'] == 'CANCELED':
                    client.futures_cancel_order(symbol=symbol, orderId=order[1]['orderId'])
                    short = False
                elif take_order['status'] == 'FILLED' or stop_loss['status'] == 'CANCELED':
                    client.futures_cancel_order(symbol=symbol, orderId=order[2]['orderId'])
                    long = True

        if long is not None and short is not None:
            break

        time.sleep(3)
    print('Ордера закрыты PNL:', get_balance(balance_currency) - start_balance)
    start_balance = round(get_balance(balance_currency), 2)
    print('Текущий баланс', start_balance)
    print('Spread', get_spread(symbol))


    if martingale:
        if not long and not short and stop_loss['status'] != 'CANCELED':
            lot *= lot_increment
        else:
            lot = initial_lot

    # Повторная торговля
    if not auto_stop_var.get():
        main(initial_lot, lot, take, loss, trailing_stop, trailing_limit, trail_distance_percent, martingale, lot_increment, symbol, start_balance, balance_currency)

# Получение баланса с фьючерсов
def get_balance(asset):
    balance = client.futures_account_balance()
    for entry in balance:
        if entry['asset'] == asset:
            return float(entry['balance'])
    return 0.0

# Функция запуска основного скрипта
def start_trading() -> None:
    # Получение параметров из полей ввода
    try:
        symbol = symbol_entry.get()
        for symbol_info in client.futures_exchange_info()['symbols']:
            if symbol_info['symbol'] == symbol:
                balance_currency = symbol_info['quoteAsset']
                break

        if float(initial_lot_entry.get()) / 100 * get_balance(balance_currency) < 10:
            initial_lot = 10
        else:
            initial_lot = float(initial_lot_entry.get()) / 100 * get_balance(balance_currency)

        print('Текущий баланс', get_balance(balance_currency))
        print('Spread', get_spread(symbol))
        print('Текущий LOT $:', initial_lot)

        take = int(take_entry.get())
        loss = int(loss_entry.get())

        trailing_stop = trailing_stop_var.get()
        trail_distance_percent = float(trail_distance_entry.get())
        martingale = martingale_var.get()
        lot_increment = float(lot_increment_entry.get())
        start_balance = get_balance(balance_currency)
        lot = initial_lot
        trailing_limit = float(trail_limit_entry.get())

        # Запуск основного скрипта с заданными параметрами
        if not testnet:
            if check_fee(symbol) >= fee_entry_variable.get():
                main(initial_lot, lot, take, loss, trailing_stop, trailing_limit, trail_distance_percent, martingale, lot_increment, symbol, start_balance, balance_currency)
        else:
            main(initial_lot, lot, take, loss, trailing_stop, trailing_limit, trail_distance_percent, martingale, lot_increment, symbol, start_balance, balance_currency)
        print('Общий профит: ', get_balance(balance_currency)-start_balance)
        print('Общий профит %: ', (get_balance(balance_currency) - start_balance) / start_balance * 100)
    except Exception as ex:
        print('Ошибка при старте трейдинга:', ex)

# Запуск через поток
def start_trading_thread():
    trading_thread = threading.Thread(target=start_trading)
    trading_thread.start()

if __name__ == "__main__":
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
