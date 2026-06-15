import { NextResponse } from 'next/server'
import { getPool } from '@/lib/db'

export async function GET() {
  const pool = getPool()
  const client = await pool.connect()
  try {
    // Try the signals table first, fallback gracefully if it doesn't exist
    const { rows } = await client.query(`
      SELECT stock, score, action, price, atr, stop_loss, target, reason, updated_at
      FROM signals
      ORDER BY ABS(score) DESC, updated_at DESC
    `)
    return NextResponse.json(rows)
  } catch {
    // signals table may not exist yet — return empty array
    return NextResponse.json([])
  } finally {
    client.release()
  }
}
