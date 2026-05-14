import sqlite3
import time
from pathlib import Path
import ahocorasick
# === НАСТРОЙКИ ===
DB_FILE        = r"D:\InstAccountsManager\InstAccountsManager\DataBases\recV2.pb"
BAD_WORDS_FILE = r"C:\Users\Administrator\Desktop\Desktop\bad_words.txt"
OUT_FILE       = r"C:\Users\Administrator\Desktop\ids2.txt"
# === ЗАГРУЗКА ПЛОХИХ СЛОВ В AHO-CORASICK АВТОМАТ ===
print("Загрузка плохих слов...")
A = ahocorasick.Automaton()
with open(BAD_WORDS_FILE, encoding="utf-8") as f:
    n = 0
    for line in f:
        w = line.strip().lower()
        if w:
            A.add_word(w, w)
            n += 1
A.make_automaton()
print(f"Загружено: {n} слов")
# === ASCII-ПРОВЕРКА ===
def is_ascii_with_letter(s):
    if not s:
        return False
    has_letter = False
    for ch in s:
        if ord(ch) > 127:
            return False
        if ch.isalpha():
            has_letter = True
    return has_letter
# === ОСНОВНАЯ ВЫГРУЗКА ===
print("Подключение к БД...")
conn = sqlite3.connect(DB_FILE)
conn.execute("PRAGMA cache_size=-1048576")    # 1 GB кэш
conn.execute("PRAGMA temp_store=MEMORY")
conn.execute("PRAGMA mmap_size=2147483648")   # 2 GB mmap
cur = conn.cursor()
cur.execute("""
    SELECT user_id, full_name FROM Users
    WHERE is_private=0
""")
t0 = time.time()
checked = kept = 0
with open(OUT_FILE, "w", encoding="ascii", buffering=1024*1024) as out:
    for user_id, full_name in cur:
        checked += 1
        if not is_ascii_with_letter(full_name):
            continue
        # Aho-Corasick: ищет любое из 50k слов одним проходом
        name_lower = full_name.lower()
        try:
            next(A.iter(name_lower))
            # нашли плохое слово -> пропускаем
            continue
        except StopIteration:
            pass
        out.write(f"{user_id}\n")
        kept += 1
        if checked % 500000 == 0:
            elapsed = time.time() - t0
            rate = checked / elapsed
            print(f"Обработано: {checked:,} / оставлено: {kept:,} / {rate:,.0f} строк/сек")
elapsed = time.time() - t0
print(f"\nГотово: {kept:,} из {checked:,} за {elapsed:.1f} сек")
print(f"Файл: {OUT_FILE}")
conn.close()
