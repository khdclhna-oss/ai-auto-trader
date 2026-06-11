import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
import { pool } from '@/lib/db'

export async function GET() {
  const client = await pool.connect()
  try {
    // Win/loss stats from closed trades (exclude SIGNAL and HOLD)
    const trades = await client.query(`
      SELECT stock, action, entry_price, exit_price, pnl, entry_time, exit_time, 
             confluence_score, regime, atr_at_entry, sentiment_score, charges
      FROM trades 
      WHERE status = 'CLOSED' AND quantity > 0 AND pnl IS NOT NULL
      ORDER BY exit_time DESC
    `)

    const rows = trades.rows
    const netWins = rows.filter(t => Number(t.pnl) > 0)
    const netLosses = rows.filter(t => Number(t.pnl) <= 0)
    const totalTrades = rows.length
    const winRate = totalTrades > 0 ? (netWins.length / totalTrades) * 100 : 0
    
    // Net profit factor (post-fee)
    const netProfit = netWins.reduce((s, t) => s + Number(t.pnl), 0)
    const netLoss = Math.abs(netLosses.reduce((s, t) => s + Number(t.pnl), 0))
    const netPnl = netProfit - netLoss
    const netProfitFactor = netLoss > 0 ? netProfit / netLoss : 0

    // Gross profit factor (pre-fee): gross_pnl = net_pnl + charges
    const grossProfit = rows.reduce((s, t) => {
      const gross = Number(t.pnl) + Number(t.charges || 0)
      return gross > 0 ? s + gross : s
    }, 0)
    const grossLoss = Math.abs(rows.reduce((s, t) => {
      const gross = Number(t.pnl) + Number(t.charges || 0)
      return gross <= 0 ? s + gross : s
    }, 0))
    const grossProfitFactor = grossLoss > 0 ? grossProfit / grossLoss : 0

    const totalCharges = rows.reduce((s, t) => s + Number(t.charges || 0), 0)
    const dpFeeLeakage = rows.length * 15.93 // ₹13.50 + 18% GST flat per delivery sell

    const avgWin = netWins.length > 0 ? netProfit / netWins.length : 0
    const avgLoss = netLosses.length > 0 ? netLoss / netLosses.length : 0
    const payoffRatio = avgLoss > 0 ? avgWin / avgLoss : 0
    const expectancy = totalTrades > 0 ? (winRate / 100 * avgWin) - ((1 - winRate / 100) * avgLoss) : 0

    // Best and worst trades
    const bestTrade = netWins.length > 0 ? netWins.reduce((a, b) => Number(a.pnl) > Number(b.pnl) ? a : b) : null
    const worstTrade = netLosses.length > 0 ? netLosses.reduce((a, b) => Number(a.pnl) < Number(b.pnl) ? a : b) : null

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
      profitFactor: Math.round(netProfitFactor * 100) / 100, // kept for compatibility
      netProfitFactor: Math.round(netProfitFactor * 100) / 100,
      grossProfitFactor: Math.round(grossProfitFactor * 100) / 100,
      totalCharges: Math.round(totalCharges * 100) / 100,
      dpFeeLeakage: Math.round(dpFeeLeakage * 100) / 100,
      payoffRatio: Math.round(payoffRatio * 100) / 100,
      netPnl: Math.round(netPnl * 100) / 100,
      avgWin: Math.round(avgWin * 100) / 100,
      avgLoss: Math.round(avgLoss * 100) / 100,
      avgRR: Math.round(payoffRatio * 100) / 100, // kept for compatibility
      expectancy: Math.round(expectancy * 100) / 100,
      bestTrade: bestTrade ? { stock: bestTrade.stock, pnl: Number(bestTrade.pnl) } : null,
      worstTrade: worstTrade ? { stock: worstTrade.stock, pnl: Number(worstTrade.pnl) } : null,
      backtest: btResult.rows[0] || null,
      signalDistribution: signalDist.rows,
      regimeDistribution: regimeDist.rows,
    })
  } finally { client.release() }
}
