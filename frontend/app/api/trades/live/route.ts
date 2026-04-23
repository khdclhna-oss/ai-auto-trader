import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
import { pool } from '@/lib/db'

export async function GET() {
  const client = await pool.connect()
  try {
    const { rows } = await client.query(`
      SELECT op.*, t.reason FROM open_positions op
      LEFT JOIN trades t ON t.stock = op.stock AND t.status = 'OPEN'
      ORDER BY op.entry_time DESC
    `)
    return NextResponse.json(rows)
  } finally { client.release() }
}
