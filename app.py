from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import re
import json
import os
import requests
import glob
from openai import OpenAI
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import jsonify
from datetime import datetime

USD_TO_JPY = 150

def split_news_sentences(text):

    # 改行をスペースへ
    text = text.replace("\n", " ")

    # 略語保護
    abbreviations = {
        "U.S.": "__US__",
        "U.K.": "__UK__",
        "Mr.": "__MR__",
        "Mrs.": "__MRS__",
        "Ms.": "__MS__",
        "Dr.": "__DR__",
        "Prof.": "__PROF__",
        "Inc.": "__INC__",
        "Ltd.": "__LTD__",
        "Jr.": "__JR__",
        "Sr.": "__SR__",
        "e.g.": "__EG__",
        "i.e.": "__IE__"
    }

    # 一時置換
    for k, v in abbreviations.items():
        text = text.replace(k, v)

    # 文分割
    sentences = re.split(
        r'(?<=[.!?])\s+(?=[A-Z"])',
        text
    )

    result = []

    for i, s in enumerate(sentences, 1):

        s = s.strip()

        # 略語復元
        for k, v in abbreviations.items():
            s = s.replace(v, k)

        if s:
            result.append(f"B{i}: {s}")

    return "\n".join(result)


app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static"
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1, x_proto=1, x_host=1)

DB_NAME = "/home/bitnami/eng_app/conversation.db"
USAGE_DB_NAME = "/home/bitnami/eng_app/usage.db"
API_BUDGET_USD = 5.0
DICT_DIR = "/home/bitnami/eng_app/dict"

STATS_PATH = (
    f"{DICT_DIR}/stats.json"
)

client = OpenAI()
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
AI_KANA_COUNT = 0
AI_TRANS_COUNT = 0

KANA_CACHE_HIT = 0
KANA_TOTAL = 0
UNKNOWN_WORDS = {}

TRANS_CACHE_HIT = 0
TRANS_TOTAL = 0

# -----------------------
# 翻訳キャッシュ
# -----------------------
TRANSLATION_CACHE_PATH = (
    f"{DICT_DIR}/translation_cache.json"
)

try:

    with open(
        TRANSLATION_CACHE_PATH,
        encoding="utf-8"
    ) as f:

        TRANSLATION_CACHE = json.load(f)

except:

    TRANSLATION_CACHE = {}

# -----------------------
# カタカナキャッシュ
# -----------------------
KATAKANA_CACHE_PATH = (
    f"{DICT_DIR}/katakana_cache.json"
)

try:

    with open(
        KATAKANA_CACHE_PATH,
        encoding="utf-8"
    ) as f:

        KATAKANA_CACHE = json.load(f)

except:

    KATAKANA_CACHE = {}

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
    elif (
        "translate" in name
        and "translation_cache" not in name
    ):

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

    # キャッシュは無視
    elif (
        "translation_cache" in name
        or "katakana_cache" in name
    ):

        pass

    # それ以外は全部発音辞書
    else:

        PHRASE_DICT.update({
            k.lower(): v
            for k, v in data.items()
       })

# -----------------------
# 汚染データ除去
# -----------------------
REMOVE_FROM_PHRASE = set(TRANSLATION_CACHE.keys())

for k in REMOVE_FROM_PHRASE:

    if k in PHRASE_DICT:

        print(f"[REMOVE PHRASE POLLUTION] {k}")

        del PHRASE_DICT[k]
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
    # contraction展開
    text = text.replace("i'll", "i will")
    text = text.replace("you're", "you are")
    text = text.replace("we're", "we are")
    text = text.replace("they're", "they are")

    text = text.replace("don't", "do not")
    text = text.replace("doesn't", "does not")
    text = text.replace("can't", "cannot")
    text = text.replace("won't", "will not")

    text = text.replace("it's", "it is")
    text = text.replace("that's", "that is")

    text = text.replace("i'm", "i am")
    text = text.replace("isn't", "is not")
    text = text.replace("aren't", "are not")
    text = text.replace("didn't", "did not")
    text = text.replace("wouldn't", "would not")
    text = text.replace("couldn't", "could not")

    # contraction対応
    text = text.replace("'", "")

    # 記号除去
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # 空白整理（改行維持）
    text = re.sub(r"[ \t]+", " ", text)
    
    text = text.strip()

    #print(f"[NORMALIZE] {text}")

    return text

# -----------------------
# DB
# -----------------------
def get_db():

    conn = sqlite3.connect(
        DB_NAME,
        timeout=30
    )
    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

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
    # API usage
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cost REAL
        )
        """
    )
    conn.commit()
    conn.close()

init_db()
def get_usage_db():

    conn = sqlite3.connect(
        USAGE_DB_NAME,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn

# -----------------------
# Usage DB 初期化
# -----------------------
def init_usage_db():

    conn = get_usage_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cost REAL
        )
        """
    )

    conn.commit()
    conn.close()

init_usage_db()
# -----------------------
# OpenAI billing取得
# -----------------------
def get_openai_balance():

    try:

        headers = {
            "Authorization":
            f"Bearer {os.getenv('OPENAI_API_KEY')}"
        }
        response = requests.get(
            "https://api.openai.com/v1/dashboard/billing/credit_grants",
            headers=headers,
            timeout=20
        )

        data = response.json()

        print("[OPENAI BILLING RAW]")
        print(data)

        return {
            "total_granted":
                data.get("total_granted", 0),

            "total_used":
                data.get("total_used", 0),

            "total_available":
                data.get("total_available", 0)
        }

    except Exception as e:

        print(f"[OPENAI BILLING ERROR] {e}")

        return {
            "total_granted": 0,
            "total_used": 0,
            "total_available": 0
        }
# -----------------------
# OpenAI total cost取得
# -----------------------
def get_openai_total_cost():

    try:

        headers = {
            "Authorization":
            f"Bearer {os.getenv('OPENAI_API_KEY')}"
        }

        response = requests.get(
            "https://api.openai.com/v1/organization/costs",
            headers=headers,
            timeout=20
        )

        data = response.json()

        print("[OPENAI COST RAW]")
        print(data)

        total_cost = 0.0

        for item in data.get("data", []):

            try:

                amount = (
                    item["amount"]["value"]
                )

                total_cost += amount

            except Exception as e:

                print(
                    f"[OPENAI COST ITEM ERROR] {e}"
                )

        print(
            f"[OPENAI TOTAL COST] ${total_cost}"
        )

        return total_cost

    except Exception as e:

        print(
            f"[OPENAI TOTAL COST ERROR] {e}"
        )

        return 0.0
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

    # 数値化
    try:
        n = int(num)

    except:
        return num

    # 10〜19
    if 10 <= n <= 19:
        return teens[n]

    # 20〜99
    if 20 <= n <= 99:

        tens = n // 10
        ones = n % 10

        result = [TENS[tens]]

        if ones > 0:
            result.append(ONES[ones])

        return " ".join(result)

    # 100〜999
    if 100 <= n <= 999:

        hundreds = n // 100
        tens_ones = n % 100

        result = []

        # hundreds
        result.append(
            f"{ONES[hundreds]} ハンドレッド"
        )

        # tens
        if 10 <= tens_ones <= 19:

            result.append(
                teens[tens_ones]
            )

        elif tens_ones >= 20:

            tens = tens_ones // 10
            ones = tens_ones % 10

            result.append(
                TENS[tens]
            )

            if ones > 0:
                result.append(
                    ONES[ones]
                )

        elif tens_ones > 0:

            result.append(
                ONES[tens_ones]
            )

        return " ".join(result)

    # 0〜9 fallback
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

    return " ".join(
        NUM.get(c, c)
        for c in str(num)
    )
# -----------------------
# 発音調整
# -----------------------
def tune_katakana(text):

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()

# -----------------------
# 辞書ヒット率
# -----------------------
def dict_hit_rate(text):

    words = re.findall(
        r"[a-zA-Z]+",
        normalize(text)
    )

    if not words:

        return 0

    hit = 0

    for w in words:

        if w.lower() in WORD_KANA_DICT:

            hit += 1

    rate = hit / len(words)

    #print(
    #    f"[DICT RATE] "
    #    f"{hit}/{len(words)} = {rate}"
    #)

    return rate
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

        # 数字
        elif re.fullmatch(r"\d+", w):

            result.append(
                number_to_kana(w)
            )

        elif re.fullmatch(r"[a-zA-Z]+", w):
            print(f"[FALLBACK WORD] {w}")
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

            #print(f"[PARTIAL HIT] {k}")

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

            #print(f"[TRANS PARTIAL HIT] {k}")

            result = re.sub(
                pattern,
                target_dict[k],
                result
            )

    if result != text:
        return result

    return None
# -----------------------
# カタカナ
# -----------------------
def to_katakana(text):

    global KANA_CACHE_HIT
    global KANA_TOTAL

    KANA_TOTAL += 1

    norm = normalize(text)

    # キャッシュ
    if norm in KATAKANA_CACHE:

        KANA_CACHE_HIT += 1

        print(
            f"[KANA CACHE] {norm}"
        )

        return KATAKANA_CACHE[norm]

    #print(f"[NORM] {norm}")
    
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
    #print(f"[CHECK PHRASE] {norm}")

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

        return ai_sentence_katakana(converted)

    # fallback
    if re.search(r"[a-z]", norm):

        rate = dict_hit_rate(norm)

        # 全単語辞書一致ならAI不要
        if rate == 1.0:

            return tune_katakana(
                fallback_word_katakana(norm)
            )

        print(
            f"[KANA] AI SENTENCE: {norm}"
        )

        return ai_sentence_katakana(text)

    return text
# -----------------------
# ネイティブ風変換
# -----------------------
def convert_native_kana(text):

    print("★★★★ convert_native_kana CALLED ★★★★")

    if not text:
        return ""

    result = text

    for key, value in NATIVE_DICT.items():

        result = result.replace(
            key,
            value
        )

    print("[BEFORE]", text)
    print("[AFTER ]", result)

    return result
# -----------------------
# 単語辞書保存
# -----------------------
def save_word_kana(word, kana):

    path = f"{DICT_DIR}/word_kana.json"

    try:

        with open(path, encoding="utf-8") as f:

            data = json.load(f)

    except:

        data = {}

    word = word.lower()

    # 未登録のみ保存
    if word not in data:

        data[word] = kana

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"[SAVE WORD] "
            f"{word} -> {kana}"
        )

        # メモリ辞書にも反映
        WORD_KANA_DICT[word] = kana

# -----------------------
# ネイティブ発音辞書
# -----------------------
NATIVE_DICT_PATH = (
    "/home/bitnami/eng_app/dict/00_native.json"
)

if os.path.exists(NATIVE_DICT_PATH):

    with open(
        NATIVE_DICT_PATH,
        "r",
        encoding="utf-8"
    ) as f:

        NATIVE_DICT = json.load(f)

else:

    NATIVE_DICT = {}

# -----------------------
# 翻訳キャッシュ保存
# -----------------------
def save_translation_cache(
    eng,
    jp
):

    eng = normalize(eng)

    # 未登録のみ
    if eng not in TRANSLATION_CACHE:

        TRANSLATION_CACHE[eng] = jp

        with open(
            TRANSLATION_CACHE_PATH,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                TRANSLATION_CACHE,
                f,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"[SAVE TRANS] "
            f"{eng}"
        )
# -----------------------
# カタカナキャッシュ保存
# -----------------------
def save_katakana_cache(
    eng,
    kana
):

    eng = normalize(eng)

    # 未登録のみ
    if eng not in KATAKANA_CACHE:

        KATAKANA_CACHE[eng] = kana

        with open(
            KATAKANA_CACHE_PATH,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                KATAKANA_CACHE,
                f,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"[SAVE KANA] "
            f"{eng}"
        )
# -----------------------
# AIカタカナ
# -----------------------
def ai_katakana(word):

    try:

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content":
                    (
                        "Convert English word "
                        "to Japanese katakana only. "
                        "No explanation."
                    )
                },
                {
                    "role": "user",
                    "content": word
                }
            ],
            temperature=0
        )

        usage = getattr(response, "usage", None)

        print("[RESPONSE OK]")
        print(f"[USAGE TYPE] {type(usage)}")
        print(f"[USAGE VALUE] {usage}")
        if usage:

            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens

            cost = (
                prompt_tokens * 0.0000004 +
                completion_tokens * 0.0000016
            )

            print(
                f"[API COST] "
                f"prompt={prompt_tokens} "
                f"completion={completion_tokens} "
                f"cost=${cost:.8f}"
            )

            conn = get_usage_db()

            print("[API INSERT START]")

            conn.execute(
                """
                INSERT INTO api_usage (
                    created_at,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    cost
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "gpt-4.1-mini",
                    prompt_tokens,
                    completion_tokens,
                    usage.total_tokens,
                    cost
                )
            )

            print("[API INSERT DONE]")

            conn.commit()
            conn.close()
        result = (
            response
            .choices[0]
            .message
            .content
            .strip()
        )

        # カギ括弧除去
        result = result.replace("「", "")
        result = result.replace("」", "")
        result = result.replace("。", "")
        result = result.replace("、", " ")
 
        print(
            f"[AI KANA] "
            f"{word} -> {result}"
        )

        # 自動辞書登録
        save_word_kana(
            word,
            result
        )

        return result

    except Exception as e:

        print(
            f"[AI KANA ERROR] {e}"
        )

        return word.upper()
# -----------------------
# AI文カタカナ
# -----------------------
def ai_sentence_katakana(text):

    global AI_KANA_COUNT
    global UNKNOWN_WORDS
    
    AI_KANA_COUNT += 1

    try:

        response = client.chat.completions.create(

            model="gpt-4.1-mini",

            messages=[

                {
                    "role": "system",

                    "content":
                    (
                        "Convert English sentence "
                        "to Japanese katakana pronunciation "
                        "for learners. "

                        "Use clear textbook-style pronunciation. "

                        "Do NOT use overly native pronunciation. "

                        "For example: "

                        "'do you' -> 'ドゥ ユー', "

                        "'want to' -> 'ウォント トゥ', "

                        "'have to' -> 'ハヴ トゥ'. "

                        "Output katakana only."
                    )
                },

                {
                    "role": "user",
                    "content": text
                }

            ],

        temperature=0
        )
        usage = getattr(response, "usage", None)

        if usage:

            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens

            cost = (
                prompt_tokens * 0.0000004 +
                completion_tokens * 0.0000016
            )

            print(
                f"[AI SENTENCE COST] "
                f"prompt={prompt_tokens} "
                f"completion={completion_tokens} "
                f"cost=${cost:.8f}"
            )

            conn = get_usage_db()

            conn.execute(
                """
                INSERT INTO api_usage (
                    created_at,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    cost
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "gpt-4.1-mini",
                    prompt_tokens,
                    completion_tokens,
                    usage.total_tokens,
                    cost
                )
            )

            conn.commit()
            conn.close()

        result = (
            response
            .choices[0]
            .message
            .content
            .strip()
        )

        # カギ括弧除去
        result = result.replace("「", "")
        result = result.replace("」", "")

        # 句読点除去
        result = result.replace("。", "")
        result = result.replace("、", " ")

        print(
            f"[AI SENTENCE KANA] "
            f"{text} -> {result}"
        )

        words = re.findall(
            r"[a-zA-Z]+",
            normalize(text)
        )

        for w in words:

            w = w.lower()

            if w not in WORD_KANA_DICT:

                UNKNOWN_WORDS[w] = (
                    UNKNOWN_WORDS.get(w, 0) + 1
                )

                print(f"[UNKNOWN WORD] {w}")
                ai_katakana(w)
        result = tune_katakana(result)
        
        save_katakana_cache(
            text,
            result
        )

        return result

    except Exception as e:

        print(
            f"[AI SENTENCE ERROR] {e}"
        )

        return text
# -----------------------
# AI翻訳
# -----------------------
def ai_translate(text):

    global AI_TRANS_COUNT

    AI_TRANS_COUNT += 1
    
    key = normalize(text)

    # キャッシュ
    if key in TRANSLATION_CACHE:

        print(
            f"[TRANS CACHE] {key}"
        )

        return TRANSLATION_CACHE[key]

    try:

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content":
                    "Translate English to natural Japanese."
                },
                {
                    "role": "user",
                    "content": text
                }
            ],
            temperature=0
        )

        usage = getattr(response, "usage", None)

        if usage:

            prompt_tokens = usage.prompt_tokens
            completion_tokens = usage.completion_tokens

            cost = (
                prompt_tokens * 0.0000004 +
                completion_tokens * 0.0000016
            )

            print(
                f"[AI TRANS COST] "
                f"prompt={prompt_tokens} "
                f"completion={completion_tokens} "
                f"cost=${cost:.8f}"
            )

            conn = get_usage_db()

            conn.execute(
                """
                INSERT INTO api_usage (
                    created_at,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    cost
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "gpt-4.1-mini",
                    prompt_tokens,
                    completion_tokens,
                    usage.total_tokens,
                    cost
                )
            )

            conn.commit()
            conn.close()

        result = (
            response
            .choices[0]
            .message
            .content
            .strip()
        )

        print(f"[AI TRANS] {result}")

        save_translation_cache(
            text,
            result
        )

        return result

    except Exception as e:

        print(f"[AI ERROR] {e}")

        return text

# -----------------------
# 翻訳
# -----------------------   
def translate(text):

    global TRANS_CACHE_HIT
    global TRANS_TOTAL

    TRANS_TOTAL += 1

    key = normalize(text)
    
    #print(f"[KEY] {key}")

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

    # サイズ確認
    if (
        "sure" in key
        and "what size would you like" in key
    ):

        print("[HIT] SIZE_PATTERN")

        return (
            "かしこまりました。"
            "どのサイズになさいますか？"
        )
    # ready soon
    if (
        "your order will be ready soon"
        in key
    ):

        return (
            "ご注文はすぐに"
            "ご用意できます。"
        )
    # 少々お待ちください
    if (
        "sure" in key
        and "please wait a moment" in key
    ):

        print("[HIT] WAIT_PATTERN")

        return (
            "かしこまりました。"
            "少々お待ちください。"
        )
        

    
    # キャッシュ
    if key in TRANSLATION_CACHE:

        TRANS_CACHE_HIT += 1

        print(
            f"[TRANS CACHE HIT] {key}"
        )

        return TRANSLATION_CACHE[key]
    # 完全一致
    if key in TRANSLATE_DICT:

        #print(f"[HIT] EXACT: {key}")

        return TRANSLATE_DICT[key]

    # 長文優先部分一致
    partial = partial_match_translate(
        key,
        TRANSLATE_DICT
    )
    #print(f"[PARTIAL RESULT] {partial}")
    
    if partial:

        #print(f"[HIT] PARTIAL: {key}")

        return partial

    #print("[MISS AI]")

    return ai_translate(text)
# -----------------------
# 辞書のみ翻訳
# ---------------
def translate_dict_only(text):

    key = normalize(text)

    # 金額
    m = re.search(
        r"that will be (\d+) yen",
        key
    )
    if m:
        return f"お会計は{m.group(1)}円です"

    # 特殊
    if (
        "sure" in key
        and "hot or iced" in key
    ):

        return (
            "かしこまりました。"
            "ホットかアイス、"
            "どちらにしますか？"
        )

    # 完全一致
    if key in TRANSLATE_DICT:
        return TRANSLATE_DICT[key]

    # 部分一致
    partial = partial_match_translate(
        key,
        TRANSLATE_DICT
    )

    if partial:
        return partial

    # AIなし
    return ""
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
# 統計保存
# -----------------------
def save_stats():

    data = {
        "phrase": len(PHRASE_DICT),
        "trans": len(TRANSLATE_DICT),
        "word": len(WORD_KANA_DICT),
        "native": len(NATIVE_DICT),
        "kana_cache": len(KATAKANA_CACHE),
        "trans_cache": len(TRANSLATION_CACHE)
    }

    with open(
        STATS_PATH,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    print("[SAVE STATS]")
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
    
    # -----------------------
    # 前回統計
    # -----------------------
    old_stats = load_json(STATS_PATH)

    phrase_diff = (
        len(PHRASE_DICT)
        - old_stats.get("phrase", 0)
    )

    trans_diff = (
        len(TRANSLATE_DICT)
        - old_stats.get("trans", 0)
    )

    word_diff = (
        len(WORD_KANA_DICT)
        - old_stats.get("word", 0)
    )

    native_diff = (
        len(NATIVE_DICT)
        - old_stats.get("native", 0)
    )

    kana_cache_diff = (
        len(KATAKANA_CACHE)
        - old_stats.get("kana_cache", 0)
    )

    trans_cache_diff = (
        len(TRANSLATION_CACHE)
        - old_stats.get("trans_cache", 0)
    )
    
    return render_template(
        "index.html",
        data=data,
        phrase_count=len(PHRASE_DICT),
        trans_count=len(TRANSLATE_DICT),
        word_count=len(WORD_KANA_DICT),
        native_count=len(NATIVE_DICT),
        kana_cache_count=len(KATAKANA_CACHE),
        trans_cache_count=len(TRANSLATION_CACHE),

        phrase_diff=phrase_diff,
        trans_diff=trans_diff,
        word_diff=word_diff,
        native_diff=native_diff,
        kana_cache_diff=kana_cache_diff,
        trans_cache_diff=trans_cache_diff,

        ai_kana_count=AI_KANA_COUNT,
        ai_trans_count=AI_TRANS_COUNT,
 
        kana_hit_rate=(
            int(KANA_CACHE_HIT / KANA_TOTAL * 100)
            if KANA_TOTAL else 0
  ),

  trans_hit_rate=(
      int(TRANS_CACHE_HIT / TRANS_TOTAL * 100)
      if TRANS_TOTAL else 0
  )
        
)
# -----------------------
# 管理画面
# -----------------------
@app.route("/eng/admin")
def admin():

    q = request.args.get(
        "q",
        ""
    ).strip().lower()

    old_stats = load_json(STATS_PATH)

    phrase_hits = {}
    trans_hits = {}
    word_hits = {}
    native_hits = {}
    kana_cache_hits = {}
    trans_cache_hits = {}

    phrase_diff = (
        len(PHRASE_DICT)
        - old_stats.get("phrase", 0)
    )

    trans_diff = (
        len(TRANSLATE_DICT)
        - old_stats.get("trans", 0)
    )

    word_diff = (
        len(WORD_KANA_DICT)
        - old_stats.get("word", 0)
    )

    native_diff = (
        len(NATIVE_DICT)
        - old_stats.get("native", 0)
    )

    kana_cache_diff = (
        len(KATAKANA_CACHE)
        - old_stats.get("kana_cache", 0)
    )

    trans_cache_diff = (
        len(TRANSLATION_CACHE)
        - old_stats.get("trans_cache", 0)
    )

    kana_hit_rate = (
        int(KANA_CACHE_HIT / KANA_TOTAL * 100)
        if KANA_TOTAL else 0
    )

    trans_hit_rate = (
        int(TRANS_CACHE_HIT / TRANS_TOTAL * 100)
        if TRANS_TOTAL else 0
    )

    # -----------------------
    # API Usage Summary
    # -----------------------
    conn = get_usage_db()

    usage_summary = conn.execute(
        """
        SELECT
            COUNT(*) as calls,
            ROUND(SUM(cost),4) as total_cost,
            ROUND(AVG(total_tokens),1) as avg_tokens
        FROM api_usage
        """
    ).fetchone()

    conn.close()

    # -----------------------
    # API usage実測
    # -----------------------
    conn = get_usage_db()

    row = conn.execute(
        """
        SELECT
            COUNT(*) as calls,
            ROUND(SUM(cost),8) as total_cost,
            SUM(total_tokens) as total_tokens,
            ROUND(AVG(cost),8) as avg_cost
        FROM api_usage
        """
    ).fetchone()

    conn.close()

    api_calls = row["calls"] or 0
    real_api_cost = row["total_cost"] or 0
    total_tokens = row["total_tokens"] or 0
    avg_cost = row["avg_cost"] or 0

    remain_budget = (
        API_BUDGET_USD - real_api_cost
    )
    api_budget_yen = API_BUDGET_USD * USD_TO_JPY
    remain_budget_yen = remain_budget * USD_TO_JPY
    usage_percent = (
        real_api_cost / API_BUDGET_USD * 100
    )

    # -----------------------
    # OpenAI billing
    # -----------------------
    billing = get_openai_balance()

    openai_total_granted = (
        billing["total_granted"]
    )

    openai_total_used = (
        billing["total_used"]
    )

    openai_total_available = (
        billing["total_available"]
    )

    openai_total_cost = (
        get_openai_total_cost()
    )

    # -----------------------
    # API料金推定
    # -----------------------
    kana_cost = 0.00015
    trans_cost = 0.0003

    estimated_cost = (
        AI_KANA_COUNT * kana_cost
        + AI_TRANS_COUNT * trans_cost
    )

    estimated_yen = (
        estimated_cost * 150
    )

    if q:

        phrase_hits = {
            k: v
            for k, v in PHRASE_DICT.items()
            if q in k
        }

        trans_hits = {
            k: v
            for k, v in TRANSLATE_DICT.items()
            if q in k
        }

        word_hits = {
            k: v
            for k, v in WORD_KANA_DICT.items()
            if q in k
        }

        native_hits = {
            k: v
            for k, v in NATIVE_DICT.items()
            if q in k
        }

        kana_cache_hits = {
            k: v
            for k, v in KATAKANA_CACHE.items()
            if q in k
        }

        trans_cache_hits = {
            k: v
            for k, v in TRANSLATION_CACHE.items()
            if q in k
        }

    return render_template(
        "admin.html",

        phrase_count=len(PHRASE_DICT),
        trans_count=len(TRANSLATE_DICT),
        word_count=len(WORD_KANA_DICT),
        native_count=len(NATIVE_DICT),
        kana_cache_count=len(KATAKANA_CACHE),
        trans_cache_count=len(TRANSLATION_CACHE),

        phrase_diff=phrase_diff,
        trans_diff=trans_diff,
        word_diff=word_diff,
        native_diff=native_diff,
        kana_cache_diff=kana_cache_diff,
        trans_cache_diff=trans_cache_diff,

        ai_kana_count=AI_KANA_COUNT,
        ai_trans_count=AI_TRANS_COUNT,

        kana_hit_rate=kana_hit_rate,
        trans_hit_rate=trans_hit_rate,

        q=q,

        phrase_hits=phrase_hits,
        trans_hits=trans_hits,
        word_hits=word_hits,
        native_hits=native_hits,
        kana_cache_hits=kana_cache_hits,
        trans_cache_hits=trans_cache_hits,

        estimated_cost=estimated_cost,
        estimated_yen=estimated_yen,

        real_api_cost=real_api_cost,
        total_tokens=total_tokens,
        api_calls=api_calls,
        avg_cost=avg_cost,

        api_budget_usd=API_BUDGET_USD,
        remain_budget=remain_budget,
        usage_percent=usage_percent,

        api_budget_yen=api_budget_yen,
        remain_budget_yen=remain_budget_yen,
        
        openai_total_granted=openai_total_granted,
        openai_total_used=openai_total_used,
        openai_total_available=openai_total_available,
        openai_total_cost=openai_total_cost,

        usage_summary=usage_summary,

        unknown_words=sorted(
            UNKNOWN_WORDS.items(),
            key=lambda x: x[1],
            reverse=True
        )[:30]
    )

# -----------------------
# API Usage ダッシュボード
# -----------------------
@app.route("/eng/admin_usage")
def admin_usage():

    conn = get_usage_db()

    summary = conn.execute(
        """
        SELECT
            COUNT(*) as calls,
            ROUND(SUM(cost),4) as total_cost,
            ROUND(AVG(total_tokens),1) as avg_tokens
        FROM api_usage
        """
    ).fetchone()

    today = datetime.now().strftime("%Y-%m-%d")

    today_data = conn.execute(
        """
        SELECT
            COUNT(*) as calls,
            ROUND(SUM(cost),4) as cost
        FROM api_usage
        WHERE created_at LIKE ?
        """,
        (f"{today}%",)
    ).fetchone()

    daily = conn.execute(
        """
        SELECT
            substr(created_at,1,10) as day,
            COUNT(*) as calls,
            SUM(total_tokens) as tokens,
            ROUND(SUM(cost),4) as cost
        FROM api_usage
        GROUP BY day
        ORDER BY day DESC
        """
    ).fetchall()

    model_stats = conn.execute(
        """
        SELECT
            model,
            COUNT(*) as calls,
            ROUND(SUM(cost),4) as cost
        FROM api_usage
        GROUP BY model
        """
    ).fetchall()

    conn.close()
    # 円換算追加
    summary_cost_yen = float(summary["total_cost"]) * USD_TO_JPY
    today_cost_yen = float(today_data["cost"]) * USD_TO_JPY

    daily_yen = []

    for r in daily:
        row = dict(r)
        row["cost_yen"] = float(r["cost"]) * USD_TO_JPY
        daily_yen.append(row)

    model_stats_yen = []

    for r in model_stats:
        row = dict(r)
        row["cost_yen"] = float(r["cost"]) * USD_TO_JPY
        model_stats_yen.append(row)
    # -----------------------
    # API COST SUMMARY
    # -----------------------

    real_cost = float(summary["total_cost"])

    total_tokens = 0

    for r in daily:
        total_tokens += int(r["tokens"])

    api_calls = int(summary["calls"])

    if api_calls > 0:
        avg_cost = real_cost / api_calls
    else:
        avg_cost = 0

    avg_cost_yen = avg_cost * USD_TO_JPY
    budget = 5.00

    remain = budget - real_cost

    usage_rate = (real_cost / budget) * 100

    real_cost_yen = real_cost * USD_TO_JPY
    budget_yen = budget * USD_TO_JPY
    remain_yen = remain * USD_TO_JPY
    
        
    return render_template(
        "admin_usage.html",
        summary=summary,
        today_data=today_data,
        daily=daily_yen,
        model_stats=model_stats_yen,

        summary_cost_yen=summary_cost_yen,
        today_cost_yen=today_cost_yen,
        
        real_cost=real_cost,
        total_tokens=total_tokens,
        api_calls=api_calls,
        avg_cost=avg_cost,
        budget=budget,
        remain=remain,
        usage_rate=usage_rate,
        real_cost_yen=real_cost_yen,
        budget_yen=budget_yen,
        remain_yen=remain_yen,
        avg_cost_yen=avg_cost_yen,
    )
# -----------------------
# お気に入り一覧
# -----------------------
@app.route("/eng/favorites")
def favorites():

    conn = get_db()

    data = conn.execute(
        """
        SELECT *
        FROM conversations
        WHERE favorite=1
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "favorites.html",
        data=data
    )
# -----------------------
# お気に入り例文一覧
# -----------------------
@app.route("/eng/favorite_messages")
def favorite_messages():

    conn = get_db()

    data = conn.execute(
        """
        SELECT
            messages.*,
            conversations.title
        FROM messages
        LEFT JOIN conversations
        ON messages.conversation_id
           = conversations.id
        WHERE messages.favorite=1
        ORDER BY messages.id DESC
        """
    ).fetchall()

    conn.close()

    return render_template(
        "favorite_messages.html",
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

            elif re.match(r"^B\d*:", line):

                speakers.append("B")

                text = re.sub(r"^B\d*:\s*", "", line)

                texts.append(text.strip())
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
            INSERT INTO conversations
            (
                title,
                category
            )
            VALUES (?, ?)
            """,
            (
                title,
                "一般英語"
            )
        )

        conv_id = cur.lastrowid

        for i in range(len(texts)):

            text = texts[i]

            kana = to_katakana(text)

            kana_native = convert_native_kana(kana)

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
                kana_native            
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
# 例文お気に入り切替
# -----------------------
@app.route(
    "/eng/message_favorite/<int:id>",
    methods=["POST"]
)
def toggle_message_favorite(id):

    conn = get_db()

    row = conn.execute(
        """
        SELECT favorite,
               conversation_id
        FROM messages
        WHERE id=?
        """,
        (id,)
    ).fetchone()

    if row:

        new_value = (
            0
            if row["favorite"] == 1
            else 1
        )

        conn.execute(
            """
            UPDATE messages
            SET favorite=?
            WHERE id=?
            """,
            (new_value, id)
        )

        conn.commit()

        conversation_id = row["conversation_id"]

    else:

        conversation_id = 0

    conn.close()

    return jsonify({
        "success": True,
        "favorite": new_value
    })
# -----------------------
# 文法ノート
# -----------------------
@app.route(
    "/eng/message_grammar/<int:id>",
    methods=["GET", "POST"]
)
def message_grammar(id):

    conn = get_db()

    if request.method == "POST":

        grammar_note = request.form.get(
            "grammar_note",
            ""
        ).strip()

        grammar_note = re.sub(
            r'\n\s*\n+',
            '\n',
            grammar_note
        )
        conn.execute(
            """
            UPDATE messages
            SET grammar_note=?
            WHERE id=?
            """,
            (
                grammar_note,
                id
            )
        )

        conn.commit()

        row = conn.execute(
            """
            SELECT conversation_id
            FROM messages
            WHERE id=?
            """,
            (id,)
        ).fetchone()

        conn.close()

        return redirect(
            url_for(
                "detail_multi",
                id=row["conversation_id"]
            )
        )

    row = conn.execute(
        """
        SELECT *
        FROM messages
        WHERE id=?
        """,
        (id,)
    ).fetchone()

    conn.close()

    return render_template(
        "message_grammar.html",
        row=row
    )    
# -----------------------
# 個別再翻訳
# -----------------------
@app.route("/eng/retranslate/<int:id>", methods=["POST"])
def retranslate(id):

    conn = get_db()

    m = conn.execute(
        "SELECT * FROM messages WHERE id=?",
        (id,)
    ).fetchone()

    if not m:

        conn.close()

        return {"status": "error"}

    text = m["text"]

    kana = to_katakana(text)

    kana_native = convert_native_kana(kana)

    conn.execute("""
    UPDATE messages
    SET japanese=?,
        kana=?,
        kana_native=?
    WHERE id=?
    """, (
        translate(text),
        kana,
        kana_native,
        id
    ))

    conn.commit()

    conn.close()

    return {"status": "ok"}
# -----------------------
# 全件再変換
# -----------------------
@app.route("/retranslate_all", methods=["POST"])
@app.route("/eng/retranslate_all", methods=["POST"])
def retranslate_all():

    conn = get_db()

    messages = conn.execute(
        """
        SELECT * FROM messages
        LIMIT 200
       """
    ).fetchall()

    for m in messages:

        text = m["text"]

        # 日本語
        try:

            new_japanese = translate_dict_only(text)

            # 空欄なら既存維持
            if new_japanese.strip():

                japanese = new_japanese

            else:

                japanese = m["japanese"]

        except Exception as e:

            print(f"[RETRANS ERROR] {e}")

            japanese = m["japanese"]

        # カタカナ
        try:

            kana = to_katakana(text)

            kana_native = convert_native_kana(kana)

        except Exception as e:

            print(f"[KANA ERROR] {e}")

            kana = text

            kana_native = text

        conn.execute("""
        UPDATE messages
        SET japanese=?,
            kana=?,
            kana_native=?
        WHERE id=?
        """, (
            japanese,
            kana,
            kana_native,
            m["id"]
        ))

    conn.commit()

    conn.close()

    return redirect("/eng/")
# -----------------------
# 編集
# -----------------------
@app.route(
    "/eng/edit/<int:id>",
    methods=["GET", "POST"]
)
def edit_conversation(id):

    conn = get_db()

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()
        category = request.form.get(
            "category",
            "一般英語"
        ).strip()

        subcategory = request.form.get(
            "subcategory",
            ""
        ).strip()

        lines_raw = request.form.get(
            "lines",
            ""
        ).strip()

        # 会話更新
        conn.execute(
            """
            UPDATE conversations
            SET title=?, category=?, subcategory=?
            WHERE id=?
            """,
            (title, category, subcategory, id)
        )
        

        # 既存削除
        conn.execute(
            """
            DELETE FROM messages
            WHERE conversation_id=?
            """,
            (id,)
        )

        lines = lines_raw.split("\n")

        speaker_toggle = "A"

        for line in lines:

            line = line.strip()

            if not line:
                continue

            if line.startswith("A:"):

                speaker = "A"
                text = line[2:].strip()

            elif re.match(r"^B\d*:", line):

                speaker = "B"

                text = re.sub(
                    r"^B\d*:\s*",
                    "",
                    line
          
                )
            else:

                speaker = speaker_toggle
                text = line

                speaker_toggle = (
                    "B"
                    if speaker_toggle == "A"
                    else "A"
                )

            kana = to_katakana(text)

            japanese = translate(text)

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
                id,
                speaker,
                text,
                japanese,
                kana,
                kana
            ))

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "detail_multi",
                id=id
            )
        )

    # GET
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

    lines = []

    for r in rows:

        lines.append(
            f"{r['speaker']}: {r['text']}"
        )

    return render_template(
        "edit_multi.html",
        conv=conv,
        lines="\n".join(lines)
    )
# -----------------------
# お気に入り切替
# -----------------------
@app.route(
    "/eng/favorite/<int:id>",
    methods=["POST"]
)
def toggle_favorite(id):

    conn = get_db()

    row = conn.execute(
        """
        SELECT favorite
        FROM conversations
        WHERE id=?
        """,
        (id,)
    ).fetchone()

    if row:

        new_value = (
            0
            if row["favorite"] == 1
            else 1
        )

        conn.execute(
            """
            UPDATE conversations
            SET favorite=?
            WHERE id=?
            """,
            (new_value, id)
        )

        conn.commit()

    conn.close()

    return jsonify({
        "success": True,
        "favorite": new_value
    })
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
# 統計保存
# -----------------------
@app.route(
    "/eng/save_stats",
    methods=["GET", "POST"]
)
def save_stats_route():

    save_stats()

    return redirect("/eng/")
# -----------------------
# ニュース英語
# -----------------------
@app.route("/eng/news_add")
def news_add():

    return render_template(
        "news_add.html"
    )

@app.route(
    "/eng/news_import",
    methods=["POST"]
)
def news_import():

    raw = request.form.get(
        "news_text",
        ""
    )
    conv_title = request.form.get(
        "conv_title",
        "ニュース英語"
    ).strip()

    
    lines = []

    for line in raw.splitlines():

        line = line.strip()

        if not line:
            continue

        # -----------------
        # NHK共有ボタン除去
        # -----------------
        if (
            "Facebook" in line
            or "XShare" in line
            or "Share" == line
        ):
            continue

        # -----------------
        # 記号だけ除去
        # -----------------
        if re.fullmatch(
            r"[•￼\s]+",
            line
        ):
            continue

        lines.append(line)

    title = ""
    body = ""

    if len(lines) >= 1:

        # 初期化
        body = ""

        # -----------------
        # NHK WORLD型
        # 日付行あり
        # -----------------
        if (
            len(lines) >= 2
            and re.search(r"\d{1,2}:\d{2}", lines[1])
        ):

            title = lines[0]

            raw_body = " ".join(lines[2:])

            body = split_news_sentences(raw_body)

        # -----------------
        # メール型
        # タイトル2行
        # -----------------
        elif len(lines) >= 3:

            title = (
                lines[0]
                + " "
                + lines[1]
            )

            raw_body = " ".join(lines[2:])

            body = split_news_sentences(raw_body)

        # -----------------
        # 1行だけ
        # -----------------
        else:

            title = lines[0]

        # -----------------
        # タイトル末尾の
        # "7 hours ago"除去
        # -----------------
        title = re.sub(
            r"\s+\d+\s+(hour|hours|day|days)\s+ago$",
            "",
            title
        )

        texts = [title]

        speakers = ["A"]

        for line in body.split("\n"):

            line = line.strip()

            if not line:
                continue

            text = re.sub(
                r"^B\d*:\s*",
                "",
                line
            )

            texts.append(text)

            speakers.append("B")

    conn = get_db()

    cur = conn.execute(
        """
        INSERT INTO conversations
        (
            title,
            category
        )
        VALUES (?, ?)
        """,
        (
            conv_title,
            "ニュース英語"
        )
    )

    conv_id = cur.lastrowid

    for i in range(len(texts)):

        text = texts[i]

        # B1:, B2: 除去
        if speakers[i] == "B":

            text = re.sub(
                r"^B\d*:\s*",
                "",
                text
            )

        speaker = speakers[i]

        kana = to_katakana(text)

        kana_native = convert_native_kana(
            kana
        )

        japanese = translate(text)

        issues, warnings = detect_ng(
            text,
            kana,
            japanese
        )

        conn.execute(
            """
            INSERT INTO messages
            (
                conversation_id,
                speaker,
                text,
                japanese,
                kana,
                kana_native
            )
            VALUES
            (?, ?, ?, ?, ?, ?)
            """,
            (
                conv_id,
                speaker,
                text,
                japanese,
                kana,
                kana_native
            )
        )

    conn.commit()

    conn.close()

    
    return redirect(
        f"/eng/detail_multi/{conv_id}"
    )

print("TEST_USAGE_ROUTE_LOADED")
# -----------------------
# test_usage route
# -----------------------
@app.route("/eng/test_usage")
def test_usage():

    try:
        from openai import OpenAI

        client = OpenAI()

        usage = client.organization.costs.list()

        result = []

        total_cost = 0.0

        for item in usage.data:

            try:
                amount = item.amount.value
                currency = item.amount.currency

                total_cost += amount

                result.append({
                    "amount": amount,
                    "currency": currency
                })

            except Exception as e:
                print(f"[USAGE ITEM ERROR] {e}")

        html = f"""
        <h2>OpenAI Usage Test</h2>

        <p><b>Total Cost:</b> ${total_cost:.4f}</p>

        <pre>{result}</pre>
        """

        return html

    except Exception as e:

        print(f"[USAGE ERROR] {e}")

        return f"""
        <h2>Usage API Error</h2>
        <pre>{e}</pre>
        """
# -----------------------
# 起動
# -----------------------
if __name__ == "__main__":

    init_db()

    app.run(
        host="0.0.0.0",
        port=5005
    )