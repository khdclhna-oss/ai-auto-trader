import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
import { pool } from '@/lib/db'

export async function GET() {
  const client = await pool.connect()
  try {
    const { rows } = await client.query(`
      SELECT capital, invested, cash,
             (capital - 100000) as pnl,
             ((capital - 100000) / 100000 * 100) as pnl_pct
      FROM portfolio ORDER BY updated_at DESC LIMIT 1
    `)
    return NextResponse.json(rows[0] || { capital: 100000, invested: 0, cash: 100000, pnl: 0, pnl_pct: 0 })
  } finally { client.release() }
}
