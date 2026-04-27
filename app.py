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
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if api_key else None

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
# 正規化（重要）
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
        kana TEXT
    )
    """)

    conn.commit()
    conn.close()

# -----------------------
# ★ 発音チューニング（最終）
# -----------------------
def tune_katakana(text):

    # リンキング
    text = text.replace(" ア ", "ァ ")
    text = text.replace(" ア", "ァ")

    text = text.replace("ライク ア", "ライクァ")
    text = text.replace("アウッライク ア", "アウッライクァ")
    text = text.replace("ハヴ ア", "ハヴァ")

    # 基本補正
    text = text.replace("ドゥ ユー", "ジュー")
    text = text.replace("ドゥユー", "ジュー")

    text = text.replace("ドゥント", "ドント")
    text = text.replace("ドウント", "ドント")

    text = text.replace("ワット ドゥ ユー", "ワッドゥユー")
    text = text.replace("ワットゥ ユー", "ワッドゥユー")

    text = text.replace("アライク", "アウッライク")
    text = text.replace("アイ ウッド ライク", "アウッライク")

    text = text.replace("レザベイション", "リザベイション")
    text = text.replace("レコメンド", "レコメン")

    # -----------------------
    # ★ 追加（今回の本命）
    # -----------------------
    text = text.replace("ワッジュー", "ワッドゥユー")
    text = text.replace("ワッジュ", "ワッドゥユー")

    # 仕上げ
    text = text.replace("ジュー ", "ジュー")
    text = text.replace("ウィー ", "ウィ ")

    text = text.replace("アウッライク ステーキ", "アウッライクァ ステーキ")

    if text.startswith("レコメン"):
        text = "ワッドゥユー " + text

    text = re.sub(r"\s+", " ", text).strip()

    return text

# -----------------------
# カタカナ変換
# -----------------------
def to_katakana(text):

    norm = normalize(text)

    # 辞書優先
    if norm in PHRASE_DICT:
        return tune_katakana(PHRASE_DICT[norm])

    # AI
    if client is not None:
        try:
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{
                    "role": "user",
                    "content": f"{text}\nネイティブの自然な発音でカタカナ化。リンキング・弱形・脱落を反映。カタカナのみ。説明禁止。"
                }],
                temperature=0.2
            )
            result = res.choices[0].message.content.strip()
            return tune_katakana(result)
        except:
            return text

    return text

# -----------------------
# 翻訳
# -----------------------
def translate(text):
    key = normalize(text)

    for k, v in TRANSLATE_DICT.items():
        if normalize(k) == key:
            return v

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

            jp = translate(texts[i])
            kana = to_katakana(texts[i])

            conn.execute("""
            INSERT INTO messages
            (conversation_id,speaker,text,japanese,kana)
            VALUES (?,?,?,?,?)
            """,(conv_id,speakers[i],texts[i],jp,kana))

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
# 再翻訳
# -----------------------
@app.route("/eng/retranslate/<int:id>", methods=["POST"])
def retranslate(id):

    conn = get_db()
    m = conn.execute("SELECT * FROM messages WHERE id=?", (id,)).fetchone()

    new_jp = translate(m["text"])
    new_kana = to_katakana(m["text"])

    conn.execute("""
    UPDATE messages
    SET japanese=?, kana=?
    WHERE id=?
    """, (new_jp, new_kana, id))

    conn.commit()
    conn.close()

    return "OK"

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