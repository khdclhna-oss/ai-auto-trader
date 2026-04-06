TRUNCATE portfolio, trades, open_positions, equity_snapshots RESTART IDENTITY CASCADE;

INSERT INTO portfolio (capital, invested, cash) VALUES (100000.00, 8420.50, 91579.50);

INSERT INTO trades (stock, action, entry_price, exit_price, quantity, pnl, reason, entry_time, exit_time, status) VALUES
('RELIANCE.NS','BUY',2450.00,2523.50,3,220.50,'RSI oversold (28.3) +2 + MACD bullish crossover +2 + EMA20 > EMA50 +1 → score +7 → BUY','2026-03-28 09:30:00','2026-03-29 14:45:00','CLOSED'),
('TCS.NS','BUY',3680.00,3710.00,2,60.00,'RSI oversold (31.1) +2 + EMA20 > EMA50 +1 + Positive news +1 → score +4 → BUY','2026-03-30 10:00:00','2026-03-30 15:00:00','CLOSED'),
('INFY.NS','SELL',1520.00,1498.00,5,110.00,'RSI overbought (72.4) -2 + MACD bearish crossover -2 → score -4 → SELL','2026-03-31 09:45:00','2026-03-31 13:30:00','CLOSED'),
('HDFCBANK.NS','BUY',1680.00,1650.00,4,-120.00,'RSI oversold (29.8) +2 + Volume spike +1 → score +4 → BUY (SL hit)','2026-04-01 10:15:00','2026-04-01 11:00:00','CLOSED'),
('ICICIBANK.NS','BUY',1125.00,1162.50,6,225.00,'MACD bullish crossover +2 + EMA20 > EMA50 +1 + Volume spike +1 → score +4 → BUY','2026-04-02 09:30:00','2026-04-03 14:00:00','CLOSED'),
('RELIANCE.NS','BUY',2510.00,NULL,3,NULL,'RSI oversold (26.9) +2 + MACD bullish crossover +2 + EMA20 > EMA50 +1 + Positive news +1 → score +6 → BUY','2026-04-04 09:45:00',NULL,'OPEN'),
('TCS.NS','BUY',3720.00,NULL,2,NULL,'MACD bullish crossover +2 + Volume spike +1 + Positive news +1 → score +4 → BUY','2026-04-04 10:30:00',NULL,'OPEN');

INSERT INTO open_positions (stock, quantity, entry_price, stop_loss, target, entry_time, reason) VALUES
('RELIANCE.NS',3,2510.00,2472.35,2585.30,'2026-04-04 09:45:00','RSI oversold +2 + MACD bullish crossover +2 + EMA20 > EMA50 +1 + Positive news +1 → score +6 → BUY'),
('TCS.NS',2,3720.00,3664.20,3831.60,'2026-04-04 10:30:00','MACD bullish crossover +2 + Volume spike +1 + Positive news +1 → score +4 → BUY');

INSERT INTO equity_snapshots (capital, cash, invested, snapshot_at) VALUES
(100000.00,100000.00,0.00,'2026-03-28 09:00:00'),
(100000.00,92670.00,7330.00,'2026-03-28 10:00:00'),
(100220.50,99890.50,330.00,'2026-03-29 15:00:00'),
(100280.50,92642.50,7638.00,'2026-03-30 10:30:00'),
(100340.50,100340.50,0.00,'2026-03-30 15:30:00'),
(100450.50,93770.50,6680.00,'2026-03-31 10:00:00'),
(100560.50,99890.50,670.00,'2026-04-01 11:30:00'),
(108420.50,91579.50,8841.00,'2026-04-04 11:00:00');
