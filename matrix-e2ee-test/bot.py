"""Bot side of the Element-vs-bot E2EE repro.

Uses mautrix (same crypto stack as hermes-agent staging). Runs in phases:
  phase1 — register bob, open mautrix client with device BOB, upload keys,
           accept alice's invite + join, listen for her message and report
           whether decryption succeeded.
  phase2 — NUKE the crypto store, login with a fresh device_id (BOB_V2),
           re-upload fresh keys. Then listen for alice's phase2 message.

Passes iff bob can decrypt alice's message after the device rotation.
"""
import asyncio, os, sys, time, json, urllib.request, urllib.error, shutil
from pathlib import Path

from mautrix.api import HTTPAPI
from mautrix.client import Client, InternalEventType as IntEvt
from mautrix.client.state_store import MemoryStateStore, MemorySyncStore
from mautrix.types import UserID, RoomID, EventType, MessageType, TextMessageEventContent
from mautrix.crypto import OlmMachine
from mautrix.crypto.store.asyncpg import PgCryptoStore
from mautrix.util.async_db import Database

HS = os.environ.get("HOMESERVER", "http://127.0.0.1:16167")
BOOTSTRAP_TOKEN = os.environ.get("BOOTSTRAP_TOKEN", "")
REG_TOKEN = "repro-token"
PHASE = os.environ.get("PHASE", "phase1")
STATE = Path("/app/bob.state.json")
CRYPTO_DB = Path("/app/bob_crypto.db")

def _post(url, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

def register(username, password, regToken, device_id):
    try:
        _post(f"{HS}/_matrix/client/v3/register",
              {"username": username, "password": password, "auth": {"type": "m.login.dummy"}})
    except urllib.error.HTTPError as e:
        session = json.loads(e.read())["session"]
    resp = _post(f"{HS}/_matrix/client/v3/register", {
        "username": username, "password": password,
        "auth": {"type": "m.login.registration_token", "token": regToken, "session": session},
        "device_id": device_id,
    })
    return resp["user_id"], resp["access_token"]

def login(username, password, device_id):
    return _post(f"{HS}/_matrix/client/v3/login", {
        "type": "m.login.password",
        "identifier": {"type": "m.id.user", "user": username},
        "password": password,
        "device_id": device_id,
        "initial_device_display_name": device_id,
    })

class _CryptoStateStore:
    def __init__(self, inner, joined): self._inner, self._joined = inner, joined
    async def is_encrypted(self, room_id): return (await self.get_encryption_info(room_id)) is not None
    async def get_encryption_info(self, room_id):
        if hasattr(self._inner, "get_encryption_info"):
            return await self._inner.get_encryption_info(room_id)
        return None
    async def find_shared_rooms(self, user_id): return list(self._joined)

async def make_client(user_id, token, device_id):
    api = HTTPAPI(base_url=HS, token=token)
    state = MemoryStateStore()
    sync = MemorySyncStore()
    client = Client(mxid=UserID(user_id), device_id=device_id, api=api,
                    state_store=state, sync_store=sync)
    crypto_db = Database.create(f"sqlite:///{CRYPTO_DB}", upgrade_table=PgCryptoStore.upgrade_table)
    await crypto_db.start()
    crypto_store = PgCryptoStore(account_id=user_id, pickle_key=f"{user_id}:{device_id}", db=crypto_db)
    await crypto_store.open()
    joined = set()
    olm = OlmMachine(client, crypto_store, _CryptoStateStore(state, joined))
    from mautrix.types import TrustState
    olm.share_keys_min_trust = TrustState.UNVERIFIED
    olm.send_keys_min_trust = TrustState.UNVERIFIED
    await olm.load()
    client.crypto = olm
    return client, joined, crypto_db

async def sync_once(client, joined, label, timeout=3000):
    data = await client.sync(timeout=timeout, full_state=(label == "init"))
    if isinstance(data, dict):
        rooms_join = data.get("rooms", {}).get("join", {})
        joined.clear(); joined.update(rooms_join.keys())
        nb = data.get("next_batch")
        if nb: await client.sync_store.put_next_batch(nb)
        tasks = client.handle_sync(data)
        if tasks: await asyncio.gather(*tasks)
        invites = data.get("rooms", {}).get("invite", {})
        return invites
    return {}

async def main():
    # Wait for homeserver
    for _ in range(60):
        try:
            urllib.request.urlopen(f"{HS}/_matrix/client/versions", timeout=2).read()
            break
        except Exception: time.sleep(1)
    print(f"[bob] homeserver ready ({HS})")

    if PHASE == "phase1":
        # Register bob. Uses the configured REG_TOKEN. Alice registers first
        # with the one-shot BOOTSTRAP_TOKEN so REG_TOKEN is already unlocked.
        user_id, token = register("bob", "bob-pw", REG_TOKEN, "BOB")
        device_id = "BOB"
        STATE.write_text(json.dumps({"user_id": user_id, "token": token, "device_id": device_id, "password": "bob-pw"}))
        print(f"[bob] registered {user_id} device={device_id}")
    else:
        # Phase 2: NUKE crypto store, log in with a NEW device_id.
        saved = json.loads(STATE.read_text())
        user_id = saved["user_id"]
        new_device = "BOB_V2"
        for p in [CRYPTO_DB, Path(str(CRYPTO_DB) + "-shm"), Path(str(CRYPTO_DB) + "-wal")]:
            if p.exists():
                p.unlink()
                print(f"[bob] nuked {p}")
        r = login("bob", saved["password"], new_device)
        token = r["access_token"]; device_id = r["device_id"]
        STATE.write_text(json.dumps({"user_id": user_id, "token": token, "device_id": device_id, "password": "bob-pw"}))
        print(f"[bob] phase2: new device {device_id}")

    client, joined, crypto_db = await make_client(user_id, token, device_id)
    print(f"[bob] client up, identity_key={client.crypto.account.identity_key[:20]}...")
    # upload device keys
    await client.crypto.share_keys()
    print(f"[bob] device keys uploaded")
    Path("/app/bob_keys_ready").touch()

    # Watch for decrypt outcomes
    decrypted = []
    could_not_decrypt = []
    async def on_msg(evt):
        if evt.sender == user_id: return
        body = getattr(evt.content, "body", str(evt.content))
        decrypted.append((evt.event_id, evt.sender, body))
        print(f"[bob] DECRYPTED {evt.event_id} from {evt.sender}: {body!r}")
    async def on_enc(evt):
        if evt.sender == user_id: return
        could_not_decrypt.append(evt.event_id)
        print(f"[bob] COULD NOT DECRYPT {evt.event_id} from {evt.sender}")
    client.add_event_handler(EventType.ROOM_MESSAGE, on_msg)
    client.add_event_handler(EventType.ROOM_ENCRYPTED, on_enc)

    # Initial sync — pick up invite if any.
    invites = await sync_once(client, joined, "init", timeout=3000)

    # Auto-accept any invites (in phase1 alice will invite us; phase2 we're
    # already joined so this is a no-op).
    for rid in invites.keys():
        try:
            await client.api.request("POST", f"/_matrix/client/v3/rooms/{rid}/join", content={})
            print(f"[bob] joined invite room {rid}")
        except Exception as e:
            print(f"[bob] join failed: {e}")

    # Sync loop: wait up to 45s for alice's message; also accept late invites.
    deadline = time.time() + 45
    while time.time() < deadline:
        if decrypted: break
        new_invites = await sync_once(client, joined, "poll", timeout=3000)
        for rid in new_invites.keys():
            try:
                await client.api.request("POST", f"/_matrix/client/v3/rooms/{rid}/join", content={})
                print(f"[bob] joined late invite {rid}")
            except Exception as e:
                print(f"[bob] late join failed: {e}")
        await asyncio.sleep(0.2)

    await client.api.session.close()
    await crypto_db.stop()

    print()
    print(f"[bob] === {PHASE} RESULT ===")
    print(f"[bob] decrypted: {len(decrypted)}")
    print(f"[bob] decrypt failures: {len(could_not_decrypt)}")
    if decrypted:
        print("[bob] PASS")
        sys.exit(0)
    else:
        print("[bob] FAIL")
        sys.exit(1)

asyncio.run(main())
