import psycopg2

def migrate():
    db_url = 'postgresql://neondb_owner:npg_ie0GzmROxE9f@ep-proud-bird-an4ydv35-pooler.c-6.us-east-1.aws.neon.tech/neondb?sslmode=require'
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    
    try:
        print("Starting V4.1 Database Migration...")
        
        # 1. Update open_positions to support 3 targets and tranches
        print("  - Updating 'open_positions' table...")
        # Check if we already renamed target to target_1 to avoid double-rename error
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'open_positions' AND column_name = 'target'")
        if cur.fetchone():
            cur.execute("ALTER TABLE open_positions RENAME COLUMN target TO target_1")
        
        cur.execute("""
            ALTER TABLE open_positions 
            ADD COLUMN IF NOT EXISTS target_2 NUMERIC,
            ADD COLUMN IF NOT EXISTS target_3 NUMERIC,
            ADD COLUMN IF NOT EXISTS tranches_exited INTEGER DEFAULT 0
        """)
        
        # 2. Update trades to track partial exits
        print("  - Updating 'trades' table...")
        cur.execute("""
            ALTER TABLE trades 
            ADD COLUMN IF NOT EXISTS tranches_exited INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS original_quantity INTEGER
        """)
        
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
