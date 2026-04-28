from flask import Flask, render_template, request, redirect
import sqlite3
import re
import json
import os
from werkzeug.middleware.proxy_fix import ProxyFix
from openai import OpenAI

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_proto=1, x_host=1)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

DB_NAME = "/home/bitnami/eng_app/conversation.db"
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

def normalize(text):
    text = text.lower()
    text = text.replace("’", "'")
    text = re.sub(r"[.,!?]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

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
# 発音調整（強化版）
# -----------------------
def tune_katakana(text):

    # リンキング
    text = text.replace("ドゥ ユー", "ドゥヤ")
    text = text.replace("ドゥユー", "ドゥヤ")
    text = text.replace("ゲット ア", "ゲッラ")
    text = text.replace("ハヴ ア", "ハヴァ")
    text = text.replace("ヒア イズ", "ヒアリズ")

    # would you
    text = text.replace("ウッド ユー", "ウッジュー")
    text = text.replace("ウッドゥヤ", "ウッジュー")
    text = text.replace("ワッユ", "ウッジュー")

    # 発音修正
    text = text.replace("ハブ", "ハヴ")
    text = text.replace("リコメンド", "レコメンド")
    text = text.replace("レコメンドド", "レコメンド")
    text = text.replace("レコメン", "レコメンド")
    text = text.replace("レザベーション", "リザベーション")

    # 弱音
    text = text.replace("ジャスト", "ジャス")
    text = text.replace("ホット", "ハッ")

    # 誤変換除去
    text = text.replace("ジュー", "ドゥヤ")

    # アイル単独対策
    if text.strip() == "アイル":
        return "アイル ハヴ"

    return re.sub(r"\s+", " ", text).strip()

# -----------------------
# カタカナ
# -----------------------
def to_katakana(text):

    norm = normalize(text)

    if norm in PHRASE_DICT:
        return tune_katakana(PHRASE_DICT[norm])

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"{text}\nカタカナのみ。1行。説明禁止。"
            }],
            temperature=0.2
        )

        result = res.choices[0].message.content.strip().split("\n")[0]
        result = re.sub(r"[「」\"。]", "", result)

        return tune_katakana(result)

    except:
        return text

# -----------------------
# ネイティブ
# -----------------------
def to_katakana_native(text):

    norm = normalize(text)

    if norm in PHRASE_DICT:
        return tune_katakana(PHRASE_DICT[norm])

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": f"""
{text}
英語の音だけをカタカナにする
・意味変更禁止
・単語追加禁止
・説明禁止
・カタカナのみ
・1行
"""
            }],
            temperature=0
        )

        result = res.choices[0].message.content.strip().split("\n")[0]
        result = re.sub(r"[「」\"。]", "", result)

        # 🔥 日本語混入完全ブロック
        if re.search(r"[ぁ-んァ-ン一-龥]", result):
            return to_katakana(text)

        # 英語混入防止
        if re.search(r"[a-zA-Z]", result):
            return to_katakana(text)

        # 短すぎ防止
        if len(result) < 5:
            return to_katakana(text)

        return tune_katakana(result)

    except:
        return to_katakana(text)

# -----------------------
# 翻訳（強化版）
# -----------------------
def translate(text):
    key = normalize(text)

    if "good evening" in key:
        return "こんばんは。ご予約はありますか？"

    if "no we don't" in key:
        return "いいえ、ありません"

    if "what do you recommend" in key:
        return "おすすめは何ですか？"

    if "sure hot or iced" in key:
        return "かしこまりました。ホットかアイス、どちらにしますか？"

    for k, v in TRANSLATE_DICT.items():
        if normalize(k) == key:
            return v

    m = re.search(r"that will be (\d+) yen", key)
    if m:
        return f"お会計は{m.group(1)}円です"

    if "hello can i get a coffee" in key:
        return "こんにちは。コーヒーをください。"

    if "can i get a coffee" in key:
        return "コーヒーをください"

    if "hot or iced" in key:
        return "ホットかアイス、どちらにしますか？"

    if "would you like anything else" in key:
        return "他にご注文はありますか？"

    if "for here or to go" in key:
        return "店内ですか？お持ち帰りですか？"

    if key == "to go":
        return "持ち帰りでお願いします"

    if "yes a sandwich please" in key:
        return "サンドイッチをお願いします"

    if "your order will be ready soon" in key:
        return "ご注文はすぐにご用意できます"

    return text

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

@app.route("/delete/<int:id>", methods=["POST"])
def delete(id):
    conn = get_db()
    conn.execute("DELETE FROM conversations WHERE id=?", (id,))
    conn.execute("DELETE FROM messages WHERE conversation_id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/eng/")

if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0",port=5005)