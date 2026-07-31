import io
import os
from pathlib import Path

import httpx
import pandas as pd
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="KAU FCITR Assistant")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

API_SECRET_KEY = os.getenv("MY_API_KEY")
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
DATA_KEY = os.getenv("DATA_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_SECRET = os.getenv("TELEGRAM_SECRET")

for name, value in [
    ("MY_API_KEY", API_SECRET_KEY),
    ("TOGETHER_API_KEY", TOGETHER_API_KEY),
    ("DATA_KEY", DATA_KEY),
]:
    if not value:
        raise RuntimeError(f"{name} not found in environment")

client = OpenAI(api_key=TOGETHER_API_KEY, base_url="https://api.together.xyz/v1")
cipher = Fernet(DATA_KEY.encode())
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

ENCRYPTED_FILES = [
    "KAU_Computer_Courses_QA.xlsx.enc",
    "whatsapp_chat_dataset_v3.xlsx.enc",
]

SYSTEM_PROMPT_HEAD = (
    "أنت مساعد ذكي لطلاب كلية الحاسبات. "
    "مهمتك الإجابة بناءً على النص المرفق أدناه فقط وحرفياً. "
    "يمنع منعاً باتاً تخمين أسماء المواد أو تأليف أي معلومة من خارج النص. "
    "إذا لم تكن المعلومة واضحة في النص، اعتذر وقل أن المعلومة غير متوفرة.\n\n"
)


async def verify_token(x_api_key: str = Header(...)):
    if x_api_key != API_SECRET_KEY:
        raise HTTPException(status_code=403, detail="غير مصرح لك بالوصول")
    return x_api_key


def load_college_data(max_rows: int = 300) -> str:
    """يفك التشفير في الذاكرة فقط - لا يُكتب أي ملف واضح على القرص."""
    all_text = ""
    for name in ENCRYPTED_FILES:
        path = DATA_DIR / name
        if not path.exists():
            print("NOT FOUND:", name)
            continue
        try:
            raw = cipher.decrypt(path.read_bytes())
            df = pd.read_excel(io.BytesIO(raw)).head(max_rows)
            label = name.replace(".enc", "")
            all_text += f"\n--- بيانات ملف: {label} ---\n{df.to_csv(index=False)}\n"
            print("DECRYPTED:", name, "rows:", len(df))
        except InvalidToken:
            print("BAD KEY or corrupt file:", name)
        except Exception as e:
            print("ERROR:", name, type(e).__name__, e)
    return all_text if all_text else "لا توجد بيانات."


context_data = load_college_data()


def ask_model(question: str) -> str:
    """الدالة المشتركة بين واجهة API وبوت تيليجرام."""
    response = client.chat.completions.create(
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT_HEAD + f"البيانات المتاحة:\n{context_data}",
            },
            {"role": "user", "content": question},
        ],
        max_tokens=500,
        temperature=0.2,
    )
    return response.choices[0].message.content


class Query(BaseModel):
    question: str


@app.get("/")
async def root():
    return {
        "status": "ok",
        "data_loaded": context_data != "لا توجد بيانات.",
        "telegram": bool(TELEGRAM_TOKEN),
    }


@app.post("/chat", dependencies=[Depends(verify_token)])
async def chat_endpoint(query: Query):
    try:
        return {"answer": ask_model(query.question)}
    except Exception as e:
        print("CHAT ERROR:", type(e).__name__, e)
        raise HTTPException(status_code=500, detail="حدث خطأ في المعالجة")


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(None),
):
    # تيليجرام يرسل كلمة السر في هذا الهيدر - نرفض أي طلب مزوّر
    if TELEGRAM_SECRET and x_telegram_bot_api_secret_token != TELEGRAM_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    if not text:
        return {"ok": True}

    if text.startswith("/start"):
        answer = "أهلاً بك! اسألني عن مواد ومدرّسي كلية الحاسبات وسأجيبك من البيانات المتاحة."
    else:
        try:
            answer = ask_model(text)
        except Exception as e:
            print("TG ERROR:", type(e).__name__, e)
            answer = "حدث خطأ مؤقت، حاول مرة أخرى."

    async with httpx.AsyncClient(timeout=30) as http:
        await http.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": chat_id, "text": answer[:4000]},
        )

    # نرجع 200 دائماً حتى لا يعيد تيليجرام الإرسال بلا نهاية
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))