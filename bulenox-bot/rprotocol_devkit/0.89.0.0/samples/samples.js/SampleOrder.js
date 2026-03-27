#!/usr/bin/env node

/*   
==============================================================================
Copyright (c) 2025 by Omnesys Technologies, Inc.  All rights reserved.

Warning :
    This Software Product is protected by copyright law and international
    treaties.  Unauthorized use, reproduction or distribution of this
    Software Product (including its documentation), or any portion of it,
    may result in severe civil and criminal penalties, and will be
    prosecuted to the maximum extent possible under the law.

    Omnesys Technologies, Inc. will compensate individuals providing
    admissible evidence of any unauthorized use, reproduction, distribution
    or redistribution of this Software Product by any person, company or 
    organization.

This Software Product is licensed strictly in accordance with a separate
Software System License Agreement, granted by Omnesys Technologies, Inc.,
which contains restrictions on use, reverse engineering, disclosure,
confidentiality and other matters.

==============================================================================

==============================================================================

SampleOrder.js
==============
This sample program is intended to provide a simple, but working, JavaScript
example of how one might use R | Protocol API to place an order.
It makes use of the WebSocket API, which is built into modern JavaScript 
environments.

- This program can be run with no arguments to display usage information.

- To list the available Rithmic systems, pass in a single argument
    specifying the URI of the server.

- To log in to a specific system and place an order, a number of
    additional parameters are necessary, specifying the system, login
    credentials, exchange, instrument and side.

Node.js version info :
- This script is designed to run on Node.js v14.0.0 or later.
- It uses the built-in WebSocket API available in modern Node.js versions.
- It requires the 'protobufjs' library for handling Protocol Buffers.

==============================================================================

==============================================================================
To install node and npm on a mac using brew use 
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.1/install.sh | bash
nvm install {node_version}
-This will install node and npm for you

Below are library dependencies
- One might need to install them using commands such as :

    npm install protobufjs@7.4.0
    npm install ws@8.18.0

-Before running, run
nvm use {node_version}

To ensure you are using the right version of Node 

Tested on: Mac OSX 14.2.1 node version 20.2.0 

To install nvm, node, and npm on Windows you can follow this url 
https://github.com/coreybutler/nvm-windows/releases
and download the nvm-setup.exe file to download it to your machine
Launch powershell as an administrator and run 
nvm -v
If nvm is installed correctly a version number will appear in your console

you can then run 
nvm install {node_version}
to install the appropriate node version you require 
==============================================================================
To run the program:
node SampleOrder.js 
node SampleOrder.js connect_point [system_name user_id password exchange symbol side(B/S)]
(try wss://rituz00100.rithmic.com:443 for the connect_point)
*/

const WebSocket = require('ws');
const protobuf = require('protobufjs');

const protobufEngine = new protobuf.Root();
protobufEngine.loadSync('base.proto');

protobufEngine.loadSync('request_account_list.proto');
protobufEngine.loadSync('response_account_list.proto');

protobufEngine.loadSync('request_heartbeat.proto');
protobufEngine.loadSync('response_heartbeat.proto');

protobufEngine.loadSync('request_rithmic_system_info.proto');
protobufEngine.loadSync('response_rithmic_system_info.proto');

protobufEngine.loadSync('request_login.proto');
protobufEngine.loadSync('response_login.proto');

protobufEngine.loadSync('request_login_info.proto');
protobufEngine.loadSync('response_login_info.proto');

protobufEngine.loadSync('request_logout.proto');
protobufEngine.loadSync('response_logout.proto');

protobufEngine.loadSync('request_trade_routes.proto');
protobufEngine.loadSync('response_trade_routes.proto');

protobufEngine.loadSync('request_subscribe_for_order_updates.proto');
protobufEngine.loadSync('response_subscribe_for_order_updates.proto');

protobufEngine.loadSync('request_new_order.proto');
protobufEngine.loadSync('response_new_order.proto');

protobufEngine.loadSync('exchange_order_notification.proto');

protobufEngine.loadSync('rithmic_order_notification.proto');

// Load all the proto files

//   ===========================================================================

const USAGE = `node SampleOrder.js connect_point [system_name user_id password exchange symbol side(B/S)]`;
const USAGE_2 = "  (try wss://rituz00100.rithmic.com:443 for the connect_point)";

//   ===========================================================================
//   some global variables to gather info needed to place the order

var g_symbol = "";
var g_exchange = "";

var g_side = "";

var g_order_is_complete = false;

// ===========================================================================
// Helper function to create a buffer from a protobuf message
function createSerializedMessage(messageType, initialValue) {
    const message = messageType.create(initialValue)
    const serialized = messageType.encode(message).finish()
    return Buffer.from(serialized)
}

// ===========================================================================
// This routine interprets the msg_buf as a RithmicOrderNotification.
async function rithmicOrderNotificationCb(msgBuf) {
    // rithmic_order_notification : 351
    const RithmicOrderNotification = protobufEngine.lookupType('RithmicOrderNotification');
    const msg = RithmicOrderNotification.decode(msgBuf);
    const NotifyType = RithmicOrderNotification.NotifyType;
    const TransactionType = RithmicOrderNotification.TransactionType;
    const Duration = RithmicOrderNotification.Duration; 
    const PriceType = RithmicOrderNotification.PriceType; 
    const OrderPlacement = RithmicOrderNotification.OrderPlacement; 

    const notifyTypeToString = {
        [NotifyType.ORDER_RCVD_FROM_CLNT] : "ORDER_RCVD_FROM_CLNT",
        [NotifyType.MODIFY_RCVD_FROM_CLNT]: "MODIFY_RCVD_FROM_CLNT",
        [NotifyType.CANCEL_RCVD_FROM_CLNT]: "CANCEL_RCVD_FROM_CLNT",
        [NotifyType.OPEN_PENDING]: "OPEN_PENDING",
        [NotifyType.MODIFY_PENDING]: "MODIFY_PENDING",
        [NotifyType.CANCEL_PENDING]: "CANCEL_PENDING",
        [NotifyType.ORDER_RCVD_BY_EXCH_GTWY]: "ORDER_RCVD_BY_EXCH_GTWY",
        [NotifyType.MODIFY_RCVD_BY_EXCH_GTWY]: "MODIFY_RCVD_BY_EXCH_GTWY",
        [NotifyType.CANCEL_RCVD_BY_EXCH_GTWY]: "CANCEL_RCVD_BY_EXCH_GTWY",
        [NotifyType.ORDER_SENT_TO_EXCH]: "ORDER_SENT_TO_EXCH",
        [NotifyType.MODIFY_SENT_TO_EXCH]: "MODIFY_SENT_TO_EXCH",
        [NotifyType.CANCEL_SENT_TO_EXCH]: "CANCEL_SENT_TO_EXCH",
        [NotifyType.OPEN]: "OPEN",
        [NotifyType.MODIFIED]: "MODIFIED",
        [NotifyType.COMPLETE]: "COMPLETE",
        [NotifyType.MODIFICATION_FAILED]: "MODIFICATION_FAILED",
        [NotifyType.CANCELLATION_FAILED]: "CANCELLATION_FAILED",
        [NotifyType.TRIGGER_PENDING]: "TRIGGER_PENDING",
        [NotifyType.GENERIC]: "GENERIC",
        [NotifyType.LINK_ORDERS_FAILED]: "LINK_ORDERS_FAILED"
    };

    const transactionTypeToString = {
        [TransactionType.BUY]: "BUY",
        [TransactionType.SELL]: "SELL"
    };

    const durationToString = {
        [Duration.Day]: "DAY",
        [Duration.GTC]: "GTC",
        [Duration.IOC]: "IOC",
        [Duration.FOK]: "FOK"
    };

    const priceTypeToString = {
        [PriceType.LIMIT]: "LIMIT",
        [PriceType.MARKET]: "MARKET",
        [PriceType.STOP_LIMIT]: "STOP_LIMIT",
        [PriceType.STOP_MARKET]: "STOP_MARKET"
    };

    const orderPlacementToString = {
        [OrderPlacement.MANUAL]: "MANUAL",
        [OrderPlacement.AUTO]: "AUTO"
    };

    console.log("");
    console.log(" RithmicOrderNotification    : ");
    console.log(`               templateId    : ${msg.templateId}`);
    console.log(`               notifyType    : ${notifyTypeToString[msg.notifyType]} (${msg.notifyType})`);
    console.log(`               isSnapshot    : ${msg.isSnapshot}`);

    console.log(`                   status    : ${msg.status}`);
    console.log(`                 basketId    : ${msg.basketId}`);
    console.log(`         originalBasketId    : ${msg.originalBasketId}`);

    console.log(`                    fcmId    : ${msg.fcmId}`);
    console.log(`                     ibId    : ${msg.ibId}`);
    console.log(`                accountId    : ${msg.accountId}`);
    console.log(`                   userId    : ${msg.userId}`);

    console.log(`                   symbol    : ${msg.symbol}`);
    console.log(`                 exchange    : ${msg.exchange}`);
    console.log(`            tradeExchange    : ${msg.tradeExchange}`);
    console.log(`               tradeRoute    : ${msg.tradeRoute}`);
    console.log(`          exchangeOrderId    : ${msg.exchangeOrderId}`);
    console.log(`           instrumentType    : ${msg.instrumentType}`);
    
    console.log(`                 quantity    : ${msg.quantity}`);
    console.log(`                    price    : ${msg.price}`);
    console.log(`             triggerPrice    : ${msg.triggerPrice}`);
    
    console.log(`          transactionType    : ${transactionTypeToString[msg.transactionType]} (${msg.transactionType})`);
    console.log(`                 duration    : ${durationToString[msg.duration]} (${msg.duration})`);
    console.log(`                priceType    : ${priceTypeToString[msg.priceType]} (${msg.priceType})`);
    console.log(`            origPriceType    : ${priceTypeToString[msg.origPriceType]} (${msg.origPriceType})`);
    console.log(`             manualOrAuto    : ${orderPlacementToString[msg.manualOrAuto]} (${msg.manualOrAuto})`);

    console.log(`           sequenceNumber    : ${msg.sequenceNumber}`);
    console.log(`       origSequenceNumber    : ${msg.origSequenceNumber}`);
    console.log(`        corSequenceNumber    : ${msg.corSequenceNumber}`);

    console.log(`                 currency    : ${msg.currency}`);
    console.log(`              countryCode    : ${msg.countryCode}`);

    console.log(`                     text    : ${msg.text}`);
    console.log(`               reportText    : ${msg.reportText}`);
    console.log(`                  remarks    : ${msg.remarks}`);

    console.log(`                    ssboe    : ${msg.ssboe}`);
    console.log(`                    usecs    : ${msg.usecs}`);
    console.log("");

    if (msg.status === "complete" || 
        msg.notifyType === RithmicOrderNotification.NotifyType.COMPLETE) {
        g_order_is_complete = true;
    }
}

//   ===========================================================================
//   This routine interprets the msg_buf as a ExchangeOrderNotification.
async function exchangeOrderNotificationCb(msgBuf) {
    // exchange_order_notification : 352
    const ExchangeOrderNotification 
    = protobufEngine.lookupType('ExchangeOrderNotification');
    const msg = ExchangeOrderNotification.decode(msgBuf);
    const NotifyType = ExchangeOrderNotification.NotifyType
    const TransactionType = ExchangeOrderNotification.TransactionType
    const Duration = ExchangeOrderNotification.Duration; 
    const PriceType = ExchangeOrderNotification.PriceType; 
    const OrderPlacement = ExchangeOrderNotification.OrderPlacement; 

    const notifyTypeToString = {
        [NotifyType.STATUS]: "STATUS",
        [NotifyType.MODIFY]: "MODIFY",
        [NotifyType.CANCEL]: "CANCEL",
        [NotifyType.TRIGGER]: "TRIGGER",
        [NotifyType.FILL]: "FILL",
        [NotifyType.REJECT]: "REJECT",
        [NotifyType.NOT_MODIFIED]: "NOT_MODIFIED",
        [NotifyType.NOT_CANCELLED]: "NOT_CANCELLED",
        [NotifyType.GENERIC]: "GENERIC"
    };

    const transactionTypeToString = {
        [TransactionType.BUY]: "BUY",
        [TransactionType.SELL]: "SELL"
    };

    const durationToString = {
        [Duration.DAY]: "DAY",
        [Duration.GTC]: "GTC",
        [Duration.IOC]: "IOC",
        [Duration.FOK]: "FOK"
    };

    const priceTypeToString = {
        [PriceType.LIMIT]: "LIMIT",
        [PriceType.MARKET]: "MARKET",
        [PriceType.STOP_LIMIT]: "STOP_LIMIT",
        [PriceType.STOP_MARKET]: "STOP_MARKET"
    };

    const orderPlacementToString = {
        [OrderPlacement.MANUAL]: "MANUAL",
        [OrderPlacement.AUTO]: "AUTO"
    };

    console.log("");
    console.log(" ExchangeOrderNotification   : ");
    console.log(`                templateId   : ${msg.templateId}`);
    console.log(`                notifyType   : 
        ${notifyTypeToString[msg.notifyType]} (${msg.notifyType})`);
    console.log(`                isSnapshot   : ${msg.is_snapshot}`);

    console.log(`                reportType   : ${msg.reportType}`);
    console.log(`                    status   : ${msg.status}`);
    console.log(`                  basketId   : ${msg.basketId}`);
    console.log(`          originalBasketId   : ${msg.originalBasketId}`);

    console.log(`                     fcmId   : ${msg.fcmId}`);
    console.log(`                      ibId   : ${msg.ibId}`);
    console.log(`                 accountId   : ${msg.accountId}`);
    console.log(`                    userId   : ${msg.userId}`);

    console.log(`                    symbol   : ${msg.symbol}`);
    console.log(`                  exchange   : ${msg.exchange}`);
    console.log(`             tradeExchange   : ${msg.tradeExchange}`);
    console.log(`                tradeRoute   : ${msg.tradeRoute}`);
    console.log(`           exchangeOrderId   : ${msg.exchangeOrderId}`);
    console.log(`            instrumentType   : ${msg.instrumentType}`);
    
    console.log(`                  quantity   : ${msg.quantity}`);
    console.log(`                     price   : ${msg.price}`);
    console.log(`              triggerPrice   : ${msg.triggerPrice}`);
    
    console.log(`           transactionType   : ${transactionTypeToString[msg.transactionType]} (${msg.transactionType})`);
    console.log(`                  duration   : ${durationToString[msg.duration]} (${msg.duration})`);
    console.log(`                 priceType   : ${priceTypeToString[msg.priceType]} (${msg.priceType})`);
    console.log(`             origPriceType   : ${priceTypeToString[msg.origPriceType]} (${msg.origPriceType})`);
    console.log(`              manualOrAuto   :  ${orderPlacementToString[msg.manualOrAuto]} (${msg.manualOrAuto})`);

    console.log(`             confirmedSize   : ${msg.confirmedSize}`);
    console.log(`             confirmedTime   : ${msg.confirmedTime}`);
    console.log(`             confirmedDate   : ${msg.confirmedDate}`);
    console.log(`               confirmedId   : ${msg.confirmedId}`);
    
    console.log(`              modifiedSize   : ${msg.modifiedSize}`);
    console.log(`              modifiedTime   : ${msg.modifiedTime}`);
    console.log(`              modifiedDate   : ${msg.modifiedDate}`);
    console.log(`                  modifyId   : ${msg.modifyId}`);

    console.log(`             cancelledSize   : ${msg.cancelledSize}`);
    console.log(`             cancelledTime   : ${msg.cancelledTime}`);
    console.log(`             cancelledDate   : ${msg.cancelledDate}`);
    console.log(`               cancelledId   : ${msg.cancelledId}`);

    console.log(`                 fillPrice   : ${msg.fillPrice}`);
    console.log(`                  fillSize   : ${msg.fillSize}`);
    console.log(`                  fillTime   : ${msg.fillTime}`);
    console.log(`                  fillDate   : ${msg.fillDate}`);
    console.log(`                    fillId   : ${msg.fillId}`);

    console.log(`                 triggerId   : ${msg.triggerId}`);

    console.log(`            sequenceNumber   : ${msg.sequenceNumber}`);
    console.log(`        origSequenceNumber   : ${msg.origSequenceNumber}`);
    console.log(`         corSequenceNumber   : ${msg.corSequenceNumber}`);

    console.log(`                  currency   : ${msg.currency}`);
    console.log(`               countryCode   : ${msg.countryCode}`);

    console.log(`                      text   : ${msg.text}`);
    console.log(`                reportText   : ${msg.reportText}`);
    console.log(`                   remarks   : ${msg.remarks}`);

    console.log(`                     ssboe   : ${msg.ssboe}`);
    console.log(`                     usecs   : ${msg.usecs}`);

    console.log(`          exchReceiptSsboe   : ${msg.exchReceiptSsboe}`);
    console.log(`          exchReceiptNsecs   : ${msg.exchReceiptNsecs}`);

    console.log("");
}

// ===========================================================================
// This routine connects to the specified URI and returns the websocket
// connection object.

async function connectToRithmic(uri, ssl_context) {
    //Usually an SSL context does not need to be defined since node 
    //has its own ssl context 
    return new Promise((resolve, reject) => {
        const ws = new WebSocket(uri, {
            rejectUnauthorized: false,
            //cert: ssl_context ? fs.readFileSync(ssl_context) : undefined
        });

        ws.on('open', () => {
            console.log(`Connected to ${uri}`);
            console.log('')
            resolve(ws);
        });

        ws.on('error', (error) => {
            reject(error);
        });
    });
}

// ===========================================================================
// This routine sends a heartbeat request.  It does not do anything about
// reading the heartbeat response (see consume() for reading).

async function sendHeartbeat(ws) {
    const RequestHeartbeat = protobufEngine.lookupType('RequestHeartbeat');
    const buffer = createSerializedMessage(RequestHeartbeat, {
        templateId: 18
    })
    await ws.send(buffer);
    console.log("Sent heartbeat request");
    console.log('')
}

// ===========================================================================
// This routine requests the list of available Rithmic systems, and waits for
// the response from the server.  After this request is processed by the
// server, the server will initiate the closing of the websocket connection.

async function listSystems(ws) {
    const RequestRithmicSystemInfo = protobufEngine.lookupType('RequestRithmicSystemInfo');
    const buffer = createSerializedMessage(RequestRithmicSystemInfo, {
        templateId: 16,
        userMsg: ['hello', 'world']
    });

    await ws.send(buffer);
    console.log("Sent list_systems request");

    const response = await new Promise(resolve => ws.once('message', resolve));

    const ResponseRithmicSystemInfo = protobufEngine.lookupType('ResponseRithmicSystemInfo');
    const decodedResponse = ResponseRithmicSystemInfo.decode(response);


    // an rp code of "0" indicates that the request was completed successfully
    if (decodedResponse.rpCode[0] === "0") {
        console.log("Available Systems :");
        console.log("===================");
        decodedResponse.systemName.forEach(sys => {
            console.log(sys);
        });
    } else {
        console.log("error retrieving system list :");
        console.log(`templateId : ${decodedResponse.templateId}`);
        console.log(`   userMsg : ${decodedResponse.userMsg}`);
        console.log(`    rpCode : ${decodedResponse.rpCode}`);
        console.log(`systemName : ${decodedResponse.systemName}`);
        console.log('')
    }
}

// ===========================================================================
// This routine reads data off the wire, occassionally sending heartbeats if
// there is no traffic.  It will exit after receiving max_num_messages.
async function consume(ws) {
    await sendHeartbeat(ws);

    const maxNumMsgs = 20;
    let numMsgs = 0;

    while (numMsgs < maxNumMsgs && g_order_is_complete === false) {
        try {
            console.log("Waiting for msg ...");
            console.log('')
            const msgBuf = await new Promise((resolve, reject) => {
                const list = []
                const timeout = setTimeout(() => {
                    reject(new Error('Timeout'));
                }, 5000);
                ws.on('message', (data) => {
                    clearTimeout(timeout);
                    list.push(data)
                    resolve(list);
                });
            });

            numMsgs++;
            console.log(`received msg ${numMsgs} of ${maxNumMsgs}`);
            console.log('')

            // Attempt to parse the base message
            const Base = protobufEngine.lookupType('Base');
            msgBuf.forEach((buffer, index) => {
                try {
                    const base = Base.decode(buffer);
                    let msgType;
                    switch (base.templateId) {
                        case 13:
                            msgType = "logout response";
                            break;
                        case 19:
                            msgType = "heartbeat response";
                            break;
                        case 101:
                            msgType = "market data update response";
                            break;
                        case 150:
                            msgType = "last_trade";
                            break;
                        case 151:
                            msgType = "best_bid_offer";
                            break;
                        case 309:
                            msgType = "response_subscribe_for_order_updates";
                            break;
                        case 313:
                            msgType = "response_new_order";
                            break;
                        case 351:
                            msgType = "rithmic_order_notification";
                            rithmicOrderNotificationCb(buffer);
                            break;
                        case 352:
                            msgType = "exchange_order_notification";
                            exchangeOrderNotificationCb(buffer);
                            break;
                        default:
                            msgType = "unrecognized template id";
                    }
                    console.log(`consumed msg : ${msgType} (${base.templateId})`);
                    console.log('')
                } catch (decodeError) {
                    console.error("Error decoding base message:", decodeError);
                    console.log("Message buffer length:", buffer.length);
                }
            })

        } catch (error) {
            console.error("Error in consume loop:", error);
            console.log('')
            if (ws.readyState === WebSocket.OPEN) {
                console.log("sending heartbeat ...");
                console.log('')
                await sendHeartbeat(ws);
            } else {
                console.log("connection appears to be closed. exiting consume()");
                break;
            }
        }
    }

    if (g_order_is_complete === true) {
        console.log("order is complete ...");
    }
}

// ===========================================================================
// This routine logs into the specified Rithmic system using the specified
// credentials.  It will also wait for the login response.
async function  rithmicLogin(ws, systemName, infraType, userId, password) {
    const RequestLogin = protobufEngine.lookupType('RequestLogin');
    const buffer = createSerializedMessage(RequestLogin, {
        templateId: 10,
        templateVersion: '3.9',
        userMsg: ['hello'],
        user: userId,
        password: password,
        appName: 'SampleMD.js',
        appVersion: '0.3.0.0',
        systemName: systemName,
        infraType: infraType
    });
    await ws.send(buffer);
    const response = await new Promise(resolve => ws.once('message', resolve));
    const ResponseLogin = protobufEngine.lookupType('ResponseLogin');
    const decodedResponse = ResponseLogin.decode(response);
    console.log('ResponseLogin:');
    console.log('===============');
    console.log(`templateId         : ${decodedResponse.templateId}`);
    console.log(`templateVersion    : ${decodedResponse.templateVersion}`);
    console.log(`userMsg            : ${decodedResponse.userMsg}`); 
    console.log(`rpCode             : ${decodedResponse.rpCode}`);
    console.log(`fcmId              : ${decodedResponse.fcmId}`)
    console.log(`ibId               : ${decodedResponse.ibId}`);
    console.log(`countryCode        : ${decodedResponse.countryCode}`);
    console.log(`stateCode          : ${decodedResponse.stateCode}`);
    console.log(`heartbeatInterval  : ${decodedResponse.heartbeatInterval}`);
    console.log(`uniqueUserId       : ${decodedResponse.uniqueUserId}`);
    console.log("");       
}

// ===========================================================================
// This routine retrieves additional info about the currently logged in user.
// It will also wait for the (login info) response.
async function loginInfo(ws) {
    const RequestLoginInfo = protobufEngine.lookupType('RequestLoginInfo');

    const buffer = createSerializedMessage(RequestLoginInfo, { 
        templateId: 300,
        userMsg: ["hello"]
    })
    await ws.send(buffer);

    const response = await new Promise(resolve => ws.once('message', resolve));

    const ResponseLoginInfo = protobufEngine.lookupType('ResponseLoginInfo');
    const decodedResponse = ResponseLoginInfo.decode(response);

    const userTypeToString = {
        0: 'ADMIN',
        1: 'FCM',
        2: 'IB',
        3: 'TRADER'
    };

    console.log("");
    console.log("ResponseLoginInfo :");
    console.log("===================");
    console.log(`       templateId : ${decodedResponse.templateId}`);
    console.log(`          userMsg : ${decodedResponse.userMsg}`);
    console.log(`           rpCode : ${decodedResponse.rpCode}`);
    console.log(`            fcmId : ${decodedResponse.fcmId}`);
    console.log(`            ibId  : ${decodedResponse.ibId}`);
    console.log(`        firstName : ${decodedResponse.firstName}`);
    console.log(`         lastName : ${decodedResponse.lastName}`);
    console.log(`         userType : ${decodedResponse.userType} (${userTypeToString[decodedResponse.userType]})`);
    console.log("");
    let tradeDetails
    if (decodedResponse.rpCode[0] === '0') {
        console.log("retrieving account list ...");
        const accountData = await listAccounts(ws, 
            decodedResponse.fcmId, 
            decodedResponse.ibId, 
            decodedResponse.userType);
        console.log("retrieving trade routes ...");
        console.log('')
        const tradeRouteData = await listTradeRoutes(ws, 
            accountData.fcmId, 
            accountData.ibId, 
            accountData.accountId);
        tradeDetails = {...accountData, ...tradeRouteData} 
    }
    return tradeDetails
}

// ===========================================================================
// This routine retrieves the list of accounts that the currently logged in
// user has permission to trade on.  It will also wait for the associated
// response.
async function listAccounts(ws, fcm_id, ib_id, user_type) {
    const RequestAccountList
     = protobufEngine.lookupType('RequestAccountList');
    const buffer = createSerializedMessage(RequestAccountList, { 
        templateId: 302,
        userMsg: ['hello'],
        fcmId: fcm_id,
        ibId: ib_id,
        userType: user_type
    });
    
    await ws.send(buffer);
    
    const ResponseAccountList 
    = protobufEngine.lookupType('ResponseAccountList');

    // Ensure all accounts on the wire are processed before moving on
    const response = await new Promise((resolve, reject) => {
        const list = []
        const timeout = setTimeout(() => {
            reject(new Error('Timeout waiting for account list response'));
        }, 10000);
    
        ws.on('message', (data) => {
            clearTimeout(timeout);
            const resp = ResponseAccountList.decode(data);
            // If rpCode is '0', we assume the process is done
            if (resp.rpCode && resp.rpCode[0] === '0') {
                resolve(list);
            } else { 
                //If its the end of response don't push 
                //it on the list as we'd like to iterate over it
                list.push(resp);
            }
        });
    });

    let receivedAccount = false 
    let ibId = ""
    let fcmId = ""
    let accountId = ""
    //Can use index to determine what account it is 
    response.forEach((returnedAccount, index) => { 
        console.log("");
        console.log(" ResponseAccountList :");
        console.log(" =====================");
        console.log(`         templateId : ${returnedAccount.templateId}`);
        console.log(`            userMsg : ${returnedAccount.userMsg}`);
        console.log(`  rqHandlerRpCode   : ${returnedAccount.rqHandlerRpCode}`);
        console.log(`             rpCode : ${returnedAccount.rpCode}`);
        console.log(`              fcmId : ${returnedAccount.fcmId}`);
        console.log(`              ibId  : ${returnedAccount.ibId}`);
        console.log(`          accountId : ${returnedAccount.accountId}`);
        console.log(`        accountName : ${returnedAccount.accountName}`);
        console.log("");
        // Store the first account we get for placing the order
        if (!receivedAccount &&
            returnedAccount.rqHandlerRpCode.length > 0 &&
            returnedAccount.rqHandlerRpCode[0] === "0" &&
            returnedAccount.fcmId.length > 0 &&
            returnedAccount.ibId.length > 0 &&
            returnedAccount.accountId.length > 0) {
            fcmId = returnedAccount.fcmId;
            ibId = returnedAccount.ibId;
            accountId = returnedAccount.accountId;
            receivedAccount = true;
        }
    });
    return { 
        fcmId: fcmId, 
        ibId: ibId, 
        accountId: accountId, 
        receivedAccount: receivedAccount
    }
}


// =========================================================================
// This routine retrieves the list of trade routes from the order plant.  It
// will also wait for the associated response.

async function listTradeRoutes(ws, fcmId, ibId, accountId) {
    const RequestTradeRoutes = protobufEngine.lookupType('RequestTradeRoutes');

    const buffer = createSerializedMessage(RequestTradeRoutes, { 
        templateId: 310,
        userMsg: ["hello"],
        subscribeForUpdates: false
    })

    await ws.send(buffer);
    const ResponseTradeRoutes = protobufEngine.lookupType('ResponseTradeRoutes');

    const response = await new Promise((resolve, reject) => { 
        const list = []
        const timeout = setTimeout(() => { 
            reject(new Error('Timeout waiting for trade route response'));
        }, 10000)

        ws.on('message', (data) => {
            clearTimeout(timeout);
            const resp = ResponseTradeRoutes.decode(data);
            // If rpCode is '0', we assume the process is done
            if (resp.rpCode && resp.rpCode[0] === '0') {
                resolve(list);
            } else { 
                //If its the end of response don't push it on the 
                //list as we'd like to iterate over it
                list.push(resp);
            }
        });
    })

    let tradeRoute = ""
    let receivedTradeRoute = false 
    response.forEach((returnedRoute, index) => { 
        console.log(" ResponseTradeRoutes :");
        console.log(" =====================");
        console.log(`         templateId  : ${returnedRoute.templateId}`);
        console.log(`            userMsg  : ${returnedRoute.userMsg}`);
        console.log(`    rqHandlerRpCode  : ${returnedRoute.rqHandlerRpCode}`);
        console.log(`             rpCode  : ${returnedRoute.rpCode}`);
        console.log(`              fcmId  : ${returnedRoute.fcmId}`);
        console.log(`               ibId  : ${returnedRoute.ibId}`);
        console.log(`           exchange  : ${returnedRoute.exchange}`);
        console.log(`         tradeRoute  : ${returnedRoute.tradeRoute}`);
        console.log(`             status  : ${returnedRoute.status}`);
        console.log(`          isDefault  : ${returnedRoute.isDefault}`);
        console.log("");

        // store the first applicable trade route we get
        if (receivedTradeRoute === false &&
            returnedRoute.rqHandlerRpCode.length > 0 &&
            returnedRoute.rqHandlerRpCode[0] === "0" &&
            fcmId == returnedRoute.fcmId &&
            ibId == returnedRoute.ibId &&
            g_exchange == returnedRoute.exchange) {

            tradeRoute = returnedRoute.tradeRoute;
            receivedTradeRoute = true;
        }
    })
    return {
        tradeRoute: tradeRoute,
        receivedTradeRoute: receivedTradeRoute
    }

}

// ===========================================================================
// This routine subscribes for updates on any orders on the specified fcm, ib
// and account. Any received messages from this subscription request
// are handled elsewhere (see the consume() routine)
async function subscribeForOrderUpdates(ws, fcmId, ibId, accountId) {
    const RequestSubscribeForOrderUpdates = protobufEngine.lookupType('RequestSubscribeForOrderUpdates');
    const buffer = createSerializedMessage(RequestSubscribeForOrderUpdates, {
        templateId: 308,
        userMsg: ["hello"],
        fcmId: fcmId,
        ibId: ibId,
        accountId: accountId
    });

    await ws.send(buffer);
}

// ===========================================================================
// This routine submits a request for a new order. Updates to this order
// are received when subscribing to order updates on the specified
// fcm/ib/account (see subscribeForOrderUpdates(), above).
async function newOrder(ws, 
    fcmId, 
    ibId, 
    accountId, 
    exchange, 
    symbol, 
    tradeRoute, 
    side) {
    const RequestNewOrder = protobufEngine.lookupType('RequestNewOrder');
    const TransactionType = RequestNewOrder.TransactionType;
    const Duration = RequestNewOrder.Duration;
    const PriceType = RequestNewOrder.PriceType;
    const OrderPlacement = RequestNewOrder.OrderPlacement;

    const buffer = createSerializedMessage(RequestNewOrder, {
        templateId: 312,
        userMsg: ["hello"],
        fcmId: fcmId,
        ibId: ibId,
        accountId: accountId,
        exchange: exchange,
        symbol: symbol,
        quantity: 1,
        transactionType: side.toUpperCase() === "B" ? TransactionType.BUY : TransactionType.SELL,
        duration: Duration.DAY,
        priceType: PriceType.MARKET,
        manualOrAuto: OrderPlacement.MANUAL,
        tradeRoute: tradeRoute
    });
    await ws.send(buffer);
}


// ===========================================================================
// This routine sends a logout request. It does not wait for a response.
async function rithmicLogout(ws) {
    const RequestLogout = protobufEngine.lookupType('RequestLogout');
    const buffer = createSerializedMessage(RequestLogout, {
        templateId: 12,
        userMsg: ["hello"]
    });

    await ws.send(buffer);
}

// ===========================================================================
// This routine closes the websocket connection. The status code is
// hard-coded to 1000, indicating a normal closure.
async function disconnectFromRithmic(ws) {
    await ws.close(1000, "see you tomorrow");
}

// ===========================================================================
// Main execution
async function main() {
    const numArgs = process.argv.length;

    if (numArgs === 3 || numArgs === 9) {
        const uri = process.argv[2];

        // check if we should use ssl/tls
        let sslContext;
        if (uri.includes("wss://")) {
            // Set up the ssl context. 
            //One can also use an alternate SSL/TLS cert file
            // or database
            sslContext = {
                rejectUnauthorized: false,
                // You might need to adjust this part to load 
                //the SSL certificate
                // ca: fs.readFileSync(path.join(__dirname, 
                //'rithmic_ssl_cert_auth_params'))
            };
        }

        const ws = await connectToRithmic(uri, sslContext);
        if (numArgs === 3) {
            await listSystems(ws);
        } else if (numArgs === 9) {
            const systemName = process.argv[3];
            const userId = process.argv[4];
            const password = process.argv[5];
            g_exchange = process.argv[6];
            g_symbol = process.argv[7];
            g_side = process.argv[8];

            const RequestLogin = protobufEngine.lookupType('RequestLogin');
            await rithmicLogin(ws, 
                systemName, 
                RequestLogin.SysInfraType.ORDER_PLANT, 
                userId, 
                password);

            const tradeDetails = await loginInfo(ws);

            console.log("");
            console.log(`   receivedAccount     : ${tradeDetails.receivedAccount}`);
            console.log(`             fcmId     : ${tradeDetails.fcmId}`);
            console.log(`              ibId     : ${tradeDetails.ibId}`);
            console.log(`         accountId     : ${tradeDetails.accountId}`);
            console.log("");
            console.log(`        g_exchange     : ${g_exchange}`);
            console.log(`          g_symbol     : ${g_symbol}`);
            console.log("");
            console.log(`receivedTradeRoute     : ${tradeDetails.receivedTradeRoute}`);
            console.log(`        tradeRoute     : ${tradeDetails.tradeRoute}`);
            console.log("");

            if (tradeDetails.receivedAccount 
                && tradeDetails.receivedTradeRoute) {
                await subscribeForOrderUpdates(ws, 
                    tradeDetails.fcmId, 
                    tradeDetails.ibId, 
                    tradeDetails.accountId);
                await newOrder(ws, 
                    tradeDetails.fcmId, 
                    tradeDetails.ibId, 
                    tradeDetails.accountId, 
                    g_exchange, 
                    g_symbol, 
                    tradeDetails.tradeRoute, 
                    g_side);

                await consume(ws);
            }

            if (ws.readyState === WebSocket.OPEN) {
                console.log("logging out ...");
                await rithmicLogout(ws);
                console.log("disconnecting ...");
                await disconnectFromRithmic(ws);
                console.log("done!");
            } else {
                console.log("connection appears to be closed. exiting app.");
            }
        }
    } else {
        console.log(USAGE);
        console.log(USAGE_2);
    }
}

// ===========================================================================

main().catch(console.error);
