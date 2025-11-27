from __future__ import annotations

import argparse
from agentfx.shared.config import (
    load_config,
    backtest_default_spread_pips,
    backtest_default_slippage_pips,
    policy_default_max_abs_position,
    policy_default_max_trades_per_hour,
    policy_default_daily_loss_threshold,
)
from agentfx.services.ingest import backfill_all
from agentfx.services.backtest import backtest_pairs


def main():
    parser = argparse.ArgumentParser(prog="agentfx", description="AgentFX CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    bf = sub.add_parser("backfill", help="Backfill 1m OHLCV for configured pairs")
    bf.add_argument("--pairs", type=str, default=None, help="Comma-separated pairs override (e.g., EURUSD,GBPUSD)")
    bf.add_argument("--start-days", type=int, default=None, help="Lookback window in days")
    bf.add_argument("--force", action="store_true", help="Force backfill from start window, ignore existing data")

    bt = sub.add_parser("backtest", help="Run backtest over pairs using local data")
    bt.add_argument("--pairs", type=str, default=None, help="Comma-separated pairs override (e.g., EURUSD,GBPUSD)")
    bt.add_argument("--start-days", type=int, default=None, help="Limit data to last N days")
    bt.add_argument("--strategy", type=str, default="ma", help="Strategy name (e.g., ma)")
    bt.add_argument("--fast", type=int, default=20, help="Fast parameter for strategy (if applicable)")
    bt.add_argument("--slow", type=int, default=50, help="Slow parameter for strategy (if applicable)")
    bt.add_argument("--spread-pips", type=float, default=None, help="Assumed spread in pips")
    bt.add_argument("--slippage-pips", type=float, default=None, help="Assumed slippage per side in pips")
    bt.add_argument("--max-abs-position", type=int, default=None, help="Maximum absolute position magnitude")
    bt.add_argument("--max-trades-per-hour", type=int, default=None, help="Max position changes in rolling hour")
    bt.add_argument("--daily-loss-threshold", type=float, default=None, help="Daily loss threshold to halt (e.g., 0.02)")
    bt.add_argument("--persist", action="store_true", help="Persist backtest results to SQLite")

    args = parser.parse_args()

    if args.command == "backfill":
        cfg = load_config()
        if args.pairs:
            cfg.pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
        if args.start_days is not None:
            cfg.start_days = args.start_days
        if args.force:
            cfg.force_start = True
        backfill_all(cfg)
    elif args.command == "backtest":
        pairs = [p.strip() for p in args.pairs.split(",") if p.strip()] if args.pairs else []
        if not pairs:
            cfg = load_config()
            pairs = cfg.pairs
        cfg = load_config()
        # Fill defaults from env when flags omitted
        spread = args.spread_pips if args.spread_pips is not None else backtest_default_spread_pips()
        slip = args.slippage_pips if args.slippage_pips is not None else backtest_default_slippage_pips()
        max_abs = args.max_abs_position if args.max_abs_position is not None else policy_default_max_abs_position()
        max_rate = args.max_trades_per_hour if args.max_trades_per_hour is not None else policy_default_max_trades_per_hour()
        loss_thr = args.daily_loss_threshold if args.daily_loss_threshold is not None else policy_default_daily_loss_threshold()

        res = backtest_pairs(
            data_root=cfg.data_root,
            pairs=pairs,
            start_days=args.start_days,
            strategy_name=args.strategy,
            strategy_params={"fast": args.fast, "slow": args.slow},
            spread_pips=spread,
            slippage_pips_per_side=slip,
            max_abs_position=max_abs,
            max_trades_per_hour=max_rate,
            daily_loss_threshold=loss_thr,
            persist=args.persist,
            db_path=cfg.db_path,
        )
        # Print simple text output
        import json
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
