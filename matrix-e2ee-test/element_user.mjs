// Element-equivalent client using matrix-js-sdk with rust-crypto (the exact
// crypto stack modern Element Web/Desktop uses). Simulates the role of the
// human user in the staging bot2bot scenario.
//
// Flow (selected by $PHASE env var):
//   phase1  — register alice, wait for bot invite to a room, send a message.
//   phase2  — resume alice (existing creds), send another message after the
//             bot device has rotated. This reproduces the staging symptom.

import "fake-indexeddb/auto";
import * as sdk from "matrix-js-sdk";
import { createClient } from "matrix-js-sdk";
import fs from "node:fs";
import fetch from "node-fetch";

// matrix-js-sdk uses global fetch/request; polyfill for node <18 scenarios.
if (!globalThis.fetch) globalThis.fetch = fetch;

const HS = process.env.HOMESERVER || "http://127.0.0.1:16167";
const PHASE = process.env.PHASE || "phase1";
const BOOTSTRAP_TOKEN = process.env.BOOTSTRAP_TOKEN;
const REG_TOKEN = "repro-token";
const STATE_PATH = "/app/alice.state.json";

const log = (...a) => console.log("[alice]", ...a);

async function registerUIA(username, password, regToken) {
    // Two-step UIA like the python repro.
    const body = { username, password, auth: { type: "m.login.dummy" } };
    let resp = await fetch(`${HS}/_matrix/client/v3/register`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
    });
    const first = await resp.json();
    const session = first.session;
    body.auth = { type: "m.login.registration_token", token: regToken, session };
    body.device_id = username.toUpperCase() + "_MJS";
    resp = await fetch(`${HS}/_matrix/client/v3/register`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
    });
    const r = await resp.json();
    if (!r.access_token) throw new Error("register failed: " + JSON.stringify(r));
    return { userId: r.user_id, accessToken: r.access_token, deviceId: r.device_id };
}

async function makeClient({ userId, accessToken, deviceId }) {
    const client = createClient({
        baseUrl: HS,
        accessToken, userId, deviceId,
    });
    // Use rust-crypto (matrix-rust-sdk-crypto-wasm) — the same path Element Web uses.
    await client.initRustCrypto();
    // Mirror Element behavior: accept keys from unverified devices so megolm
    // sessions are shared with everyone in a room (the user's default).
    client.getCrypto()?.setTrustCrossSignedDevices?.(false);
    // Explicitly set global device blacklist to OFF
    if (client.setGlobalBlacklistUnverifiedDevices) {
        client.setGlobalBlacklistUnverifiedDevices(false);
    }
    if (client.setGlobalErrorOnUnknownDevices) {
        client.setGlobalErrorOnUnknownDevices(false);
    }
    await client.startClient({ initialSyncLimit: 10 });
    // Wait for initial sync
    await new Promise((resolve) => {
        const onSync = (state) => {
            if (state === "PREPARED" || state === "SYNCING") {
                client.off("sync", onSync);
                resolve();
            }
        };
        client.on("sync", onSync);
    });
    log("client started, sync state:", client.getSyncState());
    return client;
}

async function waitForInvite(client, timeoutMs = 30000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const rooms = client.getRooms();
        const invites = rooms.filter(r => r.getMyMembership() === "invite");
        if (invites.length > 0) return invites[0];
        await new Promise(r => setTimeout(r, 500));
    }
    throw new Error("no invite arrived in " + timeoutMs + "ms");
}

async function waitForMembership(client, roomId, membership, timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const r = client.getRoom(roomId);
        if (r && r.getMyMembership() === membership) return r;
        await new Promise(res => setTimeout(res, 400));
    }
    throw new Error(`room ${roomId} never reached membership=${membership}`);
}

async function main() {
    let creds;
    if (PHASE === "phase1") {
        creds = await registerUIA("alice", "alice-pw", BOOTSTRAP_TOKEN);
        fs.writeFileSync(STATE_PATH, JSON.stringify(creds));
        log("registered", creds.userId, "device", creds.deviceId);
    } else {
        creds = JSON.parse(fs.readFileSync(STATE_PATH, "utf8"));
        log("resumed", creds.userId, "device", creds.deviceId);
    }

    const client = await makeClient(creds);
    const bobId = `@bob:localhost:6167`;

    if (PHASE === "phase1") {
        // Wait until bob has uploaded his device keys (bot.py touches /app/bob_keys_ready).
        // This ensures alice's /keys/query for bob finds his device.
        const keysFlagDeadline = Date.now() + 60000;
        while (!fs.existsSync("/app/bob_keys_ready") && Date.now() < keysFlagDeadline) {
            await new Promise(r => setTimeout(r, 500));
        }
        log("bob_keys_ready flag:", fs.existsSync("/app/bob_keys_ready"));
        const res = await client.createRoom({
            preset: "private_chat",
            visibility: "private",
            invite: [bobId],
            initial_state: [
                { type: "m.room.encryption", state_key: "", content: { algorithm: "m.megolm.v1.aes-sha2" } },
            ],
            name: "alice-bob-e2ee",
        });
        const roomId = res.room_id;
        log("created room", roomId);
        fs.writeFileSync("/app/room_id.txt", roomId);
        // Wait for bob to actually join (up to 30s)
        const deadline = Date.now() + 30000;
        while (Date.now() < deadline) {
            const r = client.getRoom(roomId);
            if (r?.getJoinedMembers().some(m => m.userId === bobId)) break;
            await new Promise(res => setTimeout(res, 500));
        }
        const bobRoom = client.getRoom(roomId);
        log("members after join:", bobRoom?.getJoinedMembers().map(m => m.userId));
        // prepareToEncrypt forces a full device list fetch for all room members.
        const room = client.getRoom(roomId);
        if (room && client.getCrypto()?.prepareToEncrypt) {
            await client.getCrypto().prepareToEncrypt(room);
        }
        const devs = await client.getCrypto()?.getUserDeviceInfo([bobId]);
        log("bob devices after prepare:", devs?.get(bobId)?.size ?? 0);
        log("members at send time:", client.getRoom(roomId)?.getJoinedMembers().map(m => m.userId));
        await client.sendTextMessage(roomId, "hello bob, from alice (phase1)");
        log("sent phase1 message");
    } else {
        const roomId = fs.readFileSync("/app/room_id.txt", "utf8").trim();
        log("resume: roomId", roomId);
        await waitForMembership(client, roomId, "join", 30000);
        // Wait for bob_v2 device keys (flag re-written by bot.py phase2 after share_keys()).
        const keysFlagDeadline2 = Date.now() + 60000;
        while (!fs.existsSync("/app/bob_keys_ready") && Date.now() < keysFlagDeadline2) {
            await new Promise(r => setTimeout(r, 500));
        }
        log("bob_keys_ready (phase2):", fs.existsSync("/app/bob_keys_ready"));
        // prepareToEncrypt ensures alice fetches BOB_V2 before encrypting.
        const room2 = client.getRoom(roomId);
        if (room2 && client.getCrypto()?.prepareToEncrypt) {
            await client.getCrypto().prepareToEncrypt(room2);
        }
        const devs2 = await client.getCrypto()?.getUserDeviceInfo([bobId]);
        log("bob devices phase2 after prepare:", devs2?.get(bobId)?.size ?? 0);
        const room = client.getRoom(roomId);
        const members = room.getJoinedMembers().map(m => m.userId);
        log("room members:", members);
        // Send phase2 message (after the bot has rotated device_id in bot.py phase2)
        await client.sendTextMessage(roomId, "hello bob, from alice (phase2) — did you get my keys?");
        log("sent phase2 message");
    }

    // Leave client running so sync can deliver to-device messages to bob.
    await new Promise(r => setTimeout(r, 15000));
    client.stopClient();
    log("done");
}

main().catch(e => { console.error("[alice] ERROR", e); process.exit(1); });
