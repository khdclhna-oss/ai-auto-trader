'use client'
import { useState } from 'react'
import useSWR from 'swr'
import { Line } from 'react-chartjs-2'
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, Title, Tooltip, Legend, Filler
} from 'chart.js'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Activity, Clock, LayoutDashboard, LineChart,
  TrendingUp, TrendingDown, BarChart2, Target, AlertTriangle, Zap
} from 'lucide-react'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Title, Tooltip, Legend)

type Portfolio = { capital: string|number; invested: string|number; cash: string|number; pnl: string|number; pnl_pct: string|number }
type LiveTrade = { stock: string; entry_price: string|number; quantity: string|number; stop_loss: string|number; target: string|number; entry_time: string; reason: string }
type TradeLog = { id: number; stock: string; action: string; entry_price: string|number; exit_price: string|number; pnl: string|number; reason: string; entry_time: string; status: string; confluence_score: number; regime: string }
type EquitySnap = { capital: string|number; snapshot_at: string }
type Analytics = {
  totalTrades: number; winRate: number; profitFactor: number; netPnl: number;
  avgWin: number; avgLoss: number; avgRR: number; expectancy: number;
  bestTrade: { stock: string; pnl: number } | null;
  worstTrade: { stock: string; pnl: number } | null;
  backtest: { sharpe_ratio: number; sortino_ratio: number; max_drawdown_pct: number; win_rate: number; profit_factor: number; total_pnl: number; total_trades: number; period_start: string; period_end: string } | null;
  signalDistribution: { action: string; count: number }[];
  regimeDistribution: { regime: string; count: number }[];
}

const fetcher = (url: string) => fetch(url).then(res => res.json())

function Stat({ label, value, sub, color = 'cyan', up }: { label: string; value: string; sub?: string; color?: string; up?: boolean }) {
  return (
    <div className="bg-white/5 border border-white/10 rounded-2xl p-5 backdrop-blur-sm">
      <p className="text-xs text-slate-400 uppercase tracking-widest mb-1">{label}</p>
      <p className={`text-2xl font-bold ${up === true ? 'text-emerald-400' : up === false ? 'text-red-400' : `text-${color}-400`}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  )
}

function MetricCard({ label, value, icon: Icon, good, bad }: { label: string; value: string | number; icon: any; good?: boolean; bad?: boolean }) {
  return (
    <div className={`rounded-xl p-4 border backdrop-blur-sm flex items-center gap-4 ${good ? 'bg-emerald-500/10 border-emerald-500/20' : bad ? 'bg-red-500/10 border-red-500/20' : 'bg-white/5 border-white/10'}`}>
      <div className={`p-2 rounded-lg ${good ? 'bg-emerald-500/20' : bad ? 'bg-red-500/20' : 'bg-white/10'}`}>
        <Icon className={`w-5 h-5 ${good ? 'text-emerald-400' : bad ? 'text-red-400' : 'text-slate-300'}`} />
      </div>
      <div>
        <p className="text-xs text-slate-400 uppercase tracking-wider">{label}</p>
        <p className={`text-xl font-bold ${good ? 'text-emerald-400' : bad ? 'text-red-400' : 'text-white'}`}>{value}</p>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [tab, setTab] = useState<'dashboard'|'live'|'log'|'analytics'>('dashboard')

  const { data: portfolio } = useSWR<Portfolio>('/api/portfolio', fetcher, { refreshInterval: 15000 })
  const { data: live } = useSWR<LiveTrade[]>('/api/trades/live', fetcher, { refreshInterval: 15000 })
  const { data: log } = useSWR<TradeLog[]>('/api/trades/log', fetcher, { refreshInterval: 15000 })
  const { data: equity } = useSWR<EquitySnap[]>('/api/equity', fetcher, { refreshInterval: 15000 })
  const { data: analytics } = useSWR<Analytics>('/api/analytics', fetcher, { refreshInterval: 60000 })

  const n = (v: string|number|undefined, d = 0) => Number(v ?? d)

  const chartData = {
    labels: (equity || []).map(e => {
      const d = new Date(e.snapshot_at)
      return `${d.getDate()} ${d.toLocaleString('default', { month: 'short' })} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
    }),
    datasets: [{
      label: 'Portfolio Value',
      data: (equity || []).map(e => n(e.capital)),
      fill: true,
      tension: 0.4,
      pointRadius: 0,
      borderWidth: 2,
      borderColor: 'rgb(34,211,238)',
      backgroundColor: (ctx: any) => {
        const chart = ctx.chart
        const { chartArea } = chart
        if (!chartArea) return 'transparent'
        const gradient = chart.ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom)
        gradient.addColorStop(0, 'rgba(34,211,238,0.3)')
        gradient.addColorStop(1, 'rgba(34,211,238,0.01)')
        return gradient
      }
    }]
  }

  const chartOptions: any = {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false, backgroundColor: 'rgba(15,23,42,0.9)', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1 } },
    scales: {
      x: { display: false },
      y: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#94a3b8', callback: (v: number) => `₹${v.toLocaleString()}` } }
    }
  }

  const pnl = n(portfolio?.pnl)
  const pnlPct = n(portfolio?.pnl_pct)
  const bt = analytics?.backtest

  // Sharpe verdict
  const sharpeVerdict = (s: number) => {
    if (s > 2) return { label: '🏆 Excellent', color: 'emerald' }
    if (s > 1) return { label: '✅ Good', color: 'cyan' }
    if (s > 0.5) return { label: '⚠️ Mediocre', color: 'yellow' }
    if (s > 0) return { label: '🟡 Weak', color: 'orange' }
    return { label: '❌ Negative', color: 'red' }
  }

  const tabs = [
    { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
    { id: 'live', label: 'Live Positions', icon: LineChart },
    { id: 'log', label: 'Trade Log', icon: Clock },
    { id: 'analytics', label: 'Analytics', icon: BarChart2 },
  ]

  return (
    <div className="min-h-screen bg-[#070d1a] text-white p-6" style={{ fontFamily: "'Space Grotesk', sans-serif" }}>
      <div className="max-w-6xl mx-auto space-y-6">

        {/* Header */}
        <motion.div initial={{ opacity: 0, y: -20 }} animate={{ opacity: 1, y: 0 }} className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="relative">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center shadow-lg shadow-cyan-500/25">
                <Activity className="w-5 h-5 text-white" />
              </div>
              <span className="absolute -top-1 -right-1 w-3 h-3 bg-emerald-400 rounded-full border-2 border-[#070d1a] animate-pulse" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight">QuantumTrader <span className="text-xs text-cyan-400 font-normal ml-1 bg-cyan-500/10 px-2 py-0.5 rounded-full border border-cyan-500/20">V2</span></h1>
              <p className="text-xs text-slate-500">NSE Algorithmic Trading System · Multi-Timeframe Confluence</p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-xs text-slate-500">Portfolio Value</p>
            <p className="text-2xl font-bold text-white">₹{n(portfolio?.capital).toLocaleString()}</p>
            <p className={`text-sm font-medium ${pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {pnl >= 0 ? '+' : ''}₹{pnl.toFixed(2)} ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
            </p>
          </div>
        </motion.div>

        {/* Tabs */}
        <div className="flex space-x-1 bg-white/5 p-1 rounded-xl w-fit border border-white/10 backdrop-blur-md">
          {tabs.map((t) => {
            const Icon = t.icon
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id as any)}
                className={`relative flex items-center gap-2 px-5 py-2.5 text-sm font-medium rounded-lg transition-colors ${
                  tab === t.id ? 'text-white' : 'text-slate-400 hover:text-slate-200 hover:bg-white/5'
                }`}
              >
                {tab === t.id && (
                  <motion.div
                    layoutId="activeTab"
                    className="absolute inset-0 bg-white/10 border border-white/20 rounded-lg shadow-sm"
                    transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                  />
                )}
                <Icon className="w-4 h-4 relative z-10" />
                <span className="relative z-10">{t.label}</span>
                {t.id === 'live' && live && live.length > 0 && (
                  <span className="relative z-10 ml-1 flex h-4 w-4 items-center justify-center rounded-full bg-cyan-500/20 text-[10px] text-cyan-400 font-bold border border-cyan-500/30">
                    {live.length}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        {/* Content */}
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98 }}
            transition={{ duration: 0.15 }}
          >

            {/* ── DASHBOARD TAB ── */}
            {tab === 'dashboard' && (
              <div className="space-y-6">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <Stat label="Capital" value={`₹${n(portfolio?.capital).toLocaleString()}`} />
                  <Stat label="Cash Available" value={`₹${n(portfolio?.cash).toLocaleString()}`} color="blue" />
                  <Stat label="Invested" value={`₹${n(portfolio?.invested).toLocaleString()}`} color="purple" />
                  <Stat label="Total P&L" value={`${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(2)}`} up={pnl >= 0} />
                </div>

                <div className="bg-white/5 border border-white/10 rounded-2xl p-6 backdrop-blur-sm">
                  <div className="flex items-center justify-between mb-4">
                    <h2 className="font-semibold text-slate-200">Equity Curve</h2>
                    <span className="text-xs text-slate-500">Live · refreshes every 15s</span>
                  </div>
                  <div className="h-64">
                    {equity && equity.length > 1
                      ? <Line data={chartData} options={chartOptions} />
                      : <div className="h-full flex items-center justify-center text-slate-500 text-sm">Accumulating data…</div>
                    }
                  </div>
                </div>
              </div>
            )}

            {/* ── LIVE POSITIONS TAB ── */}
            {tab === 'live' && (
              <div className="space-y-4">
                <h2 className="font-semibold text-slate-200">Live Positions</h2>
                {!live || live.length === 0 ? (
                  <div className="bg-white/5 border border-white/10 rounded-2xl p-12 text-center text-slate-500">
                    <Activity className="w-8 h-8 mx-auto mb-3 opacity-30" />
                    <p>No open positions</p>
                    <p className="text-xs mt-1">The engine is scanning for confluence signals</p>
                  </div>
                ) : live.map((pos, i) => {
                  const entry = n(pos.entry_price)
                  const sl = n(pos.stop_loss)
                  const tgt = n(pos.target)
                  const slDist = entry - sl
                  const tgtDist = tgt - entry
                  const rr = slDist > 0 ? tgtDist / slDist : 0
                  return (
                    <motion.div key={i} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.05 }}
                      className="bg-white/5 border border-white/10 rounded-2xl p-5 backdrop-blur-sm">
                      <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center gap-3">
                          <span className="text-lg font-bold">{pos.stock.replace('.NS', '')}</span>
                          <span className="text-xs bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded-full">LONG</span>
                        </div>
                        <span className="text-xs text-slate-400">RR: {rr.toFixed(1)}x</span>
                      </div>
                      <div className="grid grid-cols-3 gap-4 text-sm">
                        <div><p className="text-xs text-slate-500">Entry</p><p className="font-semibold">₹{entry.toFixed(2)}</p></div>
                        <div><p className="text-xs text-red-400">Stop Loss</p><p className="font-semibold text-red-400">₹{sl.toFixed(2)}</p></div>
                        <div><p className="text-xs text-emerald-400">Target</p><p className="font-semibold text-emerald-400">₹{tgt.toFixed(2)}</p></div>
                      </div>
                      {/* SL-to-Target progress bar */}
                      <div className="mt-3">
                        <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden">
                          <div className="h-full rounded-full bg-gradient-to-r from-red-500 via-yellow-400 to-emerald-500" style={{ width: '50%' }} />
                        </div>
                        <div className="flex justify-between text-[10px] text-slate-600 mt-1">
                          <span>SL ₹{sl.toFixed(0)}</span><span>Entry ₹{entry.toFixed(0)}</span><span>TP ₹{tgt.toFixed(0)}</span>
                        </div>
                      </div>
                      <p className="text-xs text-slate-500 mt-3 border-t border-white/5 pt-3">{pos.reason}</p>
                    </motion.div>
                  )
                })}
              </div>
            )}

            {/* ── TRADE LOG TAB ── */}
            {tab === 'log' && (
              <div className="space-y-4">
                <h2 className="font-semibold text-slate-200">Recent Execution History</h2>
                <div className="space-y-3">
                  {(log || []).map((t, i) => (
                    <motion.div key={t.id} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.03 }}
                      className="bg-white/5 border border-white/10 rounded-xl p-4 backdrop-blur-sm">
                      <div className="flex items-center justify-between mb-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-semibold">{t.stock.replace('.NS','')}</span>
                          <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${
                            t.action === 'BUY' ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/20' :
                            t.action === 'SELL' ? 'bg-red-500/15 text-red-400 border-red-500/20' :
                            'bg-slate-500/15 text-slate-400 border-slate-500/20'
                          }`}>{t.action}</span>
                          {t.regime && (
                            <span className={`text-xs px-2 py-0.5 rounded-full border ${
                              t.regime === 'TRENDING' ? 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20' :
                              t.regime === 'VOLATILE' ? 'bg-orange-500/10 text-orange-400 border-orange-500/20' :
                              'bg-purple-500/10 text-purple-400 border-purple-500/20'
                            }`}>{t.regime}</span>
                          )}
                          {t.confluence_score !== null && t.confluence_score !== undefined && (
                            <span className="text-xs text-slate-500">Confluence: {t.confluence_score > 0 ? '+' : ''}{t.confluence_score}</span>
                          )}
                          {t.pnl && Number(t.pnl) !== 0 && (
                            <span className={`text-sm font-bold ${Number(t.pnl) > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {Number(t.pnl) > 0 ? '+' : ''}₹{Number(t.pnl).toFixed(2)}
                            </span>
                          )}
                        </div>
                        <span className="text-xs text-slate-500 whitespace-nowrap">
                          {new Date(t.entry_time).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
                        </span>
                      </div>
                      <p className="text-xs text-slate-400 mt-1 border-l border-white/5 pl-3 leading-relaxed">{t.reason}</p>
                    </motion.div>
                  ))}
                </div>
              </div>
            )}

            {/* ── ANALYTICS TAB ── */}
            {tab === 'analytics' && (
              <div className="space-y-6">

                {/* Live Performance */}
                <div>
                  <h2 className="font-semibold text-slate-200 mb-3 flex items-center gap-2"><Zap className="w-4 h-4 text-cyan-400" /> Live Performance</h2>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <Stat label="Total Trades" value={String(analytics?.totalTrades ?? '—')} />
                    <Stat label="Win Rate" value={analytics ? `${analytics.winRate.toFixed(1)}%` : '—'} up={analytics ? analytics.winRate >= 50 : undefined} />
                    <Stat label="Profit Factor" value={analytics ? analytics.profitFactor.toFixed(2) : '—'} up={analytics ? analytics.profitFactor >= 1.5 : undefined} />
                    <Stat label="Expectancy" value={analytics ? `₹${analytics.expectancy.toFixed(0)}` : '—'} up={analytics ? analytics.expectancy > 0 : undefined} />
                  </div>
                  {analytics && (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
                      <MetricCard label="Avg Win" value={`₹${analytics.avgWin.toFixed(0)}`} icon={TrendingUp} good />
                      <MetricCard label="Avg Loss" value={`₹${analytics.avgLoss.toFixed(0)}`} icon={TrendingDown} bad />
                      <MetricCard label="Avg RR" value={`${analytics.avgRR.toFixed(2)}x`} icon={Target} good={analytics.avgRR >= 1.5} />
                      <MetricCard label="Net P&L" value={`₹${analytics.netPnl.toFixed(0)}`} icon={Activity} good={analytics.netPnl > 0} bad={analytics.netPnl < 0} />
                    </div>
                  )}
                </div>

                {/* Backtest Results */}
                <div>
                  <h2 className="font-semibold text-slate-200 mb-3 flex items-center gap-2"><BarChart2 className="w-4 h-4 text-cyan-400" /> Backtest Results (Historical Simulation)</h2>
                  {!bt ? (
                    <div className="bg-amber-500/10 border border-amber-500/20 rounded-2xl p-8 text-center">
                      <AlertTriangle className="w-8 h-8 text-amber-400 mx-auto mb-3" />
                      <p className="text-amber-300 font-semibold">No backtest data yet</p>
                      <p className="text-sm text-slate-400 mt-2">Run the backtest engine to see historical performance</p>
                      <code className="block mt-3 text-xs text-slate-500 bg-white/5 rounded-lg p-3">python engine/backtest.py --save</code>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {/* Verdict banner */}
                      {(() => {
                        const v = sharpeVerdict(Number(bt.sharpe_ratio))
                        return (
                          <div className={`rounded-2xl p-4 border text-center bg-${v.color}-500/10 border-${v.color}-500/20`}>
                            <p className="text-lg font-bold">{v.label}</p>
                            <p className="text-xs text-slate-400 mt-1">{bt.period_start} → {bt.period_end} · {bt.total_trades} trades simulated</p>
                          </div>
                        )
                      })()}

                      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                        <MetricCard label="Sharpe Ratio" value={Number(bt.sharpe_ratio).toFixed(3)} icon={TrendingUp} good={Number(bt.sharpe_ratio) > 1} bad={Number(bt.sharpe_ratio) <= 0} />
                        <MetricCard label="Sortino Ratio" value={Number(bt.sortino_ratio).toFixed(3)} icon={TrendingUp} good={Number(bt.sortino_ratio) > 1} bad={Number(bt.sortino_ratio) <= 0} />
                        <MetricCard label="Max Drawdown" value={`${Number(bt.max_drawdown_pct).toFixed(1)}%`} icon={TrendingDown} bad={Number(bt.max_drawdown_pct) > 20} good={Number(bt.max_drawdown_pct) < 10} />
                        <MetricCard label="Win Rate" value={`${Number(bt.win_rate).toFixed(1)}%`} icon={Target} good={Number(bt.win_rate) >= 50} />
                        <MetricCard label="Profit Factor" value={Number(bt.profit_factor).toFixed(2)} icon={BarChart2} good={Number(bt.profit_factor) >= 1.5} bad={Number(bt.profit_factor) < 1} />
                        <MetricCard label="Sim Net P&L" value={`₹${Number(bt.total_pnl).toLocaleString()}`} icon={Activity} good={Number(bt.total_pnl) > 0} bad={Number(bt.total_pnl) < 0} />
                      </div>

                      <div className="bg-white/5 border border-white/10 rounded-xl p-4 text-xs text-slate-400">
                        <p className="font-semibold text-slate-300 mb-1">⚠️ Important Disclaimer</p>
                        <p>Past performance does not guarantee future results. Backtest results assume perfect execution with no slippage, brokerage fees, or market impact. Always paper-trade for 3+ months before risking real capital.</p>
                      </div>
                    </div>
                  )}
                </div>

                {/* Signal & Regime distribution */}
                {analytics && analytics.signalDistribution.length > 0 && (
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="bg-white/5 border border-white/10 rounded-2xl p-5">
                      <h3 className="text-sm font-semibold text-slate-300 mb-3">Signal Distribution</h3>
                      <div className="space-y-2">
                        {analytics.signalDistribution.map(s => {
                          const total = analytics.signalDistribution.reduce((a, b) => a + Number(b.count), 0)
                          const pct = total > 0 ? (Number(s.count) / total * 100) : 0
                          return (
                            <div key={s.action}>
                              <div className="flex justify-between text-xs mb-1">
                                <span className={s.action === 'BUY' ? 'text-emerald-400' : s.action === 'SELL' ? 'text-red-400' : 'text-slate-400'}>{s.action}</span>
                                <span className="text-slate-500">{s.count} ({pct.toFixed(0)}%)</span>
                              </div>
                              <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden">
                                <div className={`h-full rounded-full ${s.action === 'BUY' ? 'bg-emerald-500' : s.action === 'SELL' ? 'bg-red-500' : 'bg-slate-600'}`} style={{ width: `${pct}%` }} />
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                    <div className="bg-white/5 border border-white/10 rounded-2xl p-5">
                      <h3 className="text-sm font-semibold text-slate-300 mb-3">Regime Distribution</h3>
                      <div className="space-y-2">
                        {analytics.regimeDistribution.map(r => {
                          const total = analytics.regimeDistribution.reduce((a, b) => a + Number(b.count), 0)
                          const pct = total > 0 ? (Number(r.count) / total * 100) : 0
                          return (
                            <div key={r.regime}>
                              <div className="flex justify-between text-xs mb-1">
                                <span className={r.regime === 'TRENDING' ? 'text-cyan-400' : r.regime === 'VOLATILE' ? 'text-orange-400' : 'text-purple-400'}>{r.regime}</span>
                                <span className="text-slate-500">{r.count} ({pct.toFixed(0)}%)</span>
                              </div>
                              <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden">
                                <div className={`h-full rounded-full ${r.regime === 'TRENDING' ? 'bg-cyan-500' : r.regime === 'VOLATILE' ? 'bg-orange-500' : 'bg-purple-500'}`} style={{ width: `${pct}%` }} />
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}

          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}
