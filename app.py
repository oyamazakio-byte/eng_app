from flask import Flask, render_template, request, redirect
import sqlite3
import re
import json
import os
from werkzeug.middleware.proxy_fix import ProxyFix
from openai import OpenAI

app = Flask(__name__)

# Proxy対応
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_proto=1, x_host=1)

# -----------------------
# APIキー
# -----------------------
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None

DB_NAME = "/home/bitnami/eng_app/conversation.db"

# -----------------------
# 辞書読み込み
# -----------------------
DICT_DIR = "/home/bitnami/eng_app/dict"

def load_json(path):
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print("JSON読み込みエラー:", path, e)
    return {}

WORD_DICT = load_json(f"{DICT_DIR}/word.json")
PHRASE_DICT = load_json(f"{DICT_DIR}/phrase.json")

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
        title TEXT,
        favorite INTEGER DEFAULT 0
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER,
        speaker TEXT,
        text TEXT,
        japanese TEXT
    )
    """)

    conn.commit()
    conn.close()

# -----------------------
# 数字
# -----------------------
def number_to_katakana(num):
    special = {
        "600": "シクスハンドレッド",
        "500": "ファイブハンドレッド",
        "450": "フォーハンドレッドフィフティ"
    }

    if num in special:
        return special[num]

    mapping = {
        "0":"ゼロ","1":"ワン","2":"トゥー","3":"スリー",
        "4":"フォー","5":"ファイブ","6":"シクス",
        "7":"セブン","8":"エイト","9":"ナイン"
    }

    return " ".join(mapping.get(n, "") for n in num)

# -----------------------
# カタカナ変換（最終完成）
# -----------------------
def to_katakana(text):

    t = text.lower()

    # ① 数字
    t = re.sub(r"\d+", lambda m: number_to_katakana(m.group()), t)

    # ② フレーズ（長い順）
    for k, v in sorted(PHRASE_DICT.items(), key=lambda x: -len(x[0])):
        t = t.replace(k, v)

    # ③ 単語
    for k, v in WORD_DICT.items():
        t = t.replace(k, v)

    # ★ ④ フレーズ再適用（最重要）
    for k, v in sorted(PHRASE_DICT.items(), key=lambda x: -len(x[0])):
        t = t.replace(k, v)

    # ⑤ 記号削除（?も消す）
    t = re.sub(r"[.,!?]", "", t)

    # ⑥ 英字削除
    t = re.sub(r"[a-z]", "", t)

    # ⑦ スペース整理
    t = re.sub(r"\s+", " ", t).strip()

    return t

# -----------------------
# 翻訳
# -----------------------
FORCE_DICT = {
    "hello.": "こんにちは。",
    "hi.": "やあ。",
    "that will be 600 yen.": "600円になります。",
}

def translate(text):
    key = text.lower().strip()

    if key in FORCE_DICT:
        return FORCE_DICT[key]

    if client is None:
        return text

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"{text}\n自然な日本語に1文で翻訳。説明禁止"
            }],
            temperature=0
        )
        return res.choices[0].message.content.strip()
    except:
        return text

# -----------------------
# Jinja
# -----------------------
app.jinja_env.globals.update(to_katakana=to_katakana)

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

# -----------------------
# 追加
# -----------------------
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

        conn = get_db()
        cur = conn.execute("INSERT INTO conversations (title) VALUES (?)",(title,))
        conv_id = cur.lastrowid

        for i in range(len(texts)):
            conn.execute("""
            INSERT INTO messages
            (conversation_id,speaker,text,japanese)
            VALUES (?,?,?,?)
            """,(conv_id,speakers[i],texts[i],translate(texts[i])))

        conn.commit()
        conn.close()

        return redirect("/eng/")

    return render_template("add_multi.html")

# -----------------------
# 詳細
# -----------------------
@app.route("/detail_multi/<int:id>")
@app.route("/eng/detail_multi/<int:id>")
def detail_multi(id):
    conn = get_db()
    conv = conn.execute("SELECT * FROM conversations WHERE id=?",(id,)).fetchone()
    messages = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY id",(id,)
    ).fetchall()
    conn.close()
    return render_template("detail_multi.html",conv=conv,messages=messages)

# -----------------------
# 削除
# -----------------------
@app.route("/delete/<int:id>",methods=["POST"])
@app.route("/eng/delete/<int:id>",methods=["POST"])
def delete(id):
    conn = get_db()
    conn.execute("DELETE FROM conversations WHERE id=?",(id,))
    conn.execute("DELETE FROM messages WHERE conversation_id=?",(id,))
    conn.commit()
    conn.close()
    return redirect("/eng/")

# -----------------------
# 起動
# -----------------------
if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0",port=5005)