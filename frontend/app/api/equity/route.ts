import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
import { pool } from '@/lib/db'

export async function GET() {
  const client = await pool.connect()
  try {
    const { rows } = await client.query(`
      SELECT capital, snapshot_at FROM equity_snapshots ORDER BY snapshot_at ASC
    `)
    return NextResponse.json(rows)
  } finally { client.release() }
}
