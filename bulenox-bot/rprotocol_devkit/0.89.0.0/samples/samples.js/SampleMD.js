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

SampleMD.js
==============
This sample program is intended to provide a simple, but working, JavaScript
example of how one might use R | Protocol API to subscribe to market data.
It makes use of the WebSocket API, which is built into modern JavaScript 
environments.

- This program can be run with no arguments to display usage information.

- To list the available Rithmic systems, pass in a single argument
    specifying the URI of the server.

- To log in to a specific system and subscribe to market data, a number of
additional parameters are necessary, specifying the system, login credentials
and instrument

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
node SampleMD.js 
node SampleMD.js <uri> [system_name user_id password exchange symbol]
try wss://rituz00100.rithmic.com:443 for the uri
*/

const protobuf = require('protobufjs');
const WebSocket = require('ws');
const readline = require('readline')

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
protobufEngine.loadSync('request_market_data_update.proto');
protobufEngine.loadSync('response_market_data_update.proto');
protobufEngine.loadSync('last_trade.proto');
protobufEngine.loadSync('best_bid_offer.proto');

//============================================================================
// Helper function to create a serialized buffer from a protobuf message
function createSerializedMessage(messageType, initialValue) {
    const message = messageType.create(initialValue)
    const serialized = messageType.encode(message).finish()
    return Buffer.from(serialized)
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

//============================================================================
// Send heartbeat
async function sendHeartbeat(ws) {
    const RequestHeartbeat = protobufEngine.lookupType('RequestHeartbeat');
    const buffer = createSerializedMessage(RequestHeartbeat, { 
        templateId: 18 
    });
    await ws.send(buffer);
    console.log('Sent heartbeat request');
}

//============================================================================
// List systems
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
        console.log("error retrieving system list :");
        console.log(`templateId : ${decodedResponse.templateId}`);
        console.log(`   userMsg : ${decodedResponse.userMsg}`);
        console.log(`    rpCode : ${decodedResponse.rpCode}`);
        console.log(`systemName : ${decodedResponse.systemName}`);
    }
}

// Consume messages
async function consume(ws) {
    // Create readline interface for user input
    const rl = readline.createInterface({
        input: process.stdin,
        output: process.stdout
    });

    // Set up prompt
    rl.setPrompt('Enter "quit" to exit> ');

    // Flag to control the message loop
    let running = true;

    // Helper function to refresh the prompt
    const refreshPrompt = () => {
        // Clear the current line
        process.stdout.clearLine(0);
        process.stdout.cursorTo(0);
        // Display the prompt again
        rl.prompt(true);
    };

    // Helper function for cleanup and exit
    const cleanupAndExit = async () => {
        console.log('')
        console.log('Received quit command. Cleaning up...');
        running = false;
        rl.close();
        
        try {
            if (ws.readyState === WebSocket.OPEN) {
                // Perform cleanup operations
                console.log('Closing WebSocket connection...');
                await ws.close(1000, 'User requested exit');
            }
        } catch (error) {
            console.error('Error during cleanup:', error);
        } finally {
            console.log('Exiting program...');
            process.exit(0);
        }
    };

    // Listen for user input
    rl.on('line', async (input) => {
        if (input.toLowerCase() === 'quit') {
            await cleanupAndExit();
        } else {
            // If not quitting, show the prompt again
            rl.prompt();
        }
    });

    // Handle CTRL+C (SIGINT)
    rl.on('SIGINT', async () => {
        await cleanupAndExit();
    });

    await sendHeartbeat(ws);

    // Display initial prompt
    rl.prompt();


    while (running) {
        try {
            const msgBuf = await new Promise((resolve, reject) => {
                const list = []
                const timeout = setTimeout(() => {
                    reject(new Error('Timeout'));
                }, 5000);
                ws.once('message', (data) => {
                    clearTimeout(timeout);
                    list.push(data)
                    resolve(list);
                });
            });

            if (!running) break; // Check if we should exit the loop

            const Base = protobufEngine.lookupType('Base');
            
            // Save cursor position before output
            process.stdout.clearLine(0);
            process.stdout.cursorTo(0);
            console.log('')
            msgBuf.forEach((buffer, index) => { 
                try {
                    const base = Base.decode(buffer);
                    switch (base.templateId) {
                        case 13:
                            console.log('Consumed msg: logout response (13)');
                            break;
                        case 19:
                            console.log('Consumed msg: heartbeat response (19)');
                            console.log('')
                            break;
                        case 101:
                            const ResponseMarketDataUpdate = protobufEngine.lookupType('ResponseMarketDataUpdate');
                            const responseMarketDataUpdate = ResponseMarketDataUpdate.decode(buffer);
                            console.log('ResponseMarketDataUpdate:');
                            console.log(`userMsg         : ${responseMarketDataUpdate.userMsg}`);
                            console.log(`rpCode          : ${responseMarketDataUpdate.rpCode}`);
                            console.log('')
                            running = false
                            break;
                        case 151:
                            const BestBidOffer = protobufEngine.lookupType('BestBidOffer');
                            const bestBidOffer = BestBidOffer.decode(buffer);
                            const isBid = Boolean(bestBidOffer.presenceBits & BestBidOffer.PresenceBits.BID);
                            const isAsk = Boolean(bestBidOffer.presenceBits & BestBidOffer.PresenceBits.ASK);
                            const isLeanPrice = Boolean(bestBidOffer.presenceBits & BestBidOffer.PresenceBits.LEAN_PRICE);
                            console.log('BestBidOffer    :');
                            console.log(`symbol          : ${bestBidOffer.symbol}`);
                            console.log(`exchange        : ${bestBidOffer.exchange}`);
            
                            console.log(`presenceBits    : ${bestBidOffer.presenceBits}`);
                            console.log(`clearBits       : ${bestBidOffer.clearBits}`);
                            console.log(`isSnapshot      : ${bestBidOffer.isSnapshot}`);
            
                            console.log(`bidPrice        : ${bestBidOffer.bidPrice} (${isBid})`);
                            console.log(`bidSize         : ${bestBidOffer.bidSize}`);
                            console.log(`bidOrders       : ${bestBidOffer.bidOrders}`);
                            console.log(`bidImplicitSize : ${bestBidOffer.bidImplicitSize}`);
                            console.log(`askPrice        : ${bestBidOffer.askPrice} (${isAsk})`);
                            console.log(`askSize         : ${bestBidOffer.askSize}`);
                            console.log(`askOrders       : ${bestBidOffer.askOrders}`);
                            console.log(`askImplicitSize : ${bestBidOffer.askImplicitSize}`);
            
                            console.log(`leanPrice       : ${bestBidOffer.leanPrice} (${isLeanPrice})`);
                            
                            console.log(`ssboe           : ${bestBidOffer.ssboe}`);
                            console.log(`usecs           : ${bestBidOffer.usecs}`);
                            console.log('');
                            break;
                        case 150:
                            const LastTrade = protobufEngine.lookupType('LastTrade');
                            const lastTrade = LastTrade.decode(buffer);
                            const isLastTrade = Boolean(lastTrade.presenceBits & LastTrade.PresenceBits.LAST_TRADE);
                            const isNetChange = Boolean(lastTrade.presenceBits & LastTrade.PresenceBits.NET_CHANGE);
                            const isPercentChange = Boolean(lastTrade.presenceBits & LastTrade.PresenceBits.PRECENT_CHANGE);
                            const isVolume = Boolean(lastTrade.presenceBits & LastTrade.PresenceBits.VOLUME);
                            const isVwap = Boolean(lastTrade.presenceBits & LastTrade.PresenceBits.VWAP);
            
                            console.log('LastTrade                :');
                            console.log(`symbol                   : ${lastTrade.symbol}`);
                            console.log(`exchange                 : ${lastTrade.exchange}`);
                            console.log(`presenceBits             : ${lastTrade.presenceBits}`);
                            console.log(`clearBits                : ${lastTrade.clearBits}`);
                            console.log(`isSnapshot               : ${lastTrade.isSnapshot}`);
                            console.log(`tradePrice               : ${lastTrade.tradePrice} (${isLastTrade})`);
                            console.log(`tradeSize                : ${lastTrade.tradeSize}`);
            
                            if (lastTrade.aggressor === LastTrade.TransactionType.BUY) {
                                console.log(`aggressor            : BUY (${lastTrade.aggressor})`);
                            } else {
                                console.log(`aggressor            : SELL (${lastTrade.aggressor})`);
                            } 
                            console.log(`exchangeOrderId          : ${lastTrade.exchangeOrderId}`);
                            console.log(`aggressorExchangeOrderId : ${lastTrade.aggressorExchangeOrderId}`);
                            console.log(`netChange                : ${lastTrade.netChange} (${isNetChange})`);
                            console.log(`percentChange            : ${lastTrade.percentChange} (${isPercentChange})`);
                            console.log(`volume                   : ${lastTrade.volume} (${isVolume})`);
                            console.log(`vwap                     : ${lastTrade.vwap} (${isVwap})`);
                            console.log(`ssboe                    : ${lastTrade.ssboe}`);
                            console.log(`usecs                    : ${lastTrade.usecs}`);
                            console.log(`sourceSsboe              : ${lastTrade.sourceSsboe}`);
                            console.log(`sourceUsecs              : ${lastTrade.sourceUsecs}`);
                            console.log(`sourceNsecs              : ${lastTrade.sourceNsecs}`);
                            console.log(`jopSsboe                 : ${lastTrade.jopSsboe}`);
                            console.log(`jopUsecs                 : ${lastTrade.jopUsecs}`);
                            console.log(`jopNsecs                 : ${lastTrade.jopNsecs}`);
                            console.log('');
                            break;
                        default: 
                            console.log("Unrecognized template id")
                            console.log('')
                    }
                } catch (decodeError) { 
                    console.error("Error decoding base message:", decodeError);
                    console.log("Message buffer length:", buffer.length);
                    console.log('')
                }
            });

            // Refresh the prompt after output
            if (running) {
                refreshPrompt();
            }

        } catch (error) {
            if (ws.readyState === WebSocket.OPEN && running) {
                console.log("Sending heartbeat ...");
                await sendHeartbeat(ws);
                refreshPrompt();
            } else {
                console.log("Connection appears to be closed or program is stopping.");
                break;
            }
        }
    }
}

//============================================================================

// Rithmic login
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

//============================================================================
// Subscribe
async function subscribe(ws, exchange, symbol) {
    const RequestMarketDataUpdate = protobufEngine.lookupType('RequestMarketDataUpdate');
    const buffer = createSerializedMessage(RequestMarketDataUpdate, {
        templateId: 100,
        userMsg: ['hello'],
        symbol: symbol,
        exchange: exchange,
        request: RequestMarketDataUpdate.Request.SUBSCRIBE,
        updateBits: RequestMarketDataUpdate.UpdateBits.LAST_TRADE 
        | RequestMarketDataUpdate.UpdateBits.BBO
    });
    await ws.send(buffer);
}

//============================================================================
// Unsubscribe
async function unsubscribe(ws, exchange, symbol) {
    const RequestMarketDataUpdate = protobufEngine.lookupType('RequestMarketDataUpdate');
    const buffer = createSerializedMessage(RequestMarketDataUpdate, {
        templateId: 100,
        userMsg: ['hello'],
        symbol: symbol,
        exchange: exchange,
        request: RequestMarketDataUpdate.Request.UNSUBSCRIBE,
        updateBits: RequestMarketDataUpdate.UpdateBits.LAST_TRADE 
        | RequestMarketDataUpdate.UpdateBits.BBO
    });
    await ws.send(buffer);
}

//============================================================================

// Rithmic logout
async function rithmicLogout(ws) {
    const RequestLogout = protobufEngine.lookupType('RequestLogout');
    const buffer = createSerializedMessage(RequestLogout, {
        template_id: 12,
        user_msg: ['hello']
    });
    await ws.send(buffer);
}

//============================================================================

// Disconnect from Rithmic
async function disconnectFromRithmic(ws) {
    await ws.close(1000, 'See you tomorrow');
}

//============================================================================

// Main function
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
            await rithmicLogin(ws, systemName, RequestLogin.SysInfraType.TICKER_PLANT, userId, password);
            await subscribe(ws, exchange, symbol);
            await consume(ws);

            if (ws.readyState === WebSocket.OPEN) {
                console.log('Unsubscribing...');
                await unsubscribe(ws, exchange, symbol);
                console.log('Logging out...');
                await rithmicLogout(ws);
                console.log('Disconnecting...');
                await disconnectFromRithmic(ws);
                console.log('Done!');
                process.exit(0);
            } else {
                console.log('Connection appears to be closed. Exiting app.');
            }
        }
    } else {
        console.log(`Usage: node SampleMD.js <uri> [system_name user_id password exchange symbol]`);
        console.log('(try wss://rituz00100.rithmic.com:443 for the uri)');
    }
}

main().catch(console.error);
