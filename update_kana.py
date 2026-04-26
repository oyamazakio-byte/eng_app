from openai import OpenAI
import sqlite3

client = OpenAI()

DB_NAME = "conversation.db"

def generate_kana(text):
    if not text:
        return ""

    prompt = f"""
以下の英語の発音をカタカナで表記してください。

英語: {text}

【ルール（必ず守る）】
・ネイティブ発音に近づける
・Good は必ず「グッ」にする（グッド禁止）

例:
Good morning → グッ モーニング
Good afternoon → グッ アフタヌーン

カタカナのみ出力
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )

        kana = response.choices[0].message.content.strip()

        # ★ 強制補正（これが決定打）
        kana = kana.replace("グッド", "グッ")
        kana = kana.replace("・", " ")

        return kana

    except Exception as e:
        print("エラー:", e)
        return ""


def main():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT id, english_a, english_b FROM conversations").fetchall()

    for r in rows:
        print(f"更新中 ID={r['id']}")

        kana_a = generate_kana(r["english_a"])
        kana_b = generate_kana(r["english_b"])

        conn.execute("""
        UPDATE conversations
        SET katakana_a=?, katakana_b=?
        WHERE id=?
        """, (kana_a, kana_b, r["id"]))

        conn.commit()

    conn.close()
    print("完了！")


if __name__ == "__main__":
    main()