import type { Metadata } from 'next'
import { Inter, Space_Grotesk } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })
const spaceGrotesk = Space_Grotesk({ subsets: ['latin'], variable: '--font-space' })

export const metadata: Metadata = {
  title: 'AI Auto-Trader | Live Terminal',
  description: 'Automated NSE stock trading with technical analysis',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${inter.variable} ${spaceGrotesk.variable} font-sans bg-[#0B0E14] text-white antialiased selection:bg-cyan-500/30 selection:text-cyan-100`}>
        {children}
      </body>
    </html>
  )
}
