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

SampleBar.js
==============
This sample program is intended to provide a simple, but working, JavaScript
example of how one might use R | Protocol API to retrieve tick bars.
It makes use of the WebSocket API, which is built into modern JavaScript 
environments.

- This program can be run with no arguments to display usage information.

- To list the available Rithmic systems, pass in a single argument
    specifying the URI of the server.

- To log in to a specific system and retrieve tick bars, a number of
additional parameters are necessary, specifying the system login 
credentials and instrument

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
node SampleBar.js 
node SampleBar.js <uri> [system_name user_id password exchange symbol]
try wss://rituz00100.rithmic.com:443 for the uri

*/

const protobuf = require('protobufjs');
const WebSocket = require('ws');

// Load proto files
const protobufEngine = new protobuf.Root();
protobufEngine.loadSync('base.proto');
protobufEngine.loadSync('request_heartbeat.proto');
protobufEngine.loadSync('response_heartbeat.proto');
protobufEngine.loadSync('request_rithmic_system_info.proto');
protobufEngine.loadSync('response_rithmic_system_info.proto');
protobufEngine.loadSync('request_login.proto');
protobufEngine.loadSync('response_login.proto');
protobufEngine.loadSync('request_logout.proto');
protobufEngine.loadSync('response_logout.proto');
protobufEngine.loadSync('request_tick_bar_replay.proto');
protobufEngine.loadSync('response_tick_bar_replay.proto');

const USAGE = `node SampleBar.js connect_point [system_name user_id password exchange symbol]`;
const USAGE_2 = ` (try wss://rituz00100.rithmic.com:443 for the connect_point)`;

let g_rp_is_done = false;

//============================================================================
// Helper function to create a buffer from a protobuf message
function createSerializedMessage(messageType, initialValue) {
    const message = messageType.create(initialValue)
    const serialized = messageType.encode(message).finish()
    return Buffer.from(serialized)
}

// ===========================================================================
// This routine interprets the msgBuf as a ResponseTickBarReplay message
async function responseTickBarReplayCb(msgBuf) {
    const ResponseTickBarReplay 
    = protobufEngine.lookupType('ResponseTickBarReplay');
    const decodedMsg = ResponseTickBarReplay.decode(msgBuf);

    const barTypeToString = {
        0: "TICK_BAR",
        1: "RANGE_BAR",
        2: "VOLUME_BAR"
    };

    const barSubTypeToString = {
        0: "REGULAR",
        1: "CUSTOM"
    };

    console.log("");
    console.log("   ResponseTickBarReplay : ");
    console.log(`             templateId  : ${decodedMsg.templateId}`);
    console.log(`                userMsg  : ${decodedMsg.userMsg}`);
    console.log(`      rqHandlerRpCode    : ${decodedMsg.rqHandlerRpCode}`);
    console.log(`                 rpCode  : ${decodedMsg.rpCode}`);
    console.log(`                  symbol : ${decodedMsg.symbol}`);
    console.log(`                exchange : ${decodedMsg.exchange}`);
    console.log(`                    type : ${barTypeToString[decodedMsg.type]} (${decodedMsg.type})`);
    console.log(`                 subType : ${barSubTypeToString[decodedMsg.subType]} (${decodedMsg.subType})`);
    console.log(`          typeSpecifier  : ${decodedMsg.typeSpecifier}`);
    console.log(`              numTrades  : ${decodedMsg.numTrades}`);
    console.log(`                 volume  : ${decodedMsg.volume}`);
    console.log(`              bidVolume  : ${decodedMsg.bidVolume}`);
    console.log(`              askVolume  : ${decodedMsg.askVolume}`);
    console.log(`              openPrice  : ${decodedMsg.openPrice}`);
    console.log(`             closePrice  : ${decodedMsg.closePrice}`);
    console.log(`              highPrice  : ${decodedMsg.highPrice}`);
    console.log(`               lowPrice  : ${decodedMsg.lowPrice}`);
    console.log(`   customSessionOpenSsm  : ${decodedMsg.customSessionOpenSsm}`);
    console.log(`           dataBarSsboe  : ${decodedMsg.dataBarSsboe[0]}`);
    console.log(`           dataBarUsecs  : ${decodedMsg.dataBarUsecs[0]}`);
    console.log("");

    if (decodedMsg.rqHandlerRpCode.length === 0 
        && decodedMsg.rpCode.length > 0) {
        console.log("tick bar responses are done.");
        console.log('')
        g_rp_is_done = true;
    }
}

//============================================================================
// Connect to Rithmic
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
            console.log("Could not connect to Rithmic system");
            reject(error);
        });
    });
}

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

async function listSystems(ws) {
    const RequestRithmicSystemInfo = protobufEngine.lookupType('RequestRithmicSystemInfo');
    const buffer = createSerializedMessage(RequestRithmicSystemInfo, {
        templateId: 16,
        userMsg: ['hello']
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
        console.log(" error retrieving system list :");
        console.log(` templateId : ${decodedResponse.templateId}`);
        console.log(`    userMsg : ${decodedResponse.userMsg}`);
        console.log(`     rpCode : ${decodedResponse.rpCode}`);
        console.log(` systemName : ${decodedResponse.systemName}`);
    }
}

// ===========================================================================
// Consumes messages off the wire 
async function consume(ws) {
    await sendHeartbeat(ws);

    const maxNumMsgs = 10000;
    let numMsgs = 0;

    return new Promise((resolve) => {
        const messageHandler = (buffer) => {
            try {
                const Base = protobufEngine.lookupType('Base');
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
                    case 151:
                        msgType = "best_bid_offer";
                        break;
                    case 150:
                        msgType = "last_trade";
                        break;
                    case 207:
                        msgType = "tick bar replay response";
                        responseTickBarReplayCb(buffer);
                        break;
                    case 251:
                        msgType = "tick bar";
                        break;
                    default:
                        msgType = "unrecognized template id";
                }
                console.log(`consumed msg : ${msgType} (${base.templateId})`);
                console.log('')
                
                numMsgs++;
                console.log(`received msg ${numMsgs} of ${maxNumMsgs}`);
                console.log('')

                // Check if we should stop
                if (numMsgs >= maxNumMsgs || g_rp_is_done) {
                    ws.removeListener('message', messageHandler);
                    resolve();
                }
            } catch (decodeError) {
                console.error("Error decoding base message:", decodeError);
                console.log("Message buffer length:", buffer.length);
            }
        };

        // Set up error handling
        const errorHandler = (error) => {
            console.error('WebSocket error:', error);
            ws.removeListener('message', messageHandler);
            ws.removeListener('error', errorHandler);
            ws.removeListener('close', closeHandler);
            resolve();
        };

        // Set up close handling
        const closeHandler = () => {
            console.log('WebSocket closed');
            ws.removeListener('message', messageHandler);
            ws.removeListener('error', errorHandler);
            ws.removeListener('close', closeHandler);
            resolve();
        };

        // Add all event listeners
        ws.addListener('message', messageHandler);
        ws.addListener('error', errorHandler);
        ws.addListener('close', closeHandler);

        // Set up periodic heartbeat
        const heartbeatInterval = setInterval(async () => {
            if (ws.readyState === WebSocket.OPEN && !g_rp_is_done) {
                await sendHeartbeat(ws);
            } else {
                clearInterval(heartbeatInterval);
            }
        }, 30000); // Send heartbeat every 30 seconds

        // Clean up heartbeat interval when done
        ws.once('close', () => clearInterval(heartbeatInterval));
    });
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
//This routine requests tick bars for the specified instrument. Any received 
//messages resulting from this request are handled elsewhere (see consume())
async function replayTickBars(ws, exchange, symbol) {
    const RequestTickBarReplay = protobufEngine.lookupType('RequestTickBarReplay');
    const BarType = RequestTickBarReplay.BarType;
    const BarSubType = RequestTickBarReplay.BarSubType;

    const rq = {
        templateId: 206,
        userMsg: ["hello"],
        symbol: symbol,
        exchange: exchange,
        barType: BarType.TICK_BAR,
        barTypeSpecifier: "1",
        barSubType: BarSubType.REGULAR,
        startIndex: 1595260800,
        finishIndex: 1595261400
    };

    // Create a buffer from the request object
    const buffer = createSerializedMessage(RequestTickBarReplay, rq);

    // Send the buffer
    await ws.send(buffer);
}

// ===========================================================================
// This routine sends a logout request. It does not wait for a response.
async function rithmicLogout(ws) {
    const RequestLogout = protobufEngine.lookupType('RequestLogout');
    const buffer = createSerializedMessage(RequestLogout, {
        template_id: 12,
        user_msg: ['hello']
    });
    await ws.send(buffer);
}

// ===========================================================================
// This routine closes the websocket connection. The status code is
// hard-coded to 1000, indicating a normal closure.
async function disconnectFromRithmic(ws) {
    await ws.close(1000, 'see you tomorrow');
}

// ===========================================================================
// Main execution
async function main() {
    const args = process.argv.slice(2);

    if (args.length === 1 || args.length === 6) {
        const uri = args[0];
        const ws = await connectToRithmic(uri);
        if (args.length === 1) {
            await listSystems(ws);
        } else if (args.length === 6) {
            const [systemName, userId, password, exchange, symbol] = args.slice(1);
            const RequestLogin = protobufEngine.lookupType('RequestLogin');
            await rithmicLogin(ws, 
                systemName, 
                RequestLogin.SysInfraType.HISTORY_PLANT, 
                userId, 
                password);
            await replayTickBars(ws, exchange, symbol);
            await consume(ws);

            if (ws.readyState === WebSocket.OPEN) {
                console.log("Logging out ...");
                await rithmicLogout(ws);
                console.log("Disconnecting ...");
                await disconnectFromRithmic(ws);
                console.log("Done!");
            } else {
                console.log("Connection appears to be closed. Exiting app.");
            }
        }
    } else {
        console.log(USAGE);
        console.log(USAGE_2);
    }
}

main().catch(console.error);
