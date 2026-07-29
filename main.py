import io
import os
from pathlib import Path

import pandas as pd
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="KAU FCITR Assistant")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

API_SECRET_KEY = os.getenv("MY_API_KEY")
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
DATA_KEY = os.getenv("DATA_KEY")

for name, value in [
    ("MY_API_KEY", API_SECRET_KEY),
    ("TOGETHER_API_KEY", TOGETHER_API_KEY),
    ("DATA_KEY", DATA_KEY),
]:
    if not value:
        raise RuntimeError(f"{name} not found in .env")

client = OpenAI(api_key=TOGETHER_API_KEY, base_url="https://api.together.xyz/v1")
cipher = Fernet(DATA_KEY.encode())

ENCRYPTED_FILES = [
    "KAU_Computer_Courses_QA.xlsx.enc",
    "whatsapp_chat_dataset_v3.xlsx.enc",
]


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


class Query(BaseModel):
    question: str


@app.get("/")
async def root():
    return {"status": "ok", "data_loaded": context_data != "لا توجد بيانات."}


@app.post("/chat", dependencies=[Depends(verify_token)])
async def chat_endpoint(query: Query):
    try:
        response = client.chat.completions.create(
            model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "أنت مساعد ذكي لطلاب كلية الحاسبات. "
                        "مهمتك الإجابة بناءً على النص المرفق أدناه فقط وحرفياً. "
                        "يمنع منعاً باتاً تخمين أسماء المواد أو تأليف أي معلومة من خارج النص. "
                        "إذا لم تكن المعلومة واضحة في النص، اعتذر وقل أن المعلومة غير متوفرة.\n\n"
                        f"البيانات المتاحة:\n{context_data}"
                    ),
                },
                {"role": "user", "content": query.question},
            ],
            max_tokens=500,
            temperature=0.2,
        )
        return {"answer": response.choices[0].message.content}
    except Exception as e:
        print("CHAT ERROR:", type(e).__name__, e)
        raise HTTPException(status_code=500, detail="حدث خطأ في المعالجة")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))