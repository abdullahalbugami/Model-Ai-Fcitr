"""
تشفير ملفات البيانات - يُشغَّل محلياً فقط.
يكتشف تلقائياً أي ملف xlsx أو csv في مجلد المشروع أو في data.

    python encrypt_data.py --list       عرض الملفات التي ستُشفَّر
    python encrypt_data.py --encrypt    تشفير الكل
    python encrypt_data.py --verify     اختبار فك التشفير وعدّ الصفوف
    python encrypt_data.py --genkey     توليد مفتاح جديد (مرة واحدة فقط)
"""

import os
import sys
import io
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
SOURCE_EXT = {".xlsx", ".xls", ".csv"}


def sources() -> list[Path]:
    """كل ملفات البيانات في جذر المشروع وفي مجلد data."""
    found = []
    for folder in (BASE, DATA):
        if not folder.exists():
            continue
        for p in sorted(folder.iterdir()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in SOURCE_EXT:
                continue
            if p.name.startswith("~$") or p.name.startswith("."):
                continue
            found.append(p)
    return found


def cipher() -> Fernet:
    key = os.getenv("DATA_KEY")
    if not key:
        sys.exit("DATA_KEY not in .env")
    return Fernet(key.encode())


def genkey():
    print("\nDATA_KEY=" + Fernet.generate_key().decode())
    print("\nWARNING: changing the key requires re-encrypting all files\n")


def list_files():
    files = sources()
    if not files:
        print("No data files found.")
        return
    print(f"\n{len(files)} file(s) will be encrypted:\n")
    for p in files:
        print(f"  {p.name:<45} {p.stat().st_size / 1024:>8.1f} KB")
    print()


def encrypt():
    c = cipher()
    DATA.mkdir(exist_ok=True)
    files = sources()
    if not files:
        sys.exit("No data files found.")
    for src in files:
        dst = DATA / (src.name + ".enc")
        dst.write_bytes(c.encrypt(src.read_bytes()))
        print(f"OK: {src.name}  ->  data/{dst.name}")
    print(f"\n{len(files)} file(s) encrypted.\n")


def verify():
    import pandas as pd

    c = cipher()
    enc_files = sorted(DATA.glob("*.enc"))
    if not enc_files:
        sys.exit("No .enc files in data/")
    total = 0
    for p in enc_files:
        try:
            raw = c.decrypt(p.read_bytes())
            if p.name.lower().endswith(".csv.enc"):
                df = pd.read_csv(io.BytesIO(raw))
            else:
                df = pd.read_excel(io.BytesIO(raw))
            total += len(df)
            print(f"OK: {p.name:<45} rows: {len(df):>5}")
        except Exception as e:
            print(f"FAIL: {p.name} -> {type(e).__name__}: {e}")
    print(f"\nTotal rows: {total}\n")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--genkey":
        genkey()
    elif arg == "--list":
        list_files()
    elif arg == "--encrypt":
        encrypt()
    elif arg == "--verify":
        verify()
    else:
        print(__doc__)