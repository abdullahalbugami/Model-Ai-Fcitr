import os
import sys
import io
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).resolve().parent
OUT = BASE / "data"
NAMES = ["KAU_Computer_Courses_QA.xlsx", "whatsapp_chat_dataset_v3.xlsx"]


def find(name):
    for p in (BASE / name, BASE / "data" / name):
        if p.exists():
            return p
    return None


def cipher():
    key = os.getenv("DATA_KEY")
    if not key:
        sys.exit("DATA_KEY not in .env  ->  run: python encrypt_data.py --genkey")
    return Fernet(key.encode())


def genkey():
    print("\nDATA_KEY=" + Fernet.generate_key().decode())
    print("\nCopy the line above into your .env file\n")


def encrypt():
    c = cipher()
    OUT.mkdir(exist_ok=True)
    for n in NAMES:
        src = find(n)
        if not src:
            print("NOT FOUND:", n)
            continue
        dst = OUT / (n + ".enc")
        dst.write_bytes(c.encrypt(src.read_bytes()))
        print("OK:", src.name, "->", "data/" + dst.name)


def verify():
    import pandas as pd

    c = cipher()
    for n in NAMES:
        p = OUT / (n + ".enc")
        if not p.exists():
            print("NOT FOUND:", p.name)
            continue
        df = pd.read_excel(io.BytesIO(c.decrypt(p.read_bytes())))
        print("OK:", p.name, "rows:", len(df))


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else ""
    if a == "--genkey":
        genkey()
    elif a == "--encrypt":
        encrypt()
    elif a == "--verify":
        verify()
    else:
        print("usage: --genkey | --encrypt | --verify")