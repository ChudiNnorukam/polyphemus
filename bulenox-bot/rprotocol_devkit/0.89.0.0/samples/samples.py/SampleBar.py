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
#   SampleBar.py
#   ============
#   This sample program is intended to provide a simple, but working, python3
#   example of how one might use R | Protocol API to retrieve tick bars.
#   It makes use of the websockets library, which is built over the asyncio
#   library.
#
#   - This program can be run with no arguments to display usage information.
#
#   - To list the available Rithmic systems, pass in a single argument
#     specifying the URI of the server.
#
#   - To log in to a specific system and retrieve tick bars, a number of
#     additional parameters are necessary, specifying the system, login
#     credentials and instrument.
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
import time

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

import request_tick_bar_replay_pb2
import response_tick_bar_replay_pb2

#   ===========================================================================

USAGE   = "SampleBar.py connect_point [system_name user_id password exchange symbol]"
USAGE_2 = "  (try wss://rituz00100.rithmic.com:443 for the connect_point)"

#   ===========================================================================
#   global variable to signal when to exit consume()

g_rp_is_done = False

#   ===========================================================================
#   This routine interprets the msg_buf as a ResponseTickBarReplay

async def response_tick_bar_replay_cb(msg_buf):
    # rithmic_order_notification : 207
    global g_rp_is_done
    
    msg = response_tick_bar_replay_pb2.ResponseTickBarReplay()
    msg.ParseFromString(msg_buf[0:])

    bar_type_to_string = {response_tick_bar_replay_pb2.ResponseTickBarReplay.BarType.TICK_BAR   : "TICK_BAR",
                          response_tick_bar_replay_pb2.ResponseTickBarReplay.BarType.RANGE_BAR  : "RANGE_BAR",
                          response_tick_bar_replay_pb2.ResponseTickBarReplay.BarType.VOLUME_BAR : "VOLUME_BAR"}

    bar_sub_type_to_string = {response_tick_bar_replay_pb2.ResponseTickBarReplay.BarSubType.REGULAR : "REGULAR",
                              response_tick_bar_replay_pb2.ResponseTickBarReplay.BarSubType.CUSTOM  : "CUSTOM"}
    
    print(f"")
    print(f"   ResponseTickBarReplay : ")
    print(f"             template_id : {msg.template_id}")
    print(f"                user_msg : {msg.user_msg}")
    print(f"      rq_handler_rp_code : {msg.rq_handler_rp_code}")
    print(f"                 rp_code : {msg.rp_code}")

    print(f"                  symbol : {msg.symbol}")
    print(f"                exchange : {msg.exchange}")
    
    print(f"                    type : {bar_type_to_string[msg.type]} ({msg.type})")
    print(f"                sub_type : {bar_sub_type_to_string[msg.sub_type]} ({msg.sub_type})")
    print(f"         type_specifier  : {msg.type_specifier}")

    print(f"              num_trades : {msg.num_trades}")
    print(f"                  volume : {msg.volume}")
    print(f"              bid_volume : {msg.bid_volume}")
    print(f"              ask_volume : {msg.ask_volume}")

    print(f"              open_price : {msg.open_price}")
    print(f"             close_price : {msg.close_price}")
    print(f"              high_price : {msg.high_price}")
    print(f"               low_price : {msg.low_price}")
    print(f" custom_session_open_ssm : {msg.custom_session_open_ssm}")
    print(f"          data_bar_ssboe : {msg.data_bar_ssboe}")
    print(f"          data_bar_usecs : {msg.data_bar_usecs}")
    print(f"")

    # How to determine when the response is done :
    # --------------------------------------------
    # There can be many messages returned when requesting tick bars.  The first
    # <N> messages will have actual tick data, and the last message will have
    # an rp_code indicating any error condition.  This non-empty rp_code also
    # indicates that all the responses to the request are now done.
    #
    # When receiving responses containing tick data, the rq_handler_rp_code
    # will not be empty, and contain "0".  The rp_code on these same messages
    # will be empty.  When receiving the last message, sometimes called the
    # end-of-response message, the rq_handler_rp_code will be empty, and the
    # rp_code will contain any error code.  A value of "0" in rp_code indicates
    # there there was no error.
    #
    # This pattern of a request having <N> + 1 response msgs appears often in
    # the RProtocolAPI.
    if len(msg.rq_handler_rp_code) == 0 and \
           len(msg.rp_code) > 0:
        print(f"tick bar responses are done.")
        g_rp_is_done = True
        
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
        print(f"    user_msg : {rp.user_msg}")
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
    global g_rp_is_done
    # send a heartbeat immediately, just in case
    await send_heartbeat(ws)

    max_num_msgs = 100000
    num_msgs = 0

    # After <max_num_msgs>  messages are read or the tick bar response is done,
    # this routine will exit
    while num_msgs < max_num_msgs and not g_rp_is_done :
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

        elif base.template_id == 151: # best_bid_offer
            msg_type = "best_bid_offer"
            print(f" consumed msg : {msg_type} ({base.template_id})")
            
        elif base.template_id == 150: # last_trade
            msg_type = "last_trade"
            print(f" consumed msg : {msg_type} ({base.template_id})")

        elif base.template_id == 207:
            msg_type = "tick bar replay response"
            print(f" consumed msg : {msg_type} ({base.template_id})")
            await response_tick_bar_replay_cb(msg_buf)
    
        elif base.template_id == 251:
            msg_type = "tick bar"
            print(f" consumed msg : {msg_type} ({base.template_id})")

        else:
            msg_type = "unrecognized template id"
            print(f" consumed msg : {msg_type} ({base.template_id})")
            

#   ===========================================================================
#   This routine logs into the specified Rithmic system using the specified
#   credentials.  It will also wait for the login response.

async def rithmic_login(ws, system_name, infra_type, user_id, password):

    rq = request_login_pb2.RequestLogin()

    rq.template_id      = 10;
    rq.template_version = "3.9"
    rq.user_msg.append("hello")
    #rq.user_msg = "hello"
    rq.user        = user_id
    rq.password    = password
    rq.app_name    = "SampleBar.py"
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
#   This routine requests tick bars for the
#   specified instrument.  Any received messages resulting from this request
#   are handled elsewhere (see the consume() routine)

async def replay_tick_bars(ws, exchange, symbol):

    rq = request_tick_bar_replay_pb2.RequestTickBarReplay()

    rq.template_id      = 206;
    rq.user_msg.append("hello")

    rq.symbol       = symbol
    rq.exchange     = exchange
    rq.bar_type     = request_tick_bar_replay_pb2.RequestTickBarReplay.BarType.TICK_BAR
    rq.bar_type_specifier = "1"

    # sub-type refers to whether the bar is calculated from the beginning of the
    # trading session, or from some custom time period
    rq.bar_sub_type = request_tick_bar_replay_pb2.RequestTickBarReplay.BarSubType.REGULAR

    # The start/end indexes specify the time period over which bars are
    # replayed.  They are integers in unix time format, which is seconds
    # elapsed since Jan 1, 1970 GMT.
    rq.start_index  = 1595260800    # 2020-07-20 12:00:00 EDT
    rq.finish_index = 1595261400    # 2020-07-20 12:10:00 EDT
    
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
                                              request_login_pb2.RequestLogin.SysInfraType.HISTORY_PLANT,
                                              user_id,
                                              password))

        exchange    = sys.argv[5]
        symbol      = sys.argv[6]

        loop.run_until_complete(replay_tick_bars(ws, exchange, symbol))

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
