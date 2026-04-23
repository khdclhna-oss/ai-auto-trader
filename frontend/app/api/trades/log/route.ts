import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
import { Pool } from 'pg'

const pool = new Pool({ connectionString: process.env.DATABASE_URL, ssl: { rejectUnauthorized: false } })

export async function GET() {
  const client = await pool.connect()
  try {
    const { rows } = await client.query(`
      SELECT id, stock, action, entry_price, exit_price, quantity, pnl, reason, 
             entry_time, exit_time, status, confluence_score, regime, atr_at_entry, sentiment_score, charges
      FROM trades 
      WHERE status IN ('OPEN', 'CLOSED', 'SIGNAL')
      ORDER BY entry_time DESC LIMIT 50
    `)
    
    const processedRows = rows.map(row => {
      let holding_period = null
      let gross_profit = null
      let taxes = null
      
      if (row.entry_time && row.exit_time && row.status === 'CLOSED') {
        const start = new Date(row.entry_time).getTime()
        const end = new Date(row.exit_time).getTime()
        const diffMs = end - start
        
        const days = Math.floor(diffMs / (1000 * 60 * 60 * 24))
        const hours = Math.floor((diffMs % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60))
        const minutes = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60))
        
        if (days > 0) {
            holding_period = `${days}d ${hours}h`
        } else {
            holding_period = `${hours}h ${minutes}m`
        }
        
        // [P2 FIX] gross_profit = (exit - entry) * qty is already correctly signed for long exits.
        // Negating again when action === 'SELL' was inverting winners into losers in the UI.
        gross_profit = (Number(row.exit_price) - Number(row.entry_price)) * Number(row.quantity)
        
        // Use DB charges column if available (written by calculator), else derive from gross - net
        taxes = row.charges != null ? Number(row.charges) : gross_profit - Number(row.pnl || 0)
      }
      return { ...row, holding_period, gross_profit, taxes }
    })
    
    return NextResponse.json(processedRows)
  } finally { client.release() }
}
