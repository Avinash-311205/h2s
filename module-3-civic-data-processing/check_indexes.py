import sqlite3
conn = sqlite3.connect('civic_processing.db')
cursor = conn.cursor()
cursor.execute('SELECT * FROM sqlite_master WHERE type="index"')
for r in cursor.fetchall():
    print(r)
conn.close()