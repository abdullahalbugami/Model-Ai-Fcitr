import io
import os
import re
from pathlib import Path

import httpx
import pandas as pd
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="KAU FCITR Assistant")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# يقبل عدة مفاتيح مفصولة بفاصلة
API_KEYS = {k.strip() for k in os.getenv("MY_API_KEY", "").split(",") if k.strip()}
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
DATA_KEY = os.getenv("DATA_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_SECRET = os.getenv("TELEGRAM_SECRET")

if not API_KEYS:
    raise RuntimeError("MY_API_KEY not found in environment")
if not TOGETHER_API_KEY:
    raise RuntimeError("TOGETHER_API_KEY not found in environment")
if not DATA_KEY:
    raise RuntimeError("DATA_KEY not found in environment")

# يسمح لأي موقع بالاتصال من المتصفح
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

client = OpenAI(api_key=TOGETHER_API_KEY, base_url="https://api.together.xyz/v1")
cipher = Fernet(DATA_KEY.encode())
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

NO_DATA_REPLY = "هذه المعلومة غير متوفرة في بياناتي. راجع الإرشاد الأكاديمي أو مدرّس المادة."

SYSTEM_PROMPT = (
    "# هويتك\n"
    "أنت مساعد إرشادي لطلاب كلية الحاسبات وتقنية المعلومات بجامعة الملك عبدالعزيز.\n"
    "مهمتك الوحيدة: عرض ما ورد في «المقاطع المسترجعة» أدناه عن المواد وأعضاء "
    "هيئة التدريس وأبرز التجميعات وخبرات الطلاب السابقين.\n\n"
    "# المصدر الوحيد\n"
    "«المقاطع المسترجعة» أدناه هي مصدرك الوحيد والحصري.\n"
    "معرفتك العامة معطّلة تماماً في هذه المحادثة. تعامل مع أي معلومة خارج هذه "
    "المقاطع كأنك لا تعرفها إطلاقاً، مهما بدت بديهية أو بسيطة.\n\n"
    "# ممنوع منعاً باتاً\n"
    "1. حل الواجبات أو الاختبارات أو الكويزات أو أسئلة الاختيار من متعدد.\n"
    "2. كتابة أو تصحيح أو شرح كود برمجي.\n"
    "3. شرح المفاهيم الدراسية والنظرية (خوارزميات، قواعد بيانات، رياضيات، pandas...).\n"
    "4. الإجابة عن أسئلة عامة خارج نطاق الكلية.\n"
    "5. تخمين المواعيد أو الدرجات أو المتطلبات أو أسماء المواد غير الواردة.\n"
    "6. الربط بين معلومتين لتوليد معلومة ثالثة غير مذكورة صراحة.\n\n"
    "# ردودك الجاهزة\n"
    "إذا طُلب منك حل واجب أو اختبار أو مسألة:\n"
    "«لا أحل الواجبات ولا الاختبارات. يمكنني عرض تجميعات وخبرات الطلاب السابقين "
    "عن هذه المادة إن كانت متوفرة لدي.»\n\n"
    "إذا لم تجد الإجابة في المقاطع المسترجعة:\n"
    f"«{NO_DATA_REPLY}»\n\n"
    "إذا كان السؤال خارج نطاق الكلية:\n"
    "«تخصصي محصور في إرشاد طلاب كلية الحاسبات بجامعة الملك عبدالعزيز.»\n\n"
    "# طريقة الرد\n"
    "- بالعربية دائماً، ولو كان السؤال بالإنجليزية.\n"
    "- انقل ما في المقاطع كما هو دون إضافة أو توسّع.\n"
    "- عند عرض التجميعات، نبّه أنها خبرات سابقة وقد تكون تغيّرت.\n"
    "- موجز ومباشر بلا مقدمات.\n\n"
    "# قبل كل رد\n"
    "اسأل نفسك: هل هذه المعلومة مكتوبة حرفياً في المقاطع أدناه؟\n"
    "إن كان الجواب لا — استخدم الرد الجاهز ولا تجب من عندك.\n"
)

DIACRITICS = re.compile(r"[ً-ْ]")
NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def normalize_ar(text) -> str:
    """توحيد شكل النص العربي حتى تتطابق الكلمات رغم اختلاف الهمزات والتشكيل."""
    s = str(text)
    s = DIACRITICS.sub("", s)
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    s = NON_WORD.sub(" ", s)
    return " ".join(s.lower().split())


def load_rows() -> list[tuple[str, str]]:
    """يفك تشفير كل ملف .enc في data ويحوّل صفوفه إلى نص قابل للبحث."""
    rows: list[tuple[str, str]] = []
    for path in sorted(DATA_DIR.glob("*.enc")):
        name = path.name
        try:
            raw = cipher.decrypt(path.read_bytes())
            if name.lower().endswith(".csv.enc"):
                df = pd.read_csv(io.BytesIO(raw))
            else:
                df = pd.read_excel(io.BytesIO(raw))
            label = name.replace(".enc", "")
            for _, r in df.iterrows():
                parts = [f"{c}: {r[c]}" for c in df.columns if pd.notna(r[c])]
                if not parts:
                    continue
                original = " | ".join(parts)
                rows.append((normalize_ar(original), f"[{label}] {original}"))
            print("DECRYPTED:", name, "rows:", len(df))
        except InvalidToken:
            print("BAD KEY or corrupt file:", name)
        except Exception as e:
            print("ERROR:", name, type(e).__name__, e)
    return rows


ROWS = load_rows()
print("TOTAL SEARCHABLE ROWS:", len(ROWS))

STOPWORDS = {
    "من", "ما", "هو", "هي", "في", "على", "عن", "الى", "الي", "هل", "كيف",
    "وش", "ايش", "متى", "اين", "مين", "كم", "لي", "لك", "انا", "هذا", "هذه",
    "الذي", "التي", "مع", "او", "و", "ثم", "بس", "يا", "ال",
    "the", "is", "of", "a", "an", "to", "for", "what", "who", "how", "in",
}


def retrieve(question: str, k: int = 20) -> str:
    """يبحث في كل الصفوف ويرجع الأكثر صلة بالسؤال فقط."""
    q_tokens = {
        t for t in normalize_ar(question).split()
        if len(t) > 1 and t not in STOPWORDS
    }
    if not q_tokens:
        return ""

    scored = []
    for norm, original in ROWS:
        score = sum(1 for t in q_tokens if t in norm)
        if score:
            scored.append((score, original))

    if not scored:
        return ""

    scored.sort(key=lambda x: -x[0])
    return "\n\n".join(original for _, original in scored[:k])


def ask_model(question: str) -> str:
    """يسترجع أولاً؛ وإن لم يجد شيئاً يرفض دون استدعاء النموذج إطلاقاً."""
    context = retrieve(question)
    if not context:
        return NO_DATA_REPLY

    response = client.chat.completions.create(
        model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        messages=[
            {
                "role": "system",
                "content": f"{SYSTEM_PROMPT}\n# المقاطع المسترجعة\n{context}",
            },
            {"role": "user", "content": question},
        ],
        max_tokens=500,
        temperature=0.1,
    )
    return response.choices[0].message.content


class Query(BaseModel):
    question: str


async def verify_token(x_api_key: str = Header(...)):
    if x_api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="غير مصرح لك بالوصول")
    return x_api_key


@app.get("/")
async def root():
    return {
        "status": "ok",
        "rows_indexed": len(ROWS),
        "files": [p.name.replace(".enc", "") for p in sorted(DATA_DIR.glob("*.enc"))],
        "telegram": bool(TELEGRAM_TOKEN),
    }


@app.post("/chat", dependencies=[Depends(verify_token)])
async def chat_endpoint(query: Query):
    question = query.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="السؤال فارغ")
    try:
        return {"answer": ask_model(question)}
    except Exception as e:
        print("CHAT ERROR:", type(e).__name__, e)
        raise HTTPException(status_code=500, detail="حدث خطأ في المعالجة")


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(None),
):
    if TELEGRAM_SECRET and x_telegram_bot_api_secret_token != TELEGRAM_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]
    text = (message.get("text") or message.get("caption") or "").strip()

    async with httpx.AsyncClient(timeout=30) as http:
        if not text:
            await http.post(
                f"{TG_API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "أقرأ النصوص فقط. اكتب سؤالك كتابةً من فضلك.",
                },
            )
            return {"ok": True}

        await http.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
        )

        if text.startswith("/start"):
            answer = (
                "أهلاً بك! أنا مساعد إرشادي لطلاب كلية الحاسبات.\n"
                "اسألني عن المواد وأعضاء هيئة التدريس وتجميعات الطلاب السابقين.\n"
                "لا أحل الواجبات ولا الاختبارات."
            )
        else:
            try:
                answer = ask_model(text)
            except Exception as e:
                print("TG ERROR:", type(e).__name__, e)
                answer = "حدث خطأ مؤقت، حاول مرة أخرى."

        await http.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": chat_id, "text": answer[:4000]},
        )

    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))