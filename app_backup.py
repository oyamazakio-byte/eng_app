from flask import Flask, render_template, request, redirect
import sqlite3
import re
import json
from werkzeug.middleware.proxy_fix import ProxyFix
from openai import OpenAI

app = Flask(__name__)

# Proxy対応
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_proto=1, x_host=1)

client = OpenAI()

DB_NAME = "/home/bitnami/eng_app/conversation.db"

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
        katakana TEXT,
        japanese TEXT
    )
    """)

    conn.commit()
    conn.close()

# -----------------------
# カタカナ
# -----------------------
def to_katakana(text):
    t = text.lower()

    phrase = {
        "can i get": "キャナイゲッ",
        "would you like": "ウッジューライク",
        "for here or to go": "フォーヒアオアトゥゴー",
        "thank you": "サンキュー",
        "here you are": "ヒアユーアー",
    }

    for k,v in phrase.items():
        t = t.replace(k,v)

    word = {
        "hello":"ハロー",
        "coffee":"コーヒー",
        "please":"プリーズ",
        "sure":"シュア",
        "hot":"ホット",
        "iced":"アイスド",
        "medium":"ミディアム",
        "to":"トゥ",
        "go":"ゴー",
        "you":"ユー",
        "are":"アー",
        "a ":"ア ",
    }

    for k,v in word.items():
        t = t.replace(k,v)

    t = re.sub(r"[.,!?]", "", t)
    return t

# -----------------------
# 強制翻訳辞書
# -----------------------
FORCE_DICT = {
    "hello. can i get a coffee?": "すみません、コーヒーをいただけますか？",
    "sure. hot or iced?": "かしこまりました。ホットとアイスどちらにしますか？",
    "hot, please.": "ホットでお願いします。",
    "what size would you like?": "サイズはどれにしますか？",
    "medium, please.": "ミディアムでお願いします。",
    "for here or to go?": "こちらでお召し上がりですか、それともお持ち帰りですか？",
    "to go, please.": "持ち帰りでお願いします。",
    "that will be 450 yen.": "450円になります。",
    "here you are.": "はい、どうぞ。",
    "thank you.": "ありがとうございます。"
}

# -----------------------
# 翻訳
# -----------------------
def translate(text):

    key = text.lower().strip()

    if key in FORCE_DICT:
        return FORCE_DICT[key]

    for k,v in FORCE_DICT.items():
        if k in key:
            return v

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role":"user",
                "content":f"{text}\n自然な日本語に1文で翻訳。説明禁止"
            }],
            temperature=0
        )

        result = res.choices[0].message.content.strip()

        result = result.replace("「","").replace("」","")

        if "。" in result:
            result = result.split("。")[0] + "。"

        return result

    except:
        return ""

# -----------------------
# DEBUG
# -----------------------
@app.route("/debug")
def debug():
    return "OK: app loaded"

# -----------------------
# 一覧
# -----------------------
@app.route("/")
def index():
    conn = get_db()
    data = conn.execute("SELECT * FROM conversations ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("index.html", data=data)

# -----------------------
# 複数追加（★修正ポイント）
# -----------------------
@app.route("/add_multi", methods=["GET","POST"])
def add_multi():

    if request.method=="POST":

        title = request.form.get("title", "").strip()
        lines_raw = request.form.get("lines", "").strip()

        # ★ タイトルチェック
        if not title:
            return "タイトルを入力してください"

        # ★ 会話チェック
        if not lines_raw:
            return "会話を入力してください"

        lines = lines_raw.split("\n")

        texts = []
        speakers = []

        for line in lines:
            line=line.strip()
            if not line:
                continue

            if line.startswith("A:"):
                speakers.append("A")
                texts.append(line[2:].strip())
            elif line.startswith("B:"):
                speakers.append("B")
                texts.append(line[2:].strip())

        # ★ 有効データチェック
        if not texts:
            return "A: / B: の形式で入力してください"

        conn = get_db()
        cur = conn.execute("INSERT INTO conversations (title) VALUES (?)",(title,))
        conv_id = cur.lastrowid

        for i in range(len(texts)):

            text = texts[i]

            jp = translate(text)
            kana = to_katakana(text)

            conn.execute("""
            INSERT INTO messages
            (conversation_id,speaker,text,katakana,japanese)
            VALUES (?,?,?,?,?)
            """,(conv_id,speakers[i],text,kana,jp))

        conn.commit()
        conn.close()

        return redirect("/")

    return render_template("add_multi.html")

# -----------------------
# 詳細
# -----------------------
@app.route("/detail_multi/<int:id>")
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
def delete(id):
    conn = get_db()
    conn.execute("DELETE FROM conversations WHERE id=?",(id,))
    conn.execute("DELETE FROM messages WHERE conversation_id=?",(id,))
    conn.commit()
    conn.close()
    return redirect("/")

# -----------------------
# 起動
# -----------------------
if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0",port=5005)