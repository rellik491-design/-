import sqlite3

conn = sqlite3.connect('companion.db')
c = conn.cursor()

print('=== USERS ===')
for row in c.execute('SELECT id, telegram_id, username, first_name, is_subscribed FROM users').fetchall():
    print(row)

print('\n=== ACCOUNTS ===')
for row in c.execute('SELECT id, user_id, phone, first_name, connection_type, is_active FROM accounts').fetchall():
    print(row)

print('\n=== CONTACTS (Alex or blocked or paused) ===')
for row in c.execute('SELECT id, user_id, telegram_user_id, first_name, username, status, bot_paused, paused_reason FROM contacts WHERE first_name LIKE "%Alex%" OR username LIKE "%mcputana%" OR bot_paused=1 OR status="blocked"').fetchall():
    print(row)

print('\n=== LEADS / INSULTS ===')
for row in c.execute('SELECT * FROM leads').fetchall():
    print(row)
