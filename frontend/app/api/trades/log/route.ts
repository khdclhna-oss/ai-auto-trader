import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
import { Pool } from 'pg'

const pool = new Pool({ connectionString: process.env.DATABASE_URL, ssl: { rejectUnauthorized: false } })

export async function GET() {
  const client = await pool.connect()
  try {
    const { rows } = await client.query(`
      SELECT id, stock, action, entry_price, exit_price, quantity, pnl, reason, 
             entry_time, exit_time, status, confluence_score, regime, atr_at_entry, sentiment_score
      FROM trades 
      WHERE status IN ('OPEN', 'CLOSED', 'SIGNAL')
      ORDER BY entry_time DESC LIMIT 50
    `)
    return NextResponse.json(rows)
  } finally { client.release() }
}
