import sqlite3
conn = sqlite3.connect('civic_processing.db')
cursor = conn.cursor()
cursor.execute('SELECT name FROM sqlite_master WHERE type="table"')
tables = cursor.fetchall()
for t in tables:
    print(t[0])
    cursor.execute('PRAGMA table_info({})'.format(t[0]))
    cols = cursor.fetchall()
    for c in cols:
        print('  {}: {}'.format(c[1], c[2]))
    print()
conn.close()