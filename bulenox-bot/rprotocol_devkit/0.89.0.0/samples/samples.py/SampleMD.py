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
#   SampleMD.py
#   ===========
#   This sample program is intended to provide a simple, but working, python3
#   example of how one might use R | Protocol API to subscribe to market data.
#   using asynchronous programming.
#   It makes use of the websockets library, which is built over the asyncio
#   library.
#
#   - This program can be run with no arguments to display usage information.
#
#   - To list the available Rithmic systems, pass in a single argument
#     specifying the URI of the server.
#
#   - To log in to a specific system and subscribe to market data, a number of
#     additional parameters are necessary, specifying the system, login
#     credentials and instrument.
#
#   - User can type 'quit' to exit. 
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

import request_heartbeat_pb2
import response_heartbeat_pb2

import request_rithmic_system_info_pb2
import response_rithmic_system_info_pb2

import request_login_pb2
import response_login_pb2

import request_logout_pb2
import response_logout_pb2

import request_market_data_update_pb2
import response_market_data_update_pb2
import last_trade_pb2
import best_bid_offer_pb2

#   ===========================================================================

USAGE   = "SampleMD.py connect_point [system_name user_id password exchange symbol]"
USAGE_2 = "  (try wss://rituz00100.rithmic.com:443 for the connect_point)"

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
#   there is no traffic.  It will exit after user sends 'quit'.

async def consume(ws, stop_event):
    # send a heartbeat immediately, just in case
    await send_heartbeat(ws)

    # After user inputs 'quit', this routine will exit
    while not stop_event.is_set():
        msg_buf = bytearray()

        waiting_for_msg = True
        
        while waiting_for_msg:
            try:
                print(f"\nwaiting for msg ...")
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
            msg = response_market_data_update_pb2.ResponseMarketDataUpdate()

            msg.ParseFromString(msg_buf[0:])
            print(f"")
            print(f" ResponseMarketDataUpdate : ")
            print(f"                 user_msg : {msg.user_msg}")
            print(f"                  rp_code : {msg.rp_code}")

        elif base.template_id == 151: # best_bid_offer
            msg = best_bid_offer_pb2.BestBidOffer()

            msg.ParseFromString(msg_buf[0:])
            
            is_bid        = True if msg.presence_bits & best_bid_offer_pb2.BestBidOffer.PresenceBits.BID else False
            is_ask        = True if msg.presence_bits & best_bid_offer_pb2.BestBidOffer.PresenceBits.ASK else False
            is_lean_price = True if msg.presence_bits & best_bid_offer_pb2.BestBidOffer.PresenceBits.LEAN_PRICE else False
            
            print(f"")
            print(f"      BestBidOffer : ")
            print(f"            symbol : {msg.symbol}")
            print(f"          exchange : {msg.exchange}")
            print(f"     presence_bits : {msg.presence_bits}")
            print(f"        clear_bits : {msg.clear_bits}")
            print(f"       is_snapshot : {msg.is_snapshot}")
            print(f"         bid_price : {msg.bid_price} ({is_bid})")
            print(f"          bid_size : {msg.bid_size}")
            print(f"        bid_orders : {msg.bid_orders}")
            print(f" bid_implicit_size : {msg.bid_implicit_size}")

            print(f"         ask_price : {msg.ask_price} ({is_ask})")
            print(f"          ask_size : {msg.ask_size}")
            print(f"        ask_orders : {msg.ask_orders}")
            print(f" ask_implicit_size : {msg.ask_implicit_size}")
            
            print(f"        lean_price : {msg.lean_price} ({is_lean_price})")

            print(f"             ssboe : {msg.ssboe}")
            print(f"             usecs : {msg.usecs}")
            print(f"")
            
            
        elif base.template_id == 150: # last_trade
            msg = last_trade_pb2.LastTrade()

            msg.ParseFromString(msg_buf[0:])
            
            is_last_trade     = True if msg.presence_bits & last_trade_pb2.LastTrade.PresenceBits.LAST_TRADE else False
            is_net_change     = True if msg.presence_bits & last_trade_pb2.LastTrade.PresenceBits.NET_CHANGE else False
            is_percent_change = True if msg.presence_bits & last_trade_pb2.LastTrade.PresenceBits.PRECENT_CHANGE else False
            is_volume         = True if msg.presence_bits & last_trade_pb2.LastTrade.PresenceBits.VOLUME else False
            is_vwap           = True if msg.presence_bits & last_trade_pb2.LastTrade.PresenceBits.VWAP else False

            print(f"")
            print(f"                   LastTrade : ")
            print(f"                      symbol : {msg.symbol}")
            print(f"                    exchange : {msg.exchange}")
            print(f"               presence_bits : {msg.presence_bits}")
            print(f"                  clear_bits : {msg.clear_bits}")
            print(f"                 is_snapshot : {msg.is_snapshot}")
            print(f"                 trade_price : {msg.trade_price} ({is_last_trade})")
            print(f"                  trade_size : {msg.trade_size}")

            if msg.aggressor == last_trade_pb2.LastTrade.TransactionType.BUY:
                print(f"                   aggressor : BUY ({msg.aggressor})")
            else:
                print(f"                   aggressor : SELL ({msg.aggressor})")
                
            print(f"           exchange_order_id : {msg.exchange_order_id}")
            print(f" aggressor_exchange_order_id : {msg.aggressor_exchange_order_id}")
            print(f"                  net_change : {msg.net_change} ({is_net_change})")
            print(f"              percent_change : {msg.percent_change} ({is_percent_change})")
            print(f"                      volume : {msg.volume} ({is_volume})")
            print(f"                        vwap : {msg.vwap} ({is_vwap})")
            print(f"                       ssboe : {msg.ssboe}")
            print(f"                       usecs : {msg.usecs}")
            print(f"                source_ssboe : {msg.source_ssboe}")
            print(f"                source_usecs : {msg.source_usecs}")
            print(f"                source_nsecs : {msg.source_nsecs}")
            print(f"                   jop_ssboe : {msg.jop_ssboe}")
            print(f"                   jop_usecs : {msg.jop_nsecs}")
            print(f"")
            

        print("Type 'quit' to exit")			

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
    rq.app_name    = "SampleMD.py"
    rq.app_version = "0.3.0.0"
    rq.system_name = system_name
    rq.infra_type  = infra_type

    serialized = rq.SerializeToString()
    length     = len(serialized)

    buf  = bytearray()
    buf  = serialized

    await ws.send(buf)

    rp_buf = bytearray()
    rp_buf = await ws.recv()

    rp = response_login_pb2.ResponseLogin()

    rp.ParseFromString(rp_buf[0:])

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

#   ===========================================================================
#   This routine subscribes for trade and best bid/offer market data for the
#   specified instrument.  Any received messages from this subscription request
#   are handled elsewhere (see the consume() routine)

async def subscribe(ws, exchange, symbol):

    rq = request_market_data_update_pb2.RequestMarketDataUpdate()

    rq.template_id      = 100;
    rq.user_msg.append("hello")

    rq.symbol      = symbol
    rq.exchange    = exchange
    rq.request     = request_market_data_update_pb2.RequestMarketDataUpdate.Request.SUBSCRIBE
    rq.update_bits = request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.LAST_TRADE | request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.BBO

    serialized = rq.SerializeToString()

    buf  = bytearray()
    buf  = serialized

    await ws.send(buf)

#   ===========================================================================
#   This routine unsubscribes for trade and best bid/offer market data for the
#   specified instrument.

async def unsubscribe(ws, exchange, symbol):

    rq = request_market_data_update_pb2.RequestMarketDataUpdate()

    rq.template_id      = 100;
    rq.user_msg.append("hello")

    rq.symbol      = symbol
    rq.exchange    = exchange
    rq.request     = request_market_data_update_pb2.RequestMarketDataUpdate.Request.UNSUBSCRIBE
    rq.update_bits = request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.LAST_TRADE | request_market_data_update_pb2.RequestMarketDataUpdate.UpdateBits.BBO

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
#   This routine waits for user input and sets the async event when 'quit' is
#   received

async def handle_user_input(stop_event):
    while True:
        user_input = await loop.run_in_executor(None, input)
        if user_input.strip().lower() == 'quit' :
            print("\nQuit received from user. Exiting\n")
            stop_event.set()
            break

#   ===========================================================================

loop = asyncio.get_event_loop()

num_args = len(sys.argv)

if num_args == 2 or num_args == 7:
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
    elif num_args == 7:
        system_name = sys.argv[2]
        user_id     = sys.argv[3]
        password    = sys.argv[4]
        
        loop.run_until_complete(rithmic_login(ws,
                                              system_name,
                                              request_login_pb2.RequestLogin.SysInfraType.TICKER_PLANT,
                                              user_id,
                                              password))

        exchange    = sys.argv[5]
        symbol      = sys.argv[6]

        loop.run_until_complete(subscribe(ws, exchange, symbol))

        stop_event = asyncio.Event()
        consume_tasks = asyncio.gather(consume(ws,stop_event),handle_user_input(stop_event))
        loop.run_until_complete(consume_tasks)

        if ws.open:
            print(f"unsubscribing ...")
            loop.run_until_complete(unsubscribe(ws, exchange, symbol))
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
