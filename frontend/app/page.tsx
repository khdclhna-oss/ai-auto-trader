'use client'
import { useState, useRef, useEffect } from 'react'
import useSWR from 'swr'
import { Line } from 'react-chartjs-2'
import {
  Chart as ChartJS, CategoryScale, LinearScale, PointElement,
  LineElement, Title, Tooltip, Legend, Filler
} from 'chart.js'
import { motion, AnimatePresence } from 'framer-motion'
import { Activity, Briefcase, Clock, LayoutDashboard, LineChart, MoveUpRight, ArrowDownRight, CircleDollarSign } from 'lucide-react'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Title, Tooltip, Legend)

type Portfolio = { capital: string|number; invested: string|number; cash: string|number; pnl: string|number; pnl_pct: string|number }
type LiveTrade = { stock: string; entry_price: string|number; quantity: string|number; stop_loss: string|number; target: string|number; entry_time: string; reason: string }
type TradeLog = { id: number; stock: string; action: string; entry_price: string|number; exit_price: string|number; pnl: string|number; reason: string; entry_time: string; status: string }
type EquitySnap = { capital: string|number; snapshot_at: string }

const fetcher = (url: string) => fetch(url).then(res => res.json())

export default function Dashboard() {
  const [tab, setTab] = useState<'dashboard'|'live'|'log'>('dashboard')
  const chartRef = useRef<any>(null)
  
  // Real-time Auto-Polling (every 15 seconds)
  const { data: portfolio } = useSWR<Portfolio>('/api/portfolio', fetcher, { refreshInterval: 15000 })
  const { data: live } = useSWR<LiveTrade[]>('/api/trades/live', fetcher, { refreshInterval: 15000 })
  const { data: log } = useSWR<TradeLog[]>('/api/trades/log', fetcher, { refreshInterval: 15000 })
  const { data: equity } = useSWR<EquitySnap[]>('/api/equity', fetcher, { refreshInterval: 15000 })

  const chartData = equity && equity.length > 0 ? {
    labels: equity.map(e => new Date(e.snapshot_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })),
    datasets: [{
      label: 'Equity (₹)',
      data: equity.map(e => Number(e.capital)),
      borderColor: '#06b6d4',
      backgroundColor: (context: any) => {
        const chart = context.chart
        if (!chart) return '#06b6d4'
        const ctx = chart.ctx
        const gradient = ctx.createLinearGradient(0, 0, 0, 400)
        gradient.addColorStop(0, 'rgba(6, 182, 212, 0.4)')
        gradient.addColorStop(1, 'rgba(6, 182, 212, 0.0)')
        return gradient
      },
      fill: true,
      tension: 0.4,
      pointRadius: 0,
      pointHoverRadius: 6,
      pointBackgroundColor: '#06b6d4',
      borderWidth: 2,
    }]
  } : null

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index' as const, intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: 'rgba(15, 23, 42, 0.9)',
        titleColor: '#94a3b8',
        bodyColor: '#f8fafc',
        bodyFont: { family: 'var(--font-space)' },
        borderColor: 'rgba(30, 41, 59, 1)',
        borderWidth: 1,
        padding: 12,
        displayColors: false,
        callbacks: {
          label: function(context: any) {
            let label = context.dataset.label || ''
            if (label) label += ': '
            if (context.parsed.y !== null) {
              label += '₹' + context.parsed.y.toLocaleString('en-IN', {maximumFractionDigits:0})
            }
            return label
          }
        }
      }
    },
    scales: {
      y: { grid: { color: 'rgba(51, 65, 85, 0.3)', drawBorder: false }, ticks: { color: '#64748b', font: { family: 'var(--font-space)' } } },
      x: { grid: { display: false, drawBorder: false }, ticks: { color: '#64748b', font: { family: 'var(--font-space)' }, maxTicksLimit: 8 } }
    }
  }

  return (
    <div className="min-h-screen bg-[#0B0E14] text-slate-200 font-sans p-4 md:p-8">
      <div className="max-w-6xl mx-auto space-y-8">
        
        {/* Header */}
        <header className="flex flex-col md:flex-row md:items-end justify-between border-b border-white/5 pb-6">
          <div>
            <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-white mb-2 flex items-center gap-3 font-[family-name:var(--font-space)]">
              <div className="p-2 bg-gradient-to-br from-cyan-500 to-blue-600 rounded-lg shadow-[0_0_20px_rgba(6,182,212,0.3)]">
                <Activity className="w-6 h-6 text-white" />
              </div>
              QuantumTrader
            </h1>
            <p className="text-slate-400 text-sm flex items-center gap-2">
              <span className="flex h-2 w-2 relative">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan-500"></span>
              </span>
              Live Market Link • NSE Equities • Algotrading Active
            </p>
          </div>
          <div className="mt-4 md:mt-0 text-sm font-medium text-slate-500 bg-white/5 px-4 py-2 rounded-full border border-white/10 backdrop-blur-md">
            Virtual Portfolio: ₹1,00,000
          </div>
        </header>

        {/* Navigation Tabs */}
        <div className="flex space-x-1 bg-white/5 p-1 rounded-xl w-fit border border-white/10 backdrop-blur-md">
          {[
            { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
            { id: 'live', label: 'Live Positions', icon: LineChart },
            { id: 'log', label: 'Trade Log', icon: Clock }
          ].map((t) => {
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

        {/* Content Area */}
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98 }}
            transition={{ duration: 0.2 }}
          >
            {/* Dashboard View */}
            {tab === 'dashboard' && (
              <div className="space-y-6">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {[
                    { label: 'Equity Value', value: portfolio ? Number(portfolio.capital) : null, prefix: '₹', icon: CircleDollarSign },
                    { label: 'Available Margin', value: portfolio ? Number(portfolio.cash) : null, prefix: '₹', icon: Briefcase },
                    { label: 'Total Invested', value: portfolio ? Number(portfolio.invested) : null, prefix: '₹', icon: LayoutDashboard },
                    { 
                      label: 'Net P&L', 
                      value: portfolio ? Number(portfolio.pnl) : null,
                      pct: portfolio ? Number(portfolio.pnl_pct) : null,
                      prefix: portfolio && Number(portfolio.pnl) >= 0 ? '+₹' : '-₹', 
                      icon: portfolio && Number(portfolio.pnl) >= 0 ? MoveUpRight : ArrowDownRight,
                      isPnl: true
                    },
                  ].map((metric, i) => (
                    <motion.div 
                      initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.1 }}
                      key={metric.label} 
                      className="relative overflow-hidden bg-gradient-to-b from-white/5 to-transparent rounded-2xl p-5 border border-white/10 backdrop-blur-md group hover:border-cyan-500/30 transition-colors"
                    >
                      <div className="flex items-center gap-3 mb-2">
                        <metric.icon className={`w-4 h-4 ${metric.isPnl ? (metric.value !== null && metric.value >= 0 ? 'text-green-400' : 'text-rose-400') : 'text-slate-400'}`} />
                        <span className="text-slate-400 text-sm font-medium">{metric.label}</span>
                      </div>
                      <div className="mt-1">
                        {metric.value !== null ? (
                          <div className={`text-2xl font-bold tracking-tight font-[family-name:var(--font-space)] ${metric.isPnl ? (metric.value >= 0 ? 'text-green-400 drop-shadow-[0_0_10px_rgba(74,222,128,0.2)]' : 'text-rose-400 drop-shadow-[0_0_10px_rgba(251,113,133,0.2)]') : 'text-white'}`}>
                            {metric.prefix}{Math.abs(metric.value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                            {metric.isPnl && metric.pct !== null && (
                              <span className="text-sm font-medium ml-2 opacity-80">
                                ({metric.pct > 0 ? '+' : ''}{metric.pct.toFixed(2)}%)
                              </span>
                            )}
                          </div>
                        ) : (
                          <div className="h-8 w-24 bg-white/5 rounded animate-pulse" />
                        )}
                      </div>
                      <div className="absolute -bottom-6 -right-6 w-24 h-24 bg-cyan-500/5 rounded-full blur-2xl group-hover:bg-cyan-500/10 transition-colors" />
                    </motion.div>
                  ))}
                </div>

                <div className="bg-gradient-to-b from-white/5 to-transparent rounded-2xl p-6 border border-white/10 backdrop-blur-md">
                  <div className="flex justify-between items-center mb-6">
                    <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                      <LayoutDashboard className="w-5 h-5 text-cyan-400" />
                      Performance Curve
                    </h3>
                  </div>
                  <div className="h-[350px] w-full relative">
                    {chartData ? (
                       <Line data={chartData} options={chartOptions} />
                    ) : (
                      <div className="absolute inset-0 flex items-center justify-center">
                         <div className="w-8 h-8 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin" />
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Live Positions View */}
            {tab === 'live' && (
              <div className="bg-gradient-to-b from-white/5 to-transparent rounded-2xl border border-white/10 backdrop-blur-md overflow-hidden">
                <div className="p-6 border-b border-white/5">
                   <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                      <LineChart className="w-5 h-5 text-cyan-400" />
                      Active Trades
                    </h3>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm text-left">
                    <thead className="text-xs uppercase bg-white/5 text-slate-400 font-[family-name:var(--font-space)]">
                      <tr>
                        {['Stock', 'Qty', 'Entry Price', 'Stop Loss', 'Target', 'Time', 'Signal Source'].map(h => (
                          <th key={h} className="px-6 py-4 font-semibold tracking-wider">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                      {!live ? (
                        <tr><td colSpan={7} className="px-6 py-8 text-center text-slate-500"><div className="w-6 h-6 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin mx-auto" /></td></tr>
                      ) : live.length === 0 ? (
                        <tr><td colSpan={7} className="px-6 py-12 text-center text-slate-500">Scanning markets for entries...</td></tr>
                      ) : live.map((t, i) => (
                        <motion.tr initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.05 }} key={i} className="hover:bg-white/[0.02] transition-colors">
                          <td className="px-6 py-4 font-bold text-white font-[family-name:var(--font-space)]">{t.stock.replace('.NS', '')}</td>
                          <td className="px-6 py-4 text-slate-300">{Number(t.quantity).toLocaleString()}</td>
                          <td className="px-6 py-4 font-medium">₹{Number(t.entry_price).toFixed(2)}</td>
                          <td className="px-6 py-4 text-rose-400 font-medium bg-rose-500/5">₹{Number(t.stop_loss).toFixed(2)}</td>
                          <td className="px-6 py-4 text-green-400 font-medium bg-green-500/5">₹{Number(t.target).toFixed(2)}</td>
                          <td className="px-6 py-4 text-slate-400">{new Date(t.entry_time).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit'})}</td>
                          <td className="px-6 py-4 text-xs text-slate-500 max-w-xs truncate" title={t.reason}>{t.reason}</td>
                        </motion.tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Trade Log View */}
            {tab === 'log' && (
              <div className="bg-gradient-to-b from-white/5 to-transparent rounded-2xl border border-white/10 backdrop-blur-md overflow-hidden p-6">
                <h3 className="text-lg font-semibold text-white flex items-center gap-2 mb-6">
                  <Clock className="w-5 h-5 text-cyan-400" />
                  Recent Execution History
                </h3>
                <div className="space-y-3">
                  {!log ? (
                    <div className="flex justify-center p-8"><div className="w-6 h-6 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin" /></div>
                  ) : log.length === 0 ? (
                    <p className="text-center text-slate-500 p-8">No trading history yet.</p>
                  ) : log.map((t, i) => (
                    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }} key={t.id} 
                      className={`relative overflow-hidden rounded-xl p-4 border transition-colors ${
                        t.action === 'BUY' ? 'border-cyan-900/50 bg-cyan-950/20 hover:border-cyan-700/50' : 
                        t.action === 'SELL' ? 'border-rose-900/50 bg-rose-950/20 hover:border-rose-700/50' : 
                        'border-white/10 bg-white/5 hover:border-white/20'
                      }`}
                    >
                       <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b" style={{
                         backgroundImage: t.action === 'BUY' ? 'linear-gradient(to bottom, #06b6d4, transparent)' : 
                                          t.action === 'SELL' ? 'linear-gradient(to bottom, #f43f5e, transparent)' : 'none'
                       }} />
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 mb-2 pl-3">
                        <div className="flex items-center gap-3">
                          <span className="font-bold text-lg font-[family-name:var(--font-space)] text-white">{t.stock.replace('.NS', '')}</span>
                          <span className={`text-[10px] font-bold px-2 py-1 rounded tracking-wider ${
                            t.action === 'BUY' ? 'bg-cyan-500/20 text-cyan-400 border border-cyan-500/30' : 
                            t.action === 'SELL' ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30' : 
                            'bg-slate-800 text-slate-400 border border-slate-700'
                          }`}>{t.action}</span>
                          <span className="text-xs text-slate-500 flex items-center gap-1">
                            <Clock className="w-3 h-3" />
                            {new Date(t.entry_time).toLocaleString('en-IN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                          </span>
                        </div>
                        {t.pnl !== null && (
                          <div className={`flex flex-col items-end`}>
                            <span className={`text-lg font-bold font-[family-name:var(--font-space)] drop-shadow-md ${Number(t.pnl) >= 0 ? 'text-green-400' : 'text-rose-400'}`}>
                              {Number(t.pnl) >= 0 ? '+' : ''}₹{Number(t.pnl).toFixed(2)}
                            </span>
                          </div>
                        )}
                      </div>
                      <p className="text-sm text-slate-400 pl-3 leading-relaxed border-l border-white/5">{t.reason}</p>
                    </motion.div>
                  ))}
                </div>
              </div>
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}
