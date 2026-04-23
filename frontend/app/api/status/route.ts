import { NextResponse } from 'next/server'
export const dynamic = 'force-dynamic'
import { pool } from '@/lib/db'

export async function GET() {
  const client = await pool.connect()
  try {
    // Last 20 runs
    const runs = await client.query(`
      SELECT id, started_at, finished_at, status, market_open,
             stocks_scanned, signals_fired, trades_executed,
             error_message, duration_ms, log_lines
      FROM run_logs
      ORDER BY started_at DESC
      LIMIT 20
    `)

    const latest = runs.rows[0] || null

    // Calculate "next run" — cron fires every 15 min during market hours
    const now = new Date()
    const istOffset = 5.5 * 60 * 60 * 1000
    const nowIST = new Date(now.getTime() + istOffset)
    const hours = nowIST.getUTCHours()
    const mins = nowIST.getUTCMinutes()
    const totalMins = hours * 60 + mins

    const marketOpen = 9 * 60 + 15   // 9:15 AM
    const marketClose = 15 * 60 + 30  // 3:30 PM
    const isMarketOpen = totalMins >= marketOpen && totalMins <= marketClose

    // Time since last run
    const lastRunAge = latest
      ? Math.floor((Date.now() - new Date(latest.started_at).getTime()) / 1000 / 60)
      : null

    // Count consecutive failures
    const recentStatuses = runs.rows.slice(0, 5).map(r => r.status)
    const consecutiveErrors = recentStatuses.findIndex(s => s !== 'ERROR')
    const hasRecentError = recentStatuses.includes('ERROR')

    return NextResponse.json({
      latest,
      runs: runs.rows,
      isMarketOpen,
      lastRunAgeMinutes: lastRunAge,
      consecutiveErrors: consecutiveErrors === -1 ? recentStatuses.length : consecutiveErrors,
      hasRecentError,
      systemHealthy: latest?.status !== 'ERROR' && (lastRunAge === null || lastRunAge < 20 || !isMarketOpen),
    })
  } finally { client.release() }
}
