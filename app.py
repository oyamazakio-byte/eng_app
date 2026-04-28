from flask import Flask, render_template, request, redirect
import sqlite3
import re
import json
import os
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_proto=1, x_host=1)

DB_NAME = "/home/bitnami/eng_app/conversation.db"
DICT_DIR = "/home/bitnami/eng_app/dict"

# -----------------------
# JSON読み込み
# -----------------------
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
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
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
# 発音調整
# -----------------------
def tune_katakana(text):

    text = text.replace("ドゥ ユー", "ドゥヤ")
    text = text.replace("ゲット ア", "ゲッラ")
    text = text.replace("ハヴ ア", "ハヴァ")
    text = text.replace("ウッド ユー", "ウッジュー")

    text = text.replace("レコメン", "レコメンド")
    text = text.replace("ハブ", "ハヴ")

    return re.sub(r"\s+", " ", text).strip()

# -----------------------
# カタカナ
# -----------------------
def to_katakana(text):
    norm = normalize(text)

    # 完全一致
    if norm in PHRASE_DICT:
        return tune_katakana(PHRASE_DICT[norm])

    # 部分一致（安全）
    for k, v in sorted(PHRASE_DICT.items(), key=lambda x: -len(x[0])):
        if len(k.split()) >= 3 and k in norm:
            return tune_katakana(v)

    return text

def to_katakana_native(text):
    return to_katakana(text)

# -----------------------
# 翻訳（最重要改善）
# -----------------------
def translate(text):
    key = normalize(text)

    # ★金額対応（最優先）
    m = re.search(r"that will be (\d+) yen", key)
    if m:
        return f"お会計は{m.group(1)}円です"

    # ★Sure対応
    if "sure" in key and "hot or iced" in key:
        return "かしこまりました。ホットかアイス、どちらにしますか？"

    # 完全一致
    if key in TRANSLATE_DICT:
        return TRANSLATE_DICT[key]

    # 部分一致
    for k, v in sorted(TRANSLATE_DICT.items(), key=lambda x: -len(x[0])):
        if k in key:
            return v

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

            conn.execute("""
            INSERT INTO messages
            (conversation_id,speaker,text,japanese,kana,kana_native)
            VALUES (?,?,?,?,?,?)
            """,(conv_id,speakers[i],text,
                translate(text),
                to_katakana(text),
                to_katakana_native(text)
            ))

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

# -----------------------
# 削除
# -----------------------
@app.route("/delete/<int:id>", methods=["POST"])
@app.route("/eng/delete/<int:id>", methods=["POST"])
def delete(id):
    conn = get_db()
    conn.execute("DELETE FROM conversations WHERE id=?", (id,))
    conn.execute("DELETE FROM messages WHERE conversation_id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/eng/")

# -----------------------
# 再変換
# -----------------------
@app.route("/retranslate_all", methods=["POST"])
@app.route("/eng/retranslate_all", methods=["POST"])
def retranslate_all():

    conn = get_db()
    messages = conn.execute("SELECT * FROM messages").fetchall()

    for m in messages:
        text = m["text"]

        conn.execute("""
        UPDATE messages
        SET japanese=?, kana=?, kana_native=?
        WHERE id=?
        """, (
            translate(text),
            to_katakana(text),
            to_katakana_native(text),
            m["id"]
        ))

    conn.commit()
    conn.close()

    return redirect("/eng/")

# -----------------------
# 起動
# -----------------------
if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0",port=5005)