import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
import { Pool } from 'pg'

const pool = new Pool({ connectionString: process.env.DATABASE_URL, ssl: { rejectUnauthorized: false } })

export async function GET() {
  const client = await pool.connect()
  try {
    const { rows } = await client.query(`
      SELECT capital, snapshot_at FROM equity_snapshots ORDER BY snapshot_at ASC
    `)
    return NextResponse.json(rows)
  } finally { client.release() }
}
