import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

database_url = os.environ.get("DATABASE_URL")
print("Connecting...")

conn = psycopg2.connect(database_url)
cur = conn.cursor()
cur.execute("SELECT version();")
result = cur.fetchone()
print("SUCCESS! Postgres version:", result[0])
cur.close()
conn.close()