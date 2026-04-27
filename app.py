from flask import Flask, render_template, request, redirect
import sqlite3
import re
import json
import os
from werkzeug.middleware.proxy_fix import ProxyFix
from openai import OpenAI

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_proto=1, x_host=1)

# -----------------------
# APIキー
# -----------------------
client = OpenAI()
DB_NAME = "/home/bitnami/eng_app/conversation.db"

# -----------------------
# 辞書
# -----------------------
DICT_DIR = "/home/bitnami/eng_app/dict"

def load_json(path):
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {}

PHRASE_DICT = load_json(f"{DICT_DIR}/phrase.json")
TRANSLATE_DICT = load_json(f"{DICT_DIR}/translate.json")

# -----------------------
# 正規化
# -----------------------
def normalize(text):
    text = text.lower()
    text = text.replace("’", "'")
    text = re.sub(r"[.,!?]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# -----------------------
# DB
# -----------------------
def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("PRAGMA journal_mode=WAL;")

    conn.execute("""
    CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER,
        speaker TEXT,
        text TEXT,
        japanese TEXT,
        kana TEXT,
        kana_native TEXT
    )
    """)

    conn.commit()
    conn.close()

# -----------------------
# 発音チューニング（最終版）
# -----------------------
def tune_katakana(text):

    text = text.replace(" ア ", "ァ ")
    text = text.replace(" ア", "ァ")

    text = text.replace("ライク ア", "ライクァ")
    text = text.replace("ハヴ ア", "ハヴァ")

    text = text.replace("ドゥ ユー", "ジュー")
    text = text.replace("ドゥユー", "ジュー")

    text = text.replace("ドゥント", "ドント")

    text = text.replace("ワット ドゥ ユー", "ワッドゥユー")
    text = text.replace("ワット ジュー", "ワッドゥユー")

    # ★今回の最重要修正
    text = text.replace("ワッジュー", "ワッドゥユー")

    text = text.replace("アイ ウッド ライク", "アウッライク")

    text = text.replace("レザベーション", "リザベーション")
    text = text.replace("レコメンド", "レコメン")

    text = text.replace("アウッ ライク", "アウッライク")
    text = text.replace("アウッ ライクァ", "アウッライクァ")

    return re.sub(r"\s+", " ", text).strip()

# -----------------------
# カタカナ（通常）
# -----------------------
def to_katakana(text):

    norm = normalize(text)

    # ★長いフレーズ優先（重要）
    for k, v in sorted(PHRASE_DICT.items(), key=lambda x: -len(x[0])):
        if normalize(k) in norm:
            return tune_katakana(v)

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"{text}\nカタカナのみ。1行。説明禁止。"
            }],
            temperature=0.2
        )

        result = res.choices[0].message.content.strip()
        result = result.split("\n")[0]
        result = re.sub(r"[「」\"。]", "", result)

        return tune_katakana(result)

    except:
        return text

# -----------------------
# ネイティブ
# -----------------------
def to_katakana_native(text):

    norm = normalize(text)

    # ★長いフレーズ優先（重要）
    for k, v in sorted(PHRASE_DICT.items(), key=lambda x: -len(x[0])):
        if normalize(k) in norm:
            return tune_katakana(v)

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"""
{text}

英語の音だけをカタカナにする

ルール：
・意味を変えない
・単語追加禁止
・説明禁止
・カタカナのみ
・1行
"""
            }],
            temperature=0
        )

        result = res.choices[0].message.content.strip()
        result = result.split("\n")[0]
        result = re.sub(r"[「」\"。]", "", result)
        result = re.sub(r"→.*", "", result).strip()

        if re.search(r"[a-zA-Z]", result):
            return to_katakana(text)

        if len(result) < 5:
            return to_katakana(text)

        return tune_katakana(result)

    except:
        return to_katakana(text)

# -----------------------
# 翻訳
# -----------------------
def translate(text):
    key = normalize(text)

    for k, v in TRANSLATE_DICT.items():
        if normalize(k) == key:
            return v

    if "do you have a reservation" in key:
        return "予約はありますか？"

    if "what do you recommend" in key:
        return "何をおすすめしますか？"

    if "no we don't" in key or "no we dont" in key:
        return "いいえ、ありません"

    if "i would like a steak" in key:
        return "ステーキをお願いします"

    if "i would like" in key:
        return "〜をお願いします"

    return text

# -----------------------
# ルート
# -----------------------
@app.route("/")
@app.route("/eng/")
def index():
    conn = get_db()
    data = conn.execute("SELECT * FROM conversations ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("index.html", data=data)

@app.route("/add_multi", methods=["GET","POST"])
@app.route("/eng/add_multi", methods=["GET","POST"])
def add_multi():

    if request.method == "POST":

        title = request.form.get("title","").strip()
        lines_raw = request.form.get("lines","").strip()

        if not title or not lines_raw:
            return redirect("/eng/add_multi")

        lines = lines_raw.split("\n")

        texts = []
        speakers = []
        speaker_toggle = "A"

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("A:"):
                speakers.append("A")
                texts.append(line[2:].strip())
            elif line.startswith("B:"):
                speakers.append("B")
                texts.append(line[2:].strip())
            else:
                speakers.append(speaker_toggle)
                texts.append(line)
                speaker_toggle = "B" if speaker_toggle == "A" else "A"

        conn = get_db()
        cur = conn.execute("INSERT INTO conversations (title) VALUES (?)",(title,))
        conv_id = cur.lastrowid

        for i in range(len(texts)):
            text = texts[i]

            jp = translate(text)
            kana = to_katakana(text)
            kana_native = to_katakana_native(text)

            conn.execute("""
            INSERT INTO messages
            (conversation_id,speaker,text,japanese,kana,kana_native)
            VALUES (?,?,?,?,?,?)
            """,(conv_id,speakers[i],text,jp,kana,kana_native))

        conn.commit()
        conn.close()

        return redirect("/eng/")

    return render_template("add_multi.html")

@app.route("/detail_multi/<int:id>")
@app.route("/eng/detail_multi/<int:id>")
def detail_multi(id):
    conn = get_db()
    conv = conn.execute("SELECT * FROM conversations WHERE id=?",(id,)).fetchone()

    if conv is None:
        return "Not Found", 404

    messages = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY id",(id,)
    ).fetchall()
    conn.close()

    return render_template("detail_multi.html",conv=conv,messages=messages)

@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    conn = get_db()
    conn.execute("DELETE FROM conversations WHERE id=?", (id,))
    conn.execute("DELETE FROM messages WHERE conversation_id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/eng/")

# -----------------------
# Jinja登録
# -----------------------
app.jinja_env.globals.update(
    to_katakana=to_katakana,
    to_katakana_native=to_katakana_native
)

# -----------------------
# 起動
# -----------------------
if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0",port=5005)