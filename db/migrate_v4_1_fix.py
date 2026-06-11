import psycopg2

def migrate():
    db_url = 'postgresql://neondb_owner:npg_ie0GzmROxE9f@ep-proud-bird-an4ydv35-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require'
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    try:
        print("Adding original_quantity to open_positions...")
        cur.execute("ALTER TABLE open_positions ADD COLUMN IF NOT EXISTS original_quantity INTEGER")
        conn.commit()
        print("Migration Successful!")
    except Exception as e:
        conn.rollback()
        print(f"Migration Failed: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    migrate()
