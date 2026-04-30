from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import re
import json
import os
import glob
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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

    except Exception as e:

        print(f"[JSON ERROR] {path} -> {e}")

    return {}
# -----------------------
# 辞書ロード
# -----------------------
PHRASE_DICT = {}
TRANSLATE_DICT = {}
WORD_KANA_DICT = {}
NATIVE_DICT = {}

for path in sorted(glob.glob(f"{DICT_DIR}/*.json")):

    name = os.path.basename(path)

    data = load_json(path)

    print(f"[LOAD] {name} : {len(data)}")

    # fixed翻訳辞書
    if "fixed_translate" in name:

        TRANSLATE_DICT.update({
            k.lower(): v
            for k, v in data.items()
        })

    # 翻訳辞書
    elif "translate" in name:

        TRANSLATE_DICT.update({
            k.lower(): v
            for k, v in data.items()
        })

    # native辞書
    elif "native" in name:

        NATIVE_DICT.update({
            k.lower(): v
            for k, v in data.items()
        })

    # 単語辞書
    elif "word" in name:

        WORD_KANA_DICT.update({
            k.lower(): v
            for k, v in data.items()
        })

    # それ以外は全部発音辞書
    else:

        PHRASE_DICT.update({
            k.lower(): v
            for k, v in data.items()
        })

print(f"[PHRASE] {len(PHRASE_DICT)}")
print(f"[TRANS] {len(TRANSLATE_DICT)}")
print(f"[WORD] {len(WORD_KANA_DICT)}")
print(f"[NATIVE] {len(NATIVE_DICT)}")    
    

    
# -----------------------
# 正規化
# -----------------------
def normalize(text):

    text = text.lower()

    text = text.replace("’", "'")
    text = text.replace("‘", "'")

    # contraction対応
    text = text.replace("'", "")

    # 記号除去
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # 空白整理
    text = re.sub(r"\s+", " ", text)

    
    text = text.strip()

    print(f"[NORMALIZE] {text}")

    return text

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
# 数値 → カタカナ
# -----------------------
def number_to_kana(num):

    special = {
        "999": "ナイン ハンドレッド ナインティ ナイン"
    }

    if num in special:
        return special[num]

    ONES = {
        0: "",
        1: "ワン",
        2: "トゥー",
        3: "スリー",
        4: "フォー",
        5: "ファイブ",
        6: "シックス",
        7: "セブン",
        8: "エイト",
        9: "ナイン"
    }

    TENS = {
        0: "",
        1: "テン",
        2: "トゥエンティ",
        3: "サーティ",
        4: "フォーティ",
        5: "フィフティ",
        6: "シックスティ",
        7: "セブンティ",
        8: "エイティ",
        9: "ナインティ"
    }

    # 数値化
    try:
        n = int(num)
    except:
        return num

    # 100〜999
    if 100 <= n <= 999:

        hundreds = n // 100
        tens_ones = n % 100

        result = []

        # hundreds
        if hundreds > 0:
            result.append(
                f"{ONES[hundreds]} ハンドレッド"
            )

        # 10〜99
        if tens_ones >= 20:

            tens = tens_ones // 10
            ones = tens_ones % 10

            result.append(TENS[tens])

            if ones > 0:
                result.append(ONES[ones])

        elif tens_ones >= 10:

            teens = {
                10: "テン",
                11: "イレブン",
                12: "トゥエルブ",
                13: "サーティーン",
                14: "フォーティーン",
                15: "フィフティーン",
                16: "シックスティーン",
                17: "セブンティーン",
                18: "エイティーン",
                19: "ナインティーン"
            }

            result.append(teens[tens_ones])

        elif tens_ones > 0:

            result.append(
                ONES[tens_ones]
            )

        joined = " ".join(result)

        

        return joined

    # fallback
    NUM = {
        "0": "ゼロ",
        "1": "ワン",
        "2": "トゥー",
        "3": "スリー",
        "4": "フォー",
        "5": "ファイブ",
        "6": "シックス",
        "7": "セブン",
        "8": "エイト",
        "9": "ナイン"
    }

    return " ".join(NUM.get(c, c) for c in num)
# -----------------------
# 発音調整
# -----------------------
def tune_katakana(text):

    text = text.replace("ドゥ ユー", "ドゥヤ")
    #text = text.replace("ゲット ア", "ゲッラ")
    text = text.replace("ハヴ ア", "ハヴァ")
    text = text.replace("ウッド ユー", "ウッジュー")
    text = text.replace("ハブ", "ハヴ")

    return re.sub(r"\s+", " ", text).strip()

# -----------------------
# 単語fallback
# -----------------------
def fallback_word_katakana(text):

    words = text.split()

    result = []

    for w in words:

        nw = normalize(w)

        if nw in WORD_KANA_DICT:

            result.append(
                WORD_KANA_DICT[nw]
            )

        elif re.fullmatch(r"[a-zA-Z]+", w):

            result.append(w.upper())

        else:

            result.append(w)

    joined = " ".join(result)

    for k, v in NATIVE_DICT.items():
        joined = joined.replace(k, v)

    return joined
# -----------------------
# 部分一致検索
# 最長一致優先
# -----------------------
def partial_match(text, target_dict):

    result = text

    keys = sorted(
        target_dict.keys(),
        key=len,
        reverse=True
    )

    for k in keys:

        # 1単語だけ除外
        if len(k.split()) == 1:
            continue

        pattern = r"\b" + re.escape(k) + r"\b"

        if re.search(pattern, result):

            print(f"[PARTIAL HIT] {k}")

            result = re.sub(
                pattern,
                target_dict[k],
                result
            )

    return result 

# -----------------------
# 翻訳用部分一致
# 短文誤爆防止
# -----------------------
def partial_match_translate(text, target_dict):

    result = text

    keys = sorted(
        target_dict.keys(),
        key=len,
        reverse=True
    )

    for k in keys:

        # 4単語以下は除外
        if len(k.split()) <= 4:
            continue

        pattern = r"\b" + re.escape(k) + r"\b"

        if re.search(pattern, result):

            print(f"[TRANS PARTIAL HIT] {k}")

            result = re.sub(
                pattern,
                target_dict[k],
                result
            )

    return result   
# -----------------------
# カタカナ
# -----------------------
def to_katakana(text):

    norm = normalize(text)

    print(f"[NORM] {norm}")
    
    # 金額
    m = re.search(
        r"that will be (\d+) yen",
        norm
    )

    if m:

        print("[KANA] MONEY")

        return (
            f"ザット ウィル ビー "
            f"{number_to_kana(m.group(1))} "
            f"イェン"
        )

    # 完全一致
    print(f"[CHECK PHRASE] {norm}")

    if norm in PHRASE_DICT:

        print(f"[KANA] EXACT: {norm}")

        return tune_katakana(
            PHRASE_DICT[norm]
        )

    # 長文優先部分一致
    converted = partial_match(
        norm,
        PHRASE_DICT
    )

    if converted != norm:

        print(f"[KANA] PARTIAL: {norm}")

        converted = fallback_word_katakana(converted)

        return tune_katakana(converted)

    # fallback
    if re.search(r"[a-z]", norm):

        print(f"[KANA] FALLBACK: {norm}")

        return fallback_word_katakana(norm)

    return text
def to_katakana_native(text):

    return to_katakana(text)

# -----------------------
# 翻訳
# -----------------------
def translate(text):

    key = normalize(text)
    
    print(f"[KEY] {key}")

    # 金額
    m = re.search(
        r"that will be (\d+) yen",
        key
    )

    if m:

        print("[HIT] MONEY")

        return f"お会計は{m.group(1)}円です"

    # 特殊
    if (
        "sure" in key
        and "hot or iced" in key
    ):

        print("[HIT] SURE_PATTERN")

        return (
            "かしこまりました。"
            "ホットかアイス、"
            "どちらにしますか？"
        )

    print(f"[TRANS KEY] {key}")

    # 完全一致
    if key in TRANSLATE_DICT:

        print(f"[HIT] EXACT: {key}")

        return TRANSLATE_DICT[key]

    # 長文優先部分一致
    partial = partial_match_translate(
        key,
        TRANSLATE_DICT
    )

    if partial != key:

        print(f"[HIT] PARTIAL: {key}")

        return partial
    print("[MISS]")

    return text
# -----------------------
# NG / 注意 判定
# -----------------------
def detect_ng(text, kana, japanese):

    issues = []
    warnings = []

    # 翻訳NG
    if japanese.strip().lower() == text.strip().lower():
        issues.append("translation_missing")

    # fallback
    if re.search(r"[A-Z]{2,}", kana):
        warnings.append("kana_fallback")

    # 発音短すぎ
    word_count = len(text.split())
    kana_len = len(kana.strip())

    if (
        word_count >= 4
        and kana_len < word_count * 1.5
    ):
        issues.append("kana_short")

    # 数値
    if re.search(r"\d+", text):
        warnings.append("contains_number")

    return issues, warnings

# -----------------------
# ルート
# -----------------------
@app.route("/")
@app.route("/eng/")
def index():

    conn = get_db()

    data = conn.execute(
        "SELECT * FROM conversations ORDER BY id DESC"
    ).fetchall()

    conn.close()

    return render_template(
        "index.html",
        data=data
    )

# -----------------------
# 登録
# -----------------------
@app.route("/add_multi", methods=["GET", "POST"])
@app.route("/eng/add_multi", methods=["GET", "POST"])
def add_multi():

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        lines_raw = request.form.get(
            "lines",
            ""
        ).strip()

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
                texts.append(
                    line[2:].strip()
                )

            elif line.startswith("B:"):

                speakers.append("B")
                texts.append(
                    line[2:].strip()
                )

            else:

                speakers.append(
                    speaker_toggle
                )

                texts.append(line)

                speaker_toggle = (
                    "B"
                    if speaker_toggle == "A"
                    else "A"
                )

        conn = get_db()

        cur = conn.execute(
            """
            INSERT INTO conversations (title)
            VALUES (?)
            """,
            (title,)
        )

        conv_id = cur.lastrowid

        for i in range(len(texts)):

            text = texts[i]

            kana = to_katakana(text)

            japanese = translate(text)

            issues, warnings = detect_ng(
                text,
                kana,
                japanese
            )

            print(
                f"[NG] {text} -> "
                f"{issues}, WARN={warnings}"
            )

            conn.execute("""
            INSERT INTO messages
            (
                conversation_id,
                speaker,
                text,
                japanese,
                kana,
                kana_native
            )
            VALUES (?,?,?,?,?,?)
            """, (
                conv_id,
                speakers[i],
                text,
                japanese,
                kana,
                kana
            ))

        conn.commit()
        conn.close()

        return redirect("/eng/")

    return render_template(
        "add_multi.html"
    )

# -----------------------
# 詳細
# -----------------------
@app.route("/detail_multi/<int:id>")
@app.route("/eng/detail_multi/<int:id>")
def detail_multi(id):

    conn = get_db()

    conv = conn.execute(
        """
        SELECT *
        FROM conversations
        WHERE id=?
        """,
        (id,)
    ).fetchone()

    rows = conn.execute(
        """
        SELECT *
        FROM messages
        WHERE conversation_id=?
        ORDER BY id
        """,
        (id,)
    ).fetchall()

    conn.close()

    messages = []

    for m in rows:

        issues, warnings = detect_ng(
            m["text"],
            m["kana"],
            m["japanese"]
        )

        m_dict = dict(m)

        m_dict["issues"] = issues
        m_dict["warnings"] = warnings

        messages.append(m_dict)

    return render_template(
        "detail_multi.html",
        conv=conv,
        messages=messages
    )

# -----------------------
# 全件再変換
# -----------------------
@app.route("/retranslate_all", methods=["POST"])
@app.route("/eng/retranslate_all", methods=["POST"])
def retranslate_all():

    conn = get_db()

    messages = conn.execute(
        "SELECT * FROM messages"
    ).fetchall()

    for m in messages:

        text = m["text"]

        conn.execute("""
        UPDATE messages
        SET japanese=?,
            kana=?,
            kana_native=?
        WHERE id=?
        """, (
            translate(text),
            to_katakana(text),
            to_katakana(text),
            m["id"]
        ))

    conn.commit()
    conn.close()

    return redirect(
        url_for("index"),
        code=303
    )

# -----------------------
# 削除
# -----------------------
@app.route(
    "/eng/delete/<int:id>",
    methods=["POST"]
)
def delete_conversation(id):

    print(f"[DELETE] id={id}")

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        DELETE FROM messages
        WHERE conversation_id=?
        """,
        (id,)
    )

    cur.execute(
        """
        DELETE FROM conversations
        WHERE id=?
        """,
        (id,)
    )

    conn.commit()
    conn.close()

    return redirect("/eng")

# -----------------------
# 起動
# -----------------------
if __name__ == "__main__":

    init_db()

    app.run(
        host="0.0.0.0",
        port=5005
    )