import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
import { pool } from '@/lib/db'

export async function GET() {
  const client = await pool.connect()
  try {
    // Win/loss stats from closed trades (exclude SIGNAL and HOLD)
    const trades = await client.query(`
      SELECT stock, action, entry_price, exit_price, pnl, entry_time, exit_time, 
             confluence_score, regime, atr_at_entry, sentiment_score
      FROM trades 
      WHERE status = 'CLOSED' AND quantity > 0 AND pnl IS NOT NULL
      ORDER BY exit_time DESC
    `)

    const rows = trades.rows
    const wins = rows.filter(t => Number(t.pnl) > 0)
    const losses = rows.filter(t => Number(t.pnl) <= 0)
    const totalTrades = rows.length
    const winRate = totalTrades > 0 ? (wins.length / totalTrades) * 100 : 0
    const grossProfit = wins.reduce((s, t) => s + Number(t.pnl), 0)
    const grossLoss = Math.abs(losses.reduce((s, t) => s + Number(t.pnl), 0))
    const netPnl = grossProfit - grossLoss
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : 0
    const avgWin = wins.length > 0 ? grossProfit / wins.length : 0
    const avgLoss = losses.length > 0 ? grossLoss / losses.length : 0
    const avgRR = avgLoss > 0 ? avgWin / avgLoss : 0
    const expectancy = totalTrades > 0 ? (winRate / 100 * avgWin) - ((1 - winRate / 100) * avgLoss) : 0

    // Best and worst trades
    const bestTrade = wins.length > 0 ? wins.reduce((a, b) => Number(a.pnl) > Number(b.pnl) ? a : b) : null
    const worstTrade = losses.length > 0 ? losses.reduce((a, b) => Number(a.pnl) < Number(b.pnl) ? a : b) : null

    // Backtest results (if any)
    const btResult = await client.query(`
      SELECT * FROM backtest_results ORDER BY run_at DESC LIMIT 1
    `)

    // Signal distribution
    const signalDist = await client.query(`
      SELECT action, COUNT(*) as count FROM signal_log GROUP BY action
    `)

    // Regime distribution  
    const regimeDist = await client.query(`
      SELECT regime, COUNT(*) as count FROM signal_log WHERE regime IS NOT NULL GROUP BY regime
    `)

    return NextResponse.json({
      totalTrades,
      winRate: Math.round(winRate * 100) / 100,
      profitFactor: Math.round(profitFactor * 100) / 100,
      netPnl: Math.round(netPnl * 100) / 100,
      avgWin: Math.round(avgWin * 100) / 100,
      avgLoss: Math.round(avgLoss * 100) / 100,
      avgRR: Math.round(avgRR * 100) / 100,
      expectancy: Math.round(expectancy * 100) / 100,
      bestTrade: bestTrade ? { stock: bestTrade.stock, pnl: Number(bestTrade.pnl) } : null,
      worstTrade: worstTrade ? { stock: worstTrade.stock, pnl: Number(worstTrade.pnl) } : null,
      backtest: btResult.rows[0] || null,
      signalDistribution: signalDist.rows,
      regimeDistribution: regimeDist.rows,
    })
  } finally { client.release() }
}
