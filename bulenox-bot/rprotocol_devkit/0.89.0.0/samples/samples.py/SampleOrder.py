#!/usr/bin/env python3

#   ===========================================================================
#
#   Copyright (c) 2025 by Omnesys Technologies, Inc.  All rights reserved.
#
#   Warning :
#       This Software Product is protected by copyright law and international
#       treaties.  Unauthorized use, reproduction or distribution of this
#       Software Product (including its documentation), or any portion of it,
#       may result in severe civil and criminal penalties, and will be
#       prosecuted to the maximum extent possible under the law.
#
#       Omnesys Technologies, Inc. will compensate individuals providing
#       admissible evidence of any unauthorized use, reproduction, distribution
#       or redistribution of this Software Product by any person, company or 
#       organization.
#
#   This Software Product is licensed strictly in accordance with a separate
#   Software System License Agreement, granted by Omnesys Technologies, Inc.,
#   which contains restrictions on use, reverse engineering, disclosure,
#   confidentiality and other matters.
#
#   ===========================================================================

#   ===========================================================================
#
#   SampleOrder.py
#   ==============
#   This sample program is intended to provide a simple, but working, python3
#   example of how one might use R | Protocol API to place an order.
#   It makes use of the websockets library, which is built over the asyncio
#   library.
#
#   - This program can be run with no arguments to display usage information.
#
#   - To list the available Rithmic systems, pass in a single argument
#     specifying the URI of the server.
#
#   - To log in to a specific system and place an order, a number of
#     additional parameters are necessary, specifying the system, login
#     credentials, instrument and side.
#
#   RHEL 8/CentOS 8 version info :
#   - The version of python3 used is 3.6.8.
#   - The version of the websockets lib used is 8.1.
#   - The version of the protobuf lib used is 3.12.2.
#
#   ===========================================================================

#   ===========================================================================
#   Below are library dependencies
#   - One might need to install them using commands such as :
#
#          sudo pip3 install websockets
#          sudo pip3 install protobuf
#
#               -- or --
#          pip3 install --user websockets
#          pip3 install --user protobuf

import asyncio
import google.protobuf.message
import pathlib
import ssl
import sys
import websockets

#   ===========================================================================
#   Below are references to the compiled .proto files
#   - To compile a .proto file, one has to have a protobuf compiler.  The
#     compiler used to generate the included python files was built from the
#     protobuf 2.6.1 source.
#   - The example command below generates a .py file where the input .proto
#     file is in the current working directory and the output file is written
#     to the same :
#
#          protoc --python_out=. request_rithmic_system_info.proto
#
#   - Note : Each Rithmic protobuf message has a template_id, serving as a
#     message type.  The base_pb2 class is a convenience class to get the
#     template_id so that the message can be passed to an appropriate handler
#     routine.

import base_pb2

import request_account_list_pb2
import response_account_list_pb2

import request_heartbeat_pb2
import response_heartbeat_pb2

import request_rithmic_system_info_pb2
import response_rithmic_system_info_pb2

import request_login_pb2
import response_login_pb2

import request_login_info_pb2
import response_login_info_pb2

import request_logout_pb2
import response_logout_pb2

import request_market_data_update_pb2
import response_market_data_update_pb2

import request_trade_routes_pb2
import response_trade_routes_pb2

import request_subscribe_for_order_updates_pb2
import response_subscribe_for_order_updates_pb2

import request_new_order_pb2
import response_new_order_pb2

import exchange_order_notification_pb2
import rithmic_order_notification_pb2

#   ===========================================================================

USAGE   = "SampleOrder.py connect_point [system_name user_id password exchange symbol side(B/S)]"
USAGE_2 = "  (try wss://rituz00100.rithmic.com:443 for the connect_point)"

#   ===========================================================================
#   some global variables to gather info needed to place the order

g_rcvd_account = False
g_fcm_id = ""
g_ib_id = ""
g_account_id = ""

g_symbol = ""
g_exchange = ""

g_rcvd_trade_route = False
g_trade_route = ""

g_side = ""

g_order_is_complete = False;

#   ===========================================================================
#   This routine interprets the msg_buf as a RithmicOrderNotification.

async def rithmic_order_notification_cb(msg_buf):
    # rithmic_order_notification : 351
    global g_order_is_complete
    
    msg = rithmic_order_notification_pb2.RithmicOrderNotification()
    msg.ParseFromString(msg_buf[0:])

    notify_type_to_string = {rithmic_order_notification_pb2.RithmicOrderNotification.ORDER_RCVD_FROM_CLNT     : "ORDER_RCVD_FROM_CLNT",
                             rithmic_order_notification_pb2.RithmicOrderNotification.MODIFY_RCVD_FROM_CLNT    : "MODIFY_RCVD_FROM_CLNT",
                             rithmic_order_notification_pb2.RithmicOrderNotification.CANCEL_RCVD_FROM_CLNT    : "CANCEL_RCVD_FROM_CLNT",
                             rithmic_order_notification_pb2.RithmicOrderNotification.OPEN_PENDING             : "OPEN_PENDING",
                             rithmic_order_notification_pb2.RithmicOrderNotification.MODIFY_PENDING           : "MODIFY_PENDING",
                             rithmic_order_notification_pb2.RithmicOrderNotification.CANCEL_PENDING           : "CANCEL_PENDING",
                             rithmic_order_notification_pb2.RithmicOrderNotification.ORDER_RCVD_BY_EXCH_GTWY  : "ORDER_RCVD_BY_EXCH_GTWY",
                             rithmic_order_notification_pb2.RithmicOrderNotification.MODIFY_RCVD_BY_EXCH_GTWY : "MODIFY_RCVD_BY_EXCH_GTWY",
                             rithmic_order_notification_pb2.RithmicOrderNotification.CANCEL_RCVD_BY_EXCH_GTWY : "CANCEL_RCVD_BY_EXCH_GTWY",
                             rithmic_order_notification_pb2.RithmicOrderNotification.ORDER_SENT_TO_EXCH       : "ORDER_SENT_TO_EXCH",
                             rithmic_order_notification_pb2.RithmicOrderNotification.MODIFY_SENT_TO_EXCH      : "MODIFY_SENT_TO_EXCH",
                             rithmic_order_notification_pb2.RithmicOrderNotification.CANCEL_SENT_TO_EXCH      : "CANCEL_SENT_TO_EXCH",
                             rithmic_order_notification_pb2.RithmicOrderNotification.OPEN                     : "OPEN",
                             rithmic_order_notification_pb2.RithmicOrderNotification.MODIFIED                 : "MODIFIED",
                             rithmic_order_notification_pb2.RithmicOrderNotification.COMPLETE                 : "COMPLETE",
                             rithmic_order_notification_pb2.RithmicOrderNotification.MODIFICATION_FAILED      : "MODIFICATION_FAILED",
                             rithmic_order_notification_pb2.RithmicOrderNotification.CANCELLATION_FAILED      : "CANCELLATION_FAILED",
                             rithmic_order_notification_pb2.RithmicOrderNotification.TRIGGER_PENDING          : "TRIGGER_PENDING",
                             rithmic_order_notification_pb2.RithmicOrderNotification.GENERIC                  : "GENERIC",
                             rithmic_order_notification_pb2.RithmicOrderNotification.LINK_ORDERS_FAILED       : "LINK_ORDERS_FAILED"}

    transaction_type_to_string = {rithmic_order_notification_pb2.RithmicOrderNotification.TransactionType.BUY  : "BUY",
                                  rithmic_order_notification_pb2.RithmicOrderNotification.TransactionType.SELL : "SELL"}

    duration_to_string = {rithmic_order_notification_pb2.RithmicOrderNotification.Duration.DAY : "DAY",
                          rithmic_order_notification_pb2.RithmicOrderNotification.Duration.GTC : "GTC",
                          rithmic_order_notification_pb2.RithmicOrderNotification.Duration.IOC : "IOC",
                          rithmic_order_notification_pb2.RithmicOrderNotification.Duration.FOK : "FOK"}

    price_type_to_string = {rithmic_order_notification_pb2.RithmicOrderNotification.PriceType.LIMIT       : "LIMIT",
                            rithmic_order_notification_pb2.RithmicOrderNotification.PriceType.MARKET      : "MARKET",
                            rithmic_order_notification_pb2.RithmicOrderNotification.PriceType.STOP_LIMIT  : "STOP_LIMIT",
                            rithmic_order_notification_pb2.RithmicOrderNotification.PriceType.STOP_MARKET : "STOP_MARKET"}

    order_placement_to_string = {rithmic_order_notification_pb2.RithmicOrderNotification.OrderPlacement.MANUAL : "LIMIT",
                                 rithmic_order_notification_pb2.RithmicOrderNotification.OrderPlacement.AUTO   : "AUTO"}

    print(f"")
    print(f" RithmicOrderNotification : ")
    print(f"              template_id : {msg.template_id}")
    print(f"              notify_type : {notify_type_to_string[msg.notify_type]} ({msg.notify_type})")
    print(f"              is_snapshot : {msg.is_snapshot}")

    print(f"                   status : {msg.status}")
    print(f"                basket_id : {msg.basket_id}")
    print(f"       original_basket_id : {msg.original_basket_id}")

    print(f"                   fcm_id : {msg.fcm_id}")
    print(f"                    ib_id : {msg.ib_id}")
    print(f"               account_id : {msg.account_id}")
    print(f"                  user_id : {msg.user_id}")

    print(f"                   symbol : {msg.symbol}")
    print(f"                 exchange : {msg.exchange}")
    print(f"           trade_exchange : {msg.trade_exchange}")
    print(f"              trade_route : {msg.trade_route}")
    print(f"        exchange_order_id : {msg.exchange_order_id}")
    print(f"          instrument_type : {msg.instrument_type}")
    
    print(f"                 quantity : {msg.quantity}")
    print(f"                    price : {msg.price}")
    print(f"            trigger_price : {msg.trigger_price}")
    
    print(f"         transaction_type : {transaction_type_to_string[msg.transaction_type]} ({msg.transaction_type})")
    print(f"                duration  : {duration_to_string[msg.duration]} ({msg.duration})")
    print(f"               price_type : {price_type_to_string[msg.price_type]} ({msg.price_type})")
    print(f"          orig_price_type : {price_type_to_string[msg.orig_price_type]} ({msg.orig_price_type})")
    print(f"           manual_or_auto : {order_placement_to_string[msg.manual_or_auto]} ({msg.manual_or_auto})")

    print(f"         sequence_number  : {msg.sequence_number}")
    print(f"    orig_sequence_number  : {msg.orig_sequence_number}")
    print(f"     cor_sequence_number  : {msg.cor_sequence_number}")

    print(f"                currency  : {msg.currency}")
    print(f"            country_code  : {msg.country_code}")

    print(f"                    text  : {msg.text}")
    print(f"              report_text : {msg.report_text}")
    print(f"                 remarks  : {msg.remarks}")

    print(f"                    ssboe : {msg.ssboe}")
    print(f"                    usecs : {msg.usecs}")
    print(f"")

    if msg.status == "complete" or \
       msg.notify_type == rithmic_order_notification_pb2.RithmicOrderNotification.COMPLETE:
        g_order_is_complete = True
        
#   ===========================================================================
#   This routine interprets the msg_buf as a ExchangeOrderNotification.

async def exchange_order_notification_cb(msg_buf):
    # exchange_order_notification : 352
    msg = exchange_order_notification_pb2.ExchangeOrderNotification()
    msg.ParseFromString(msg_buf[0:])

    notify_type_to_string = {exchange_order_notification_pb2.ExchangeOrderNotification.STATUS        : "STATUS",
                             exchange_order_notification_pb2.ExchangeOrderNotification.MODIFY        : "MODIFY",
                             exchange_order_notification_pb2.ExchangeOrderNotification.CANCEL        : "CANCEL",
                             exchange_order_notification_pb2.ExchangeOrderNotification.TRIGGER       : "TRIGGER",
                             exchange_order_notification_pb2.ExchangeOrderNotification.FILL          : "FILL",
                             exchange_order_notification_pb2.ExchangeOrderNotification.REJECT        : "REJECT",
                             exchange_order_notification_pb2.ExchangeOrderNotification.NOT_MODIFIED  : "NOT_MODIFIED",
                             exchange_order_notification_pb2.ExchangeOrderNotification.NOT_CANCELLED : "NOT_CANCELLED",
                             exchange_order_notification_pb2.ExchangeOrderNotification.GENERIC       : "GENERIC"}

    transaction_type_to_string = {exchange_order_notification_pb2.ExchangeOrderNotification.TransactionType.BUY  : "BUY",
                                  exchange_order_notification_pb2.ExchangeOrderNotification.TransactionType.SELL : "SELL"}

    duration_to_string = {exchange_order_notification_pb2.ExchangeOrderNotification.Duration.DAY : "DAY",
                          exchange_order_notification_pb2.ExchangeOrderNotification.Duration.GTC : "GTC",
                          exchange_order_notification_pb2.ExchangeOrderNotification.Duration.IOC : "IOC",
                          exchange_order_notification_pb2.ExchangeOrderNotification.Duration.FOK : "FOK"}

    price_type_to_string = {exchange_order_notification_pb2.ExchangeOrderNotification.PriceType.LIMIT       : "LIMIT",
                            exchange_order_notification_pb2.ExchangeOrderNotification.PriceType.MARKET      : "MARKET",
                            exchange_order_notification_pb2.ExchangeOrderNotification.PriceType.STOP_LIMIT  : "STOP_LIMIT",
                            exchange_order_notification_pb2.ExchangeOrderNotification.PriceType.STOP_MARKET : "STOP_MARKET"}

    order_placement_to_string = {exchange_order_notification_pb2.ExchangeOrderNotification.OrderPlacement.MANUAL : "LIMIT",
                                 exchange_order_notification_pb2.ExchangeOrderNotification.OrderPlacement.AUTO   : "AUTO"}

    print(f"")
    print(f" ExchangeOrderNotification : ")
    print(f"               template_id : {msg.template_id}")
    print(f"               notify_type : {notify_type_to_string[msg.notify_type]} ({msg.notify_type})")
    print(f"               is_snapshot : {msg.is_snapshot}")

    print(f"              report_type : {msg.report_type}")
    print(f"                   status : {msg.status}")
    print(f"                basket_id : {msg.basket_id}")
    print(f"       original_basket_id : {msg.original_basket_id}")

    print(f"                   fcm_id : {msg.fcm_id}")
    print(f"                    ib_id : {msg.ib_id}")
    print(f"               account_id : {msg.account_id}")
    print(f"                  user_id : {msg.user_id}")

    print(f"                   symbol : {msg.symbol}")
    print(f"                 exchange : {msg.exchange}")
    print(f"           trade_exchange : {msg.trade_exchange}")
    print(f"              trade_route : {msg.trade_route}")
    print(f"        exchange_order_id : {msg.exchange_order_id}")
    print(f"          instrument_type : {msg.instrument_type}")
    
    print(f"                 quantity : {msg.quantity}")
    print(f"                    price : {msg.price}")
    print(f"            trigger_price : {msg.trigger_price}")
    
    print(f"         transaction_type : {transaction_type_to_string[msg.transaction_type]} ({msg.transaction_type})")
    print(f"                duration  : {duration_to_string[msg.duration]} ({msg.duration})")
    print(f"               price_type : {price_type_to_string[msg.price_type]} ({msg.price_type})")
    print(f"          orig_price_type : {price_type_to_string[msg.orig_price_type]} ({msg.orig_price_type})")
    print(f"           manual_or_auto : {order_placement_to_string[msg.manual_or_auto]} ({msg.manual_or_auto})")

    print(f"           confirmed_size : {msg.confirmed_size}")
    print(f"           confirmed_time : {msg.confirmed_time}")
    print(f"           confirmed_date : {msg.confirmed_date}")
    print(f"             confirmed_id : {msg.confirmed_id}")
    
    print(f"            modified_size : {msg.modified_size}")
    print(f"            modified_time : {msg.modified_time}")
    print(f"            modified_date : {msg.modified_date}")
    print(f"               modify_id  : {msg.modify_id}")

    print(f"           cancelled_size : {msg.cancelled_size}")
    print(f"           cancelled_time : {msg.cancelled_time}")
    print(f"           cancelled_date : {msg.cancelled_date}")
    print(f"            cancelled_id  : {msg.cancelled_id}")

    print(f"               fill_price : {msg.fill_price}")
    print(f"                fill_size : {msg.fill_size}")
    print(f"                fill_time : {msg.fill_time}")
    print(f"                fill_date : {msg.fill_date}")
    print(f"                 fill_id  : {msg.fill_id}")

    print(f"              trigger_id  : {msg.trigger_id}")

    print(f"         sequence_number  : {msg.sequence_number}")
    print(f"    orig_sequence_number  : {msg.orig_sequence_number}")
    print(f"     cor_sequence_number  : {msg.cor_sequence_number}")

    print(f"                currency  : {msg.currency}")
    print(f"            country_code  : {msg.country_code}")

    print(f"                    text  : {msg.text}")
    print(f"              report_text : {msg.report_text}")
    print(f"                 remarks  : {msg.remarks}")

    print(f"                    ssboe : {msg.ssboe}")
    print(f"                    usecs : {msg.usecs}")

    print(f"       exch_receipt_ssboe : {msg.exch_receipt_ssboe}")
    print(f"       exch_receipt_nsecs : {msg.exch_receipt_nsecs}")

    print(f"")

#   ===========================================================================
#   This routine connects to the specified URI and returns the websocket
#   connection object.

async def connect_to_rithmic(uri, ssl_context):
    # disable ping keep-alive mechanism
    try:
        ws = await websockets.connect(uri, ssl=ssl_context, ping_interval=3)
        print(f"connected to {uri}")
    except websockets.exceptions.InvalidURI as e:
        print(f"Invalid URI: {e}")
        sys.exit()
    except websockets.exceptions.InvalidHandshake as e:
        print(f"Invalid handshake with {uri}: {e}")
        sys.exit()
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"Connection to {uri} was closed unexpectedly: {e}")
        sys.exit()
    except websockets.exceptions.InvalidMessage as e:
        print(f"Received invalid message from {uri}: {e}")
        sys.exit()
    except Exception as e:
        print(f"An unexpected error occurred while connecting to {uri}: {e}")
        sys.exit()
    return (ws)

#   ===========================================================================
#   This routine sends a heartbeat request.  It does not do anything about
#   reading the heartbeat response (see consume() for reading).

async def send_heartbeat(ws):
    rq = request_heartbeat_pb2.RequestHeartbeat()

    rq.template_id = 18

    serialized = rq.SerializeToString()

    buf  = bytearray()
    buf  = serialized

    await ws.send(buf)
    print(f"sent heartbeat request")

#   ===========================================================================
#   This routine requests the list of available Rithmic systems, and waits for
#   the response from the server.  After this request is processed by the
#   server, the server will initiate the closing of the websocket connection.

async def list_systems(ws):
    rq = request_rithmic_system_info_pb2.RequestRithmicSystemInfo()

    rq.template_id = 16
    rq.user_msg.append("hello");
    rq.user_msg.append("world");

    serialized = rq.SerializeToString()

    buf  = bytearray()
    buf  = serialized

    await ws.send(buf)
    print(f"sent list_systems request")

    rp_buf = bytearray()
    rp_buf = await ws.recv()

    rp = response_rithmic_system_info_pb2.ResponseRithmicSystemInfo()
    rp.ParseFromString(rp_buf[0:])

    # an rp code of "0" indicates that the request was completed successfully
    if rp.rp_code[0] == "0":
        print(f" Available Systems :")
        print(f" ===================")
        for sys_name in rp.system_name:
            print(f"{sys_name}")
    else:
        print(f" error retrieving system list :")
        print(f" template_id : {rp.template_id}")
        print(f"    user_msg : {rp.user_msg}")
        print(f"     rp code : {rp.rp_code}")
        print(f" system_name : {rp.system_name}")

#   ===========================================================================
#   This routine reads data off the wire, occassionally sending heartbeats if
#   there is no traffic.  It will exit after receiving max_num_messages.

async def consume(ws):
    # send a heartbeat immediately, just in case
    await send_heartbeat(ws)

    max_num_msgs = 20
    num_msgs = 0

    # After 20 messages are read, this routine will exit
    while num_msgs < max_num_msgs and g_order_is_complete == False:
        msg_buf = bytearray()

        waiting_for_msg = True
        
        while waiting_for_msg:
            try:
                print(f"waiting for msg ...")
                msg_buf = await asyncio.wait_for(ws.recv(), timeout=5)
                waiting_for_msg = False
            except asyncio.TimeoutError:
                if ws.open:
                    print(f"sending heartbeat ...")
                    await send_heartbeat(ws)
                else:
                    print(f"connection appears to be closed.  exiting consume()")
                    sys.exit()
            except websockets.ConnectionClosed:
                print("Connection to server was lost. Exiting")
                sys.exit()

        num_msgs += 1

        print(f"received msg {num_msgs} of {max_num_msgs}")

        # parse into base class just to get a template id
        base = base_pb2.Base()
        base.ParseFromString(msg_buf[0:])

        # route msg based on template id
        if base.template_id == 13:
            msg_type = "logout response"
            print(f" consumed msg : {msg_type} ({base.template_id})")
            
        elif base.template_id == 19:
            msg_type = "heartbeat response"
            print(f" consumed msg : {msg_type} ({base.template_id})")
            
        elif base.template_id == 101:
            msg_type = "market data update response"
            print(f" consumed msg : {msg_type} ({base.template_id})")

        elif base.template_id == 150: # last_trade
            msg_type = "last_trade"
            print(f" consumed msg : {msg_type} ({base.template_id})")
            
        elif base.template_id == 151: # best_bid_offer
            msg_type = "best_bid_offer"
            print(f" consumed msg : {msg_type} ({base.template_id})")
            
        elif base.template_id == 309: # response to subscribe_for_order_updates
            msg_type = "response_subscribe_for_order_updates"
            print(f" consumed msg : {msg_type} ({base.template_id})")

        elif base.template_id == 313: # response to new_order
            msg_type = "response_new_order"
            print(f" consumed msg : {msg_type} ({base.template_id})")

        elif base.template_id == 351: # rithmic_order_notification
            msg_type = "rithmic_order_notification"
            print(f" consumed msg : {msg_type} ({base.template_id})")

            await rithmic_order_notification_cb(msg_buf)

        elif base.template_id == 352: # exchange_order_notification
            msg_type = "exchange_order_notification"
            print(f" consumed msg : {msg_type} ({base.template_id})")

            await exchange_order_notification_cb(msg_buf)

    if g_order_is_complete == True:
        print(f"order is complete ...")

#   ===========================================================================
#   This routine logs into the specified Rithmic system using the specified
#   credentials.  It will also wait for the login response.

async def rithmic_login(ws, system_name, infra_type, user_id, password):

    rq = request_login_pb2.RequestLogin()

    rq.template_id      = 10;
    rq.template_version = "3.9"
    rq.user_msg.append("hello")

    rq.user        = user_id
    rq.password    = password
    rq.app_name    = "SampleOrder.py"
    rq.app_version = "0.3.0.0"
    rq.system_name = system_name
    rq.infra_type  = infra_type

    serialized = rq.SerializeToString()

    buf  = bytearray()
    buf  = serialized

    await ws.send(buf)

    rp_buf = bytearray()
    rp_buf = await ws.recv()

    rp = response_login_pb2.ResponseLogin()
    rp.ParseFromString(rp_buf[0:])

    print(f"")
    print(f"      ResponseLogin :")
    print(f"      ===============")
    print(f"        template_id : {rp.template_id}")
    print(f"   template_version : {rp.template_version}")
    print(f"           user_msg : {rp.user_msg}")
    print(f"            rp code : {rp.rp_code}")
    print(f"             fcm_id : {rp.fcm_id}")
    print(f"             ib_id  : {rp.ib_id}")
    print(f"       country_code : {rp.country_code}")
    print(f"         state_code : {rp.state_code}")
    print(f" heartbeat_interval : {rp.heartbeat_interval}")
    print(f"     unique_user_id : {rp.unique_user_id}")
    print(f"")

#   ===========================================================================
#   This routine retrieves additional info about the currently logged in user.
#   It will also wait for the (login info) response.

async def login_info(ws):

    rq = request_login_info_pb2.RequestLoginInfo()

    rq.template_id = 300;
    rq.user_msg.append("hello")

    serialized = rq.SerializeToString()

    buf  = bytearray()
    buf  = serialized

    await ws.send(buf)

    rp_buf = bytearray()
    rp_buf = await ws.recv()

    rp = response_login_info_pb2.ResponseLoginInfo()
    rp.ParseFromString(rp_buf[0:])

    user_type_to_string = {0:'ADMIN',
                           1:'FCM',
                           2:'IB',
                           3:'TRADER'}

    print(f"")
    print(f" ResponseLoginInfo :")
    print(f" ===================")
    print(f"       template_id : {rp.template_id}")
    print(f"          user_msg : {rp.user_msg}")
    print(f"           rp code : {rp.rp_code}")
    print(f"            fcm_id : {rp.fcm_id}")
    print(f"            ib_id  : {rp.ib_id}")
    print(f"        first_name : {rp.first_name}")
    print(f"         last_name : {rp.last_name}")
    print(f"         user_type : {rp.user_type} ({user_type_to_string[rp.user_type]})")
    print(f"")

    if rp.rp_code[0] == '0':
        print(f"retrieving account list ...")
        await list_accounts(ws, rp.fcm_id, rp.ib_id, rp.user_type)

        print(f"retrieving trade routes ...")
        await list_trade_routes(ws)

#   ===========================================================================
#   This routine retrieves the list of accounts that the currently logged in
#   user has permission to trade on.  It will also wait for the associated
#   response.

async def list_accounts(ws, fcm_id, ib_id, user_type):
    global g_rcvd_account
    global g_fcm_id
    global g_ib_id
    global g_account_id

    rq = request_account_list_pb2.RequestAccountList()

    rq.template_id = 302;
    rq.user_msg.append("hello")
    rq.fcm_id      = fcm_id
    rq.ib_id       = ib_id
    rq.user_type   = user_type

    serialized = rq.SerializeToString()

    buf  = bytearray()
    buf  = serialized

    rp_is_done = False

    await ws.send(buf)

    rp_buf = bytearray()
    
    while rp_is_done == False:
        rp_buf = await ws.recv()

        rp = response_account_list_pb2.ResponseAccountList()
        rp.ParseFromString(rp_buf[0:])

        print(f"")
        print(f" ResponseAccountList :")
        print(f" =====================")
        print(f"         template_id : {rp.template_id}")
        print(f"            user_msg : {rp.user_msg}")
        print(f"  rq_handler_rp_code : {rp.rq_handler_rp_code}")
        print(f"             rp code : {rp.rp_code}")
        print(f"              fcm_id : {rp.fcm_id}")
        print(f"              ib_id  : {rp.ib_id}")
        print(f"          account_id : {rp.account_id}")
        print(f"        account_name : {rp.account_name}")
        print(f"")

        # store the first acount we get for placing the order
        if g_rcvd_account == False         and \
           len(rp.rq_handler_rp_code) > 0  and \
           rp.rq_handler_rp_code[0] == "0" and \
           len(rp.fcm_id) > 0              and \
           len(rp.ib_id) > 0               and \
           len(rp.account_id) > 0:
            g_fcm_id       = rp.fcm_id
            g_ib_id        = rp.ib_id
            g_account_id   = rp.account_id
            g_rcvd_account = True

        # How to determine when the response is done :
        # --------------------------------------------
        # There can be many messages returned when requesting accounts.  The first
        # <N> messages will return actual accounts, and the last message will have
        # an rp_code indicating any error condition.  This non-empty rp_code also
        # indicates that all the responses to the account list request are now done.
        #
        # When receiving the <N> responses, the rq_handler_rp_code
        # will not be empty, and contain "0" when the request is being processed
        # without error.  The rp_code on these same messages
        # will be empty.  When receiving the last message, sometimes called the
        # end-of-response message, the rq_handler_rp_code will be empty, and the
        # rp_code will contain any error code.  A value of "0" in rp_code indicates
        # there there was no error.
        #
        # This pattern of a request having <N> + 1 response msgs appears often in
        # the RProtocolAPI.
    
        if len(rp.rp_code) > 0:
            rp_is_done = True

#   ===========================================================================
#   This routine retrieves the list of trade routes from the order plant.  It
#   will also wait for the associated response.

async def list_trade_routes(ws):
    global g_rcvd_trade_route
    global g_trade_route
    global g_exchange
    global g_fcm_id
    global g_ib_id

    rq = request_trade_routes_pb2.RequestTradeRoutes()

    rq.template_id = 310;
    rq.user_msg.append("hello")
    rq.subscribe_for_updates = False

    serialized = rq.SerializeToString()

    buf  = bytearray()
    buf  = serialized

    rp_is_done = False

    await ws.send(buf)

    rp_buf = bytearray()
    
    while rp_is_done == False:
        rp_buf = await ws.recv()

        rp = response_trade_routes_pb2.ResponseTradeRoutes()
        rp.ParseFromString(rp_buf[0:])

        print(f"")
        print(f" ResponseTradeRoutes :")
        print(f" =====================")
        print(f"         template_id : {rp.template_id}")
        print(f"            user_msg : {rp.user_msg}")
        print(f"  rq_handler_rp_code : {rp.rq_handler_rp_code}")
        print(f"             rp code : {rp.rp_code}")
        print(f"              fcm_id : {rp.fcm_id}")
        print(f"              ib_id  : {rp.ib_id}")
        print(f"            exchange : {rp.exchange}")
        print(f"         trade_route : {rp.trade_route}")
        print(f"              status : {rp.status}")
        print(f"          is_default : {rp.is_default}")
        print(f"")

        # store the first applicable trade route we get
        if g_rcvd_trade_route == False     and \
           len(rp.rq_handler_rp_code) > 0  and \
           rp.rq_handler_rp_code[0] == "0" and \
           g_fcm_id   == rp.fcm_id         and \
           g_ib_id    == rp.ib_id          and \
           g_exchange == rp.exchange:
            g_trade_route      = rp.trade_route
            g_rcvd_trade_route = True

        if len(rp.rp_code) > 0:
            rp_is_done = True

#   ===========================================================================
#   This routine subscribes for updates on any orders on the specified fcm, ib
#   and account.  Any received messages from this subscription request
#   are handled elsewhere (see the consume() routine)

async def subscribe_for_order_updates(ws, fcm_id, ib_id, account_id):

    rq = request_subscribe_for_order_updates_pb2.RequestSubscribeForOrderUpdates()

    rq.template_id      = 308;
    rq.user_msg.append("hello")

    rq.fcm_id     = fcm_id
    rq.ib_id      = ib_id
    rq.account_id = account_id

    serialized = rq.SerializeToString()

    buf  = bytearray()
    buf  = serialized

    await ws.send(buf)

#   ===========================================================================
#   This routine submits a request for a new order.  Updates to this order
#   are received when subscribing to order updates on the specified
#   fcm/ib/account (see subscribe_order(), above).

async def new_order(ws, fcm_id, ib_id, account_id, exchange, symbol, trade_route, side):

    rq = request_new_order_pb2.RequestNewOrder()

    rq.template_id      = 312;
    rq.user_msg.append("hello")

    rq.fcm_id     = fcm_id
    rq.ib_id      = ib_id
    rq.account_id = account_id

    rq.exchange   = exchange
    rq.symbol     = symbol

    rq.quantity   = 1

    if side == "B" or side == "b":
        rq.transaction_type = request_new_order_pb2.RequestNewOrder.TransactionType.BUY
    else:
        rq.transaction_type = request_new_order_pb2.RequestNewOrder.TransactionType.SELL
    rq.duration         = request_new_order_pb2.RequestNewOrder.Duration.DAY
    rq.price_type       = request_new_order_pb2.RequestNewOrder.PriceType.MARKET
    rq.manual_or_auto   = request_new_order_pb2.RequestNewOrder.OrderPlacement.MANUAL

    rq.trade_route = trade_route

    serialized = rq.SerializeToString()

    buf  = bytearray()
    buf  = serialized

    await ws.send(buf)

#   ===========================================================================
#   This routine sends a logout request.  It does not wait for a response.

async def rithmic_logout(ws):
    rq = request_logout_pb2.RequestLogout()

    rq.template_id      = 12;
    rq.user_msg.append("hello")

    serialized = rq.SerializeToString()

    buf = bytearray()
    buf = serialized

    await ws.send(buf)

#   ===========================================================================
#   This routine closes the websocket connection.  The status code is
#   hard-coded to 1000, indicating a normal closure.

async def disconnect_from_rithmic(ws):
    await ws.close(1000, "see you tomorrow")

#   ===========================================================================

loop = asyncio.get_event_loop()

num_args = len(sys.argv)

if num_args == 2 or num_args == 8:
    uri = sys.argv[1]

    # check if we should use ssl/tls
    ssl_context = None
    if "wss://" in uri:
        # Set up the ssl context.  One can also use an alternate SSL/TLS cert file
        # or database
        ssl_context   = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        localhost_pem = pathlib.Path(__file__).with_name("rithmic_ssl_cert_auth_params")
        ssl_context.load_verify_locations(localhost_pem)

    ws = loop.run_until_complete(connect_to_rithmic(uri, ssl_context))
    
    if num_args == 2:
        loop.run_until_complete(list_systems(ws))
    elif num_args == 8:
        system_name = sys.argv[2]
        user_id     = sys.argv[3]
        password    = sys.argv[4]
        g_exchange  = sys.argv[5]
        g_symbol    = sys.argv[6]
        g_side      = sys.argv[7]

        loop.run_until_complete(rithmic_login(ws,
                                              system_name,
                                              request_login_pb2.RequestLogin.SysInfraType.ORDER_PLANT,
                                              user_id,
                                              password))

        loop.run_until_complete(login_info(ws))

        print(f"")
        print(f"     g_rcvd_account : {g_rcvd_account}")
        print(f"           g_fcm_id : {g_fcm_id}")
        print(f"            g_ib_id : {g_ib_id}")
        print(f"       g_account_id : {g_account_id}")
        print(f"")
        print(f"         g_exchange : {g_exchange}")
        print(f"           g_symbol : {g_symbol}")
        print(f"")
        print(f" g_rcvd_trade_route : {g_rcvd_trade_route}")
        print(f"      g_trade_route : {g_trade_route}")
        print(f"")
  
        if g_rcvd_account and g_rcvd_trade_route:
            loop.run_until_complete(subscribe_for_order_updates(ws,          \
                                                                g_fcm_id,    \
                                                                g_ib_id,     \
                                                                g_account_id))
            loop.run_until_complete(new_order(ws,           \
                                              g_fcm_id,     \
                                              g_ib_id,      \
                                              g_account_id, \
                                              g_exchange,   \
                                              g_symbol,     \
                                              g_trade_route,\
                                              g_side))

            loop.run_until_complete(consume(ws))

        if ws.open:
            print(f"logging out ...")
            loop.run_until_complete(rithmic_logout(ws))
            print(f"disconnecting ...")
            loop.run_until_complete(disconnect_from_rithmic(ws))
            print(f"done!")
        else:
            print(f"connection appears to be closed.  exiting app.")
else:
    print(f"{USAGE}")
    print(f"{USAGE_2}")

#   ===========================================================================
