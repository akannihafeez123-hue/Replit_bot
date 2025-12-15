import os, sys, asyncio, secrets, sqlite3, subprocess
from datetime import datetime

# ---------------- Runtime installer (keeps build light) ----------------
def ensure(pkg):
    try:
        __import__(pkg.split(">=")[0])
    except:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

for p in ["fastapi", "uvicorn", "httpx", "pydantic", "cryptography"]:
    ensure(p)

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import httpx, uvicorn
from cryptography.fernet import Fernet

# ---------------- CONFIG ----------------
GLOBAL_API_KEY = os.getenv("GLOBAL_API_KEY")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")
PORT = int(os.getenv("PORT", "8000"))

USDT_ADDRESS = "TAS8YWZH2KSwEGZ1YNmsK6XFKtz8Sg7Dey"
USDT_CONTRACT = "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN") or secrets.token_urlsafe(32)
FERNET_KEY = os.getenv("FERNET_KEY") or Fernet.generate_key().decode()
fernet = Fernet(FERNET_KEY.encode())

print("SAVE THESE:")
print("ADMIN_TOKEN:", ADMIN_TOKEN)
print("FERNET_KEY:", FERNET_KEY)

# ---------------- DATABASE ----------------
DB_FILE = "db.enc"

def load_db():
    if not os.path.exists(DB_FILE):
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.executescript("""
        CREATE TABLE subkeys(key TEXT PRIMARY KEY, tier TEXT, used INT, day TEXT);
        CREATE TABLE payments(txid TEXT PRIMARY KEY, amount REAL, sender TEXT, time TEXT);
        """)
        return db
    data = fernet.decrypt(open(DB_FILE, "rb").read())
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript(data.decode())
    return db

db = load_db()

def save_db():
    dump = "\n".join(db.iterdump()).encode()
    open(DB_FILE, "wb").write(fernet.encrypt(dump))

# ---------------- PRICING ----------------
TIERS = {
    "starter": {"price": 3, "quota": 100_000},
    "pro": {"price": 9, "quota": 500_000},
    "unlim": {"price": 39, "quota": 5_000_000},
}

def tier_from_amount(amount):
    if amount >= 39: return "unlim"
    if amount >= 9: return "pro"
    return "starter"

# ---------------- TELEGRAM ----------------
def tg(msg):
    if not TG_TOKEN or not TG_CHAT: return
    httpx.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": msg},
        timeout=5
    )

# ---------------- FASTAPI ----------------
app = FastAPI(title="Global AI Income Engine")

class ContentReq(BaseModel):
    topic: str
    type: str

@app.post("/proxy")
async def proxy(req: ContentReq, x_subkey: str = Header(None)):
    cur = db.cursor()
    row = cur.execute(
        "SELECT tier, used, day FROM subkeys WHERE key=?",
        (x_subkey,)
    ).fetchone()

    if not row:
        raise HTTPException(401, "Invalid subkey")

    tier, used, day = row
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if day != today:
        used = 0

    if used > TIERS[tier]["quota"]:
        raise HTTPException(402, "Quota exceeded")

    prompt = f"Write {req.type} content about {req.topic}"
    r = await httpx.AsyncClient().post(
        "https://api.openai.com/v1/completions",
        headers={"Authorization": f"Bearer {GLOBAL_API_KEY}"},
        json={"model": "text-davinci-003", "prompt": prompt, "max_tokens": 500}
    )

    text = r.json()["choices"][0]["text"]
    cur.execute(
        "UPDATE subkeys SET used=?, day=? WHERE key=?",
        (used + len(text), today, x_subkey)
    )
    save_db()
    return {"content": text}

# ---------------- REAL USDT PAYMENT SCANNER ----------------
async def scan_tron_payments():
    await asyncio.sleep(10)
    headers = {"TRON-PRO-API-KEY": TRONGRID_API_KEY}

    while True:
        try:
            url = f"https://api.trongrid.io/v1/accounts/{USDT_ADDRESS}/transactions/trc20"
            r = await httpx.AsyncClient().get(url, headers=headers, timeout=15)
            for tx in r.json().get("data", []):
                if tx["token_info"]["symbol"] != "USDT":
                    continue
                if tx["to"] != USDT_ADDRESS:
                    continue

                txid = tx["transaction_id"]
                amount = int(tx["value"]) / 1_000_000
                sender = tx["from"]

                if db.execute("SELECT 1 FROM payments WHERE txid=?", (txid,)).fetchone():
                    continue

                tier = tier_from_amount(amount)
                subkey = secrets.token_urlsafe(24)

                db.execute("INSERT INTO subkeys VALUES (?,?,0,'')", (subkey, tier))
                db.execute(
                    "INSERT INTO payments VALUES (?,?,?,?)",
                    (txid, amount, sender, str(datetime.utcnow()))
                )
                save_db()

                tg(
                    f"✅ USDT Payment Confirmed\n"
                    f"Amount: ${amount}\n"
                    f"Tier: {tier}\n"
                    f"Sender: {sender}\n"
                    f"Subkey: {subkey}"
                )

        except Exception as e:
            print("TRON scan error:", e)

        await asyncio.sleep(60)

# ---------------- AI MARKETING LOOP ----------------
async def marketing():
    await asyncio.sleep(5)
    offers = [
        "AI Blogs & SEO – Starter $3",
        "Unlimited AI Content for Businesses",
        "No writers. No delay. Instant content."
    ]
    i = 0
    while True:
        tg(
            f"🤖 AI Content Service\n"
            f"{offers[i % len(offers)]}\n"
            f"Pay via USDT (TRC20)\n{USDT_ADDRESS}"
        )
        i += 1
        await asyncio.sleep(6 * 60 * 60)

@app.on_event("startup")
async def start():
    asyncio.create_task(marketing())
    asyncio.create_task(scan_tron_payments())

# ---------------- RUN ----------------
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT
