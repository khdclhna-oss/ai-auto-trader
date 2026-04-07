import { NextResponse } from 'next/server'

// ─── NSE Market Hours Check (IST = UTC+5:30) ──────────────────────────────
function isNSEMarketOpen(): { open: boolean; reason: string } {
  const nowUTC = new Date()
  // Convert to IST (UTC+5:30)
  const istOffset = 5 * 60 + 30 // minutes
  const nowIST = new Date(nowUTC.getTime() + istOffset * 60 * 1000)

  const day = nowIST.getUTCDay()    // 0=Sun, 1=Mon ... 6=Sat
  const hours = nowIST.getUTCHours()
  const mins = nowIST.getUTCMinutes()
  const totalMins = hours * 60 + mins

  const MARKET_OPEN  = 9 * 60 + 15   // 555 minutes = 9:15 AM IST
  const MARKET_CLOSE = 15 * 60 + 30  // 930 minutes = 3:30 PM IST

  const dayNames = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
  const timeStr  = `${String(hours).padStart(2,'0')}:${String(mins).padStart(2,'0')} IST`

  if (day === 0 || day === 6) {
    return { open: false, reason: `Weekend (${dayNames[day]}) — NSE is closed.` }
  }
  if (totalMins < MARKET_OPEN) {
    return { open: false, reason: `Pre-market: ${timeStr} — NSE opens at 09:15 IST.` }
  }
  if (totalMins > MARKET_CLOSE) {
    return { open: false, reason: `Post-market: ${timeStr} — NSE closed at 15:30 IST.` }
  }
  return { open: true, reason: `Market open: ${timeStr}` }
}

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const secret = searchParams.get('key')

  // ── Auth check ────────────────────────────────────────────────────────────
  const correctSecret = process.env.CRON_SECRET
  if (!correctSecret || secret !== correctSecret) {
    return NextResponse.json({ error: 'Unauthorized: Invalid or missing key' }, { status: 401 })
  }

  // ── Market hours gate ─────────────────────────────────────────────────────
  const market = isNSEMarketOpen()
  if (!market.open) {
    return NextResponse.json({
      skipped: true,
      reason: market.reason,
      message: 'No GitHub Action triggered — NSE market is closed.'
    }, { status: 200 })
  }

  // ── Trigger GitHub Actions ────────────────────────────────────────────────
  const githubToken = process.env.GITHUB_PAT
  if (!githubToken) {
    return NextResponse.json({ error: 'Configuration Error: GITHUB_PAT not set' }, { status: 500 })
  }

  try {
    const res = await fetch(
      'https://api.github.com/repos/khdclhna-oss/ai-auto-trader/actions/workflows/trader.yml/dispatches',
      {
        method: 'POST',
        headers: {
          'Accept': 'application/vnd.github+json',
          'Authorization': `Bearer ${githubToken}`,
          'X-GitHub-Api-Version': '2022-11-28',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ ref: 'main', inputs: { run_backtest: 'false' } }),
      }
    )

    if (!res.ok) {
      const errText = await res.text()
      return NextResponse.json({ error: 'Failed to trigger GitHub Action', details: errText }, { status: res.status })
    }

    return NextResponse.json({
      success: true,
      marketStatus: market.reason,
      message: 'QuantumTrader engine triggered via GitHub Actions.',
    })
  } catch (error: any) {
    return NextResponse.json({ error: 'Internal server error', details: error.message }, { status: 500 })
  }
}
