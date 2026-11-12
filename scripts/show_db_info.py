import sqlite3

conn = sqlite3.connect('companion.db')
c = conn.cursor()

tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)

for t in tables:
    count = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"Table {t}: {count} rows")

print("\n--- Telegram Accounts (or whatever stores accounts) ---")
for t in tables:
    cols = [col[1] for col in c.execute(f"PRAGMA table_info({t})").fetchall()]
    if 'user_id' in cols and t not in ('messages', 'leads', 'analytics_events'):
        print(f"Sample from {t}:")
        for row in c.execute(f"SELECT * FROM {t} WHERE user_id = 505").fetchall():
            print(" ", row)
