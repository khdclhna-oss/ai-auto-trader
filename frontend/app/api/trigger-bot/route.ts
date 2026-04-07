import { NextResponse } from 'next/server'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const secret = searchParams.get('key')
  
  // Ensure the caller has the correct secret key
  const correctSecret = process.env.CRON_SECRET
  if (!correctSecret || secret !== correctSecret) {
    return NextResponse.json({ error: 'Unauthorized: Invalid or missing key' }, { status: 401 })
  }

  const githubToken = process.env.GITHUB_PAT
  if (!githubToken) {
    return NextResponse.json({ error: 'Configuration Error: GITHUB_PAT not set' }, { status: 500 })
  }

  try {
    // Tell GitHub to run the trader.yml workflow
    const res = await fetch(
      'https://api.github.com/repos/khdclhna-oss/ai-auto-trader/actions/workflows/trader.yml/dispatches',
      {
        method: 'POST',
        headers: {
          'Accept': 'application/vnd.github+json',
          'Authorization': `Bearer ${githubToken}`,
          'X-GitHub-Api-Version': '2022-11-28',
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          ref: 'main',
          inputs: { run_backtest: 'false' }
        })
      }
    )

    if (!res.ok) {
      const errText = await res.text()
      return NextResponse.json({ error: 'Failed to trigger GitHub Action', details: errText }, { status: res.status })
    }

    return NextResponse.json({ success: true, message: 'Successfully triggered QuantumTrader engine via GitHub Actions.' })
  } catch (error: any) {
    return NextResponse.json({ error: 'Internal server error', details: error.message }, { status: 500 })
  }
}
