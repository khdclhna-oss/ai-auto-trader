import { NextResponse } from 'next/server';
import { pool } from '@/lib/db';

export async function GET() {
  try {
    const res = await pool.query(`
      SELECT finished_at 
      FROM run_logs 
      WHERE status IN ('SUCCESS', 'MARKET_CLOSED') 
      ORDER BY finished_at DESC 
      LIMIT 1
    `);

    if (res.rows.length === 0) {
      return NextResponse.json({ status: 'no_runs_found' }, { status: 500 });
    }

    const lastRun = new Date(res.rows[0].finished_at);
    const diffMinutes = (new Date().getTime() - lastRun.getTime()) / (1000 * 60);

    const now = new Date();
    const hours = now.getUTCHours();
    const isMarketHours = (hours >= 3 && hours <= 11);

    if (isMarketHours && diffMinutes > 25) {
      return NextResponse.json({ 
        status: 'stale', 
        last_run: lastRun, 
        minutes_ago: diffMinutes 
      }, { status: 500 });
    }

    return NextResponse.json({ 
      status: 'healthy', 
      last_run: lastRun 
    }, { status: 200 });

  } catch (error: any) {
    return NextResponse.json({ status: 'error', error: error.message }, { status: 500 });
  }
}
