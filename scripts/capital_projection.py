#!/usr/bin/env python3
"""
Monte Carlo simulation for capital projection over 1 month trading.
Compares 3 stop-loss strategies with realistic market scenarios.
"""

import numpy as np
from dataclasses import dataclass
from typing import List, Tuple
from datetime import datetime, timedelta
import json


@dataclass
class SimulationConfig:
    """Configuration parameters for the trading simulation."""

    initial_capital: float = 4.49  # Current balance
    trade_size_usd: float = 1.10  # Size per trade
    stop_loss_pct: float = 0.30  # 30% stop loss
    stop_loss_absolute: float = 0.80  # $0.80 absolute floor
    take_profit_pct: float = 0.10  # 10% take profit
    trailing_stop_pct: float = 0.05  # 5% trailing stop
    min_confidence: float = 0.85  # 85% minimum confidence
    daily_loss_limit_pct: float = 0.10  # 10% daily loss limit
    max_trades_per_day: int = 100
    max_capital_per_trade: float = 0.05  # 5% of capital
    trading_days: int = 30  # 1 month (approximately 30 trading days)


@dataclass
class TradeResult:
    """Result of a single trade."""

    entry_price: float
    exit_price: float
    profit_pct: float
    profit_usd: float
    reason: str  # "TAKE-PROFIT", "STOP-LOSS", "TRAILING-STOP", "MARKET-CLOSE"
    entry_capital: float
    exit_capital: float


class StopLossStrategy:
    """Different stop-loss strategies to compare."""

    @staticmethod
    def strategy_1_pct_only(
        entry_price: float, current_price: float, stop_loss_pct: float
    ) -> Tuple[bool, float]:
        """
        Strategy 1: Only 30% stop-loss percentage.
        Stop price = entry_price * (1 - 0.30)
        """
        stop_price = entry_price * (1 - stop_loss_pct)
        triggered = current_price < stop_price
        return triggered, stop_price

    @staticmethod
    def strategy_2_absolute_only(
        entry_price: float, current_price: float, absolute_floor: float
    ) -> Tuple[bool, float]:
        """
        Strategy 2: Only $0.80 absolute floor.
        Stop price = $0.80 (regardless of entry)
        """
        stop_price = absolute_floor
        triggered = current_price < stop_price
        return triggered, stop_price

    @staticmethod
    def strategy_3_both(
        entry_price: float,
        current_price: float,
        stop_loss_pct: float,
        absolute_floor: float,
    ) -> Tuple[bool, float]:
        """
        Strategy 3: Both 30% AND $0.80 absolute floor.
        Stop price = MAX(entry_price * 0.70, $0.80)
        """
        stop_price = max(entry_price * (1 - stop_loss_pct), absolute_floor)
        triggered = current_price < stop_price
        return triggered, stop_price


class MarketSimulator:
    """Simulates market price movements for binary options."""

    def __init__(self, base_win_rate: float = 0.65, volatility: float = 0.15):
        """
        Initialize market simulator.

        Args:
            base_win_rate: Base probability of price moving in our favor (0.65 = 65%)
            volatility: Market volatility (standard deviation of price changes)
        """
        self.base_win_rate = base_win_rate
        self.volatility = volatility

    def generate_market_path(self, entry_price: float, steps: int = 100) -> List[float]:
        """
        Generate a realistic price path for a binary option.

        Binary options tend to:
        - Start near entry price
        - Trend toward 0 or 1 as time progresses
        - Have mean-reverting tendencies
        """
        prices = [entry_price]
        price = entry_price

        # Determine if this trade will be a winner (based on confidence)
        is_winner = np.random.random() < self.base_win_rate

        for i in range(1, steps):
            # Trend component: drift toward 1 if winner, 0 if loser
            time_factor = i / steps  # 0 to 1
            trend_target = 1.0 if is_winner else 0.0

            # Drift increases as we approach expiration
            drift_strength = 0.02 * time_factor
            trend_drift = (trend_target - price) * drift_strength

            # Random noise (decreases as we approach expiration)
            noise = np.random.normal(0, self.volatility * (1 - time_factor * 0.5))

            price = price + trend_drift + noise
            price = max(0.01, min(0.99, price))  # Clamp to [0.01, 0.99]
            prices.append(price)

        return prices

    def simulate_trade(
        self,
        entry_price: float,
        take_profit_pct: float,
        stop_loss_func,
        trailing_stop_pct: float,
        strategy_config: dict,
    ) -> TradeResult:
        """
        Simulate a single trade with the given parameters.

        Returns:
            TradeResult with all trade details
        """
        prices = self.generate_market_path(
            entry_price, steps=60
        )  # ~60 seconds before close

        entry_capital = strategy_config["entry_capital"]
        current_capital = entry_capital

        # Initialize stops
        stop_price = stop_loss_func(entry_price)
        trailing_stop_price = None
        high_water_mark = entry_price

        take_profit_price = entry_price * (1 + take_profit_pct)

        for i, price in enumerate(prices):
            # Update trailing stop
            if price > high_water_mark:
                high_water_mark = price
                trailing_stop_price = high_water_mark * (1 - trailing_stop_pct)

            # Check take-profit
            if price > take_profit_price:
                exit_price = price
                profit_pct = (exit_price - entry_price) / entry_price
                profit_usd = entry_capital * profit_pct
                current_capital = entry_capital + profit_usd

                return TradeResult(
                    entry_price=entry_price,
                    exit_price=exit_price,
                    profit_pct=profit_pct,
                    profit_usd=profit_usd,
                    reason="TAKE-PROFIT",
                    entry_capital=entry_capital,
                    exit_capital=current_capital,
                )

            # Check trailing stop
            if trailing_stop_price is not None and price < trailing_stop_price:
                exit_price = price
                profit_pct = (exit_price - entry_price) / entry_price
                profit_usd = entry_capital * profit_pct
                current_capital = entry_capital + profit_usd

                return TradeResult(
                    entry_price=entry_price,
                    exit_price=exit_price,
                    profit_pct=profit_pct,
                    profit_usd=profit_usd,
                    reason="TRAILING-STOP",
                    entry_capital=entry_capital,
                    exit_capital=current_capital,
                )

            # Check stop-loss
            if price < stop_price:
                exit_price = price
                profit_pct = (exit_price - entry_price) / entry_price
                profit_usd = entry_capital * profit_pct
                current_capital = entry_capital + profit_usd

                return TradeResult(
                    entry_price=entry_price,
                    exit_price=exit_price,
                    profit_pct=profit_pct,
                    profit_usd=profit_usd,
                    reason="STOP-LOSS",
                    entry_capital=entry_capital,
                    exit_capital=current_capital,
                )

        # If no trigger, exit at final price
        exit_price = prices[-1]
        profit_pct = (exit_price - entry_price) / entry_price
        profit_usd = entry_capital * profit_pct
        current_capital = entry_capital + profit_usd

        return TradeResult(
            entry_price=entry_price,
            exit_price=exit_price,
            profit_pct=profit_pct,
            profit_usd=profit_usd,
            reason="MARKET-CLOSE",
            entry_capital=entry_capital,
            exit_capital=current_capital,
        )


class CapitalProjectionSimulator:
    """Simulates capital projection over 1 month for different strategies."""

    def __init__(self, config: SimulationConfig, num_simulations: int = 1000):
        """
        Initialize simulator.

        Args:
            config: Simulation configuration
            num_simulations: Number of Monte Carlo runs per strategy
        """
        self.config = config
        self.num_simulations = num_simulations
        self.market_sim = MarketSimulator(base_win_rate=0.65, volatility=0.15)

    def _calculate_trade_size(self, current_capital: float) -> float:
        """Calculate trade size based on current capital and constraints."""
        # Trade size is MAX of:
        # 1. Configured trade size ($1.10)
        # 2. 5% of current capital
        # 3. MIN_TRADE_USDC ($1.00)

        size_pct = current_capital * self.config.max_capital_per_trade
        trade_size = max(self.config.trade_size_usd, 1.00, size_pct)

        # Ensure not exceeding 5% of capital
        trade_size = min(
            trade_size, current_capital * self.config.max_capital_per_trade
        )

        # Ensure not exceeding MAX_TRADE_USDC ($10)
        trade_size = min(trade_size, 10.00)

        return trade_size

    def _simulate_day(
        self, initial_capital: float, stop_loss_func, strategy_config: dict
    ) -> Tuple[float, List[TradeResult]]:
        """
        Simulate a single trading day.

        Returns:
            Tuple of (final_capital, list_of_trades)
        """
        capital = initial_capital
        trades: List[TradeResult] = []
        daily_loss_limit = initial_capital * self.config.daily_loss_limit_pct

        # Simulate 5-10 markets per day (random)
        num_markets = np.random.randint(5, 11)

        for market_idx in range(num_markets):
            # Check daily loss limit
            daily_pnl = capital - initial_capital
            if daily_pnl < -daily_loss_limit:
                # Daily loss limit hit - stop trading
                break

            # Check max trades per day
            if len(trades) >= self.config.max_trades_per_day:
                break

            # Simulate market conditions
            # Determine if we enter (based on confidence filter)
            # 85% of markets pass the confidence threshold
            passes_confidence = np.random.random() < self.config.min_confidence

            if not passes_confidence:
                continue  # Skip this market

            # Determine entry price (0.85 to 0.99 based on confidence)
            entry_price = np.random.uniform(0.85, 0.99)

            # Calculate trade size
            trade_size = self._calculate_trade_size(capital)

            # Simulate trade
            strategy_config["entry_capital"] = capital

            trade = self.market_sim.simulate_trade(
                entry_price=entry_price,
                take_profit_pct=self.config.take_profit_pct,
                stop_loss_func=lambda ep: stop_loss_func(ep),
                trailing_stop_pct=self.config.trailing_stop_pct,
                strategy_config=strategy_config,
            )

            trades.append(trade)
            capital = trade.exit_capital

            # Check for bankruptcy
            if capital < 0.50:
                break

        return capital, trades

    def simulate_strategy(
        self, stop_loss_func, strategy_name: str, strategy_params: dict = None
    ) -> dict:
        """
        Simulate a complete strategy over 1 month.

        Args:
            stop_loss_func: Function that calculates stop loss price
            strategy_name: Name of the strategy
            strategy_params: Additional parameters for the strategy

        Returns:
            Dictionary with simulation results
        """
        final_capitals = []
        all_trades = []
        daily_capitals = []

        for sim_idx in range(self.num_simulations):
            capital = self.config.initial_capital
            daily_capital_history = [capital]
            day_trades = []

            for day in range(self.config.trading_days):
                strategy_config = {}
                capital, trades = self._simulate_day(
                    capital, stop_loss_func, strategy_config
                )
                daily_capital_history.append(capital)
                day_trades.extend(trades)

                # Check for bankruptcy
                if capital < 0.50:
                    break

            final_capitals.append(capital)
            all_trades.extend(day_trades)
            daily_capitals.append(daily_capital_history)

        # Calculate statistics
        final_capitals = np.array(final_capitals)
        all_pnls = [trade.profit_usd for trade in all_trades]
        all_pnls = np.array(all_pnls)

        # Trade statistics
        total_trades = len(all_trades)
        winning_trades = len([t for t in all_trades if t.profit_usd > 0])
        losing_trades = len([t for t in all_trades if t.profit_usd < 0])
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        # Exit reason statistics
        exit_reasons = {}
        for trade in all_trades:
            exit_reasons[trade.reason] = exit_reasons.get(trade.reason, 0) + 1

        # Risk metrics
        bankruptcy_rate = (
            len(final_capitals[final_capitals < 0.50]) / len(final_capitals) * 100
        )
        max_drawdown = np.min([np.min(dc) / dc[0] - 1 for dc in daily_capitals]) * 100
        sharpe_ratio = np.mean(all_pnls) / np.std(all_pnls) if len(all_pnls) > 1 else 0

        return {
            "strategy_name": strategy_name,
            "params": strategy_params,
            "final_capitals": final_capitals,
            "statistics": {
                "mean_final_capital": np.mean(final_capitals),
                "median_final_capital": np.median(final_capitals),
                "std_final_capital": np.std(final_capitals),
                "min_final_capital": np.min(final_capitals),
                "max_final_capital": np.max(final_capitals),
                "percentile_5": np.percentile(final_capitals, 5),
                "percentile_25": np.percentile(final_capitals, 25),
                "percentile_75": np.percentile(final_capitals, 75),
                "percentile_95": np.percentile(final_capitals, 95),
            },
            "trade_stats": {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": win_rate,
                "avg_profit_per_trade": np.mean(all_pnls) if len(all_pnls) > 0 else 0,
                "avg_win": np.mean(
                    [t.profit_usd for t in all_trades if t.profit_usd > 0]
                )
                if winning_trades > 0
                else 0,
                "avg_loss": np.mean(
                    [t.profit_usd for t in all_trades if t.profit_usd < 0]
                )
                if losing_trades > 0
                else 0,
            },
            "risk_metrics": {
                "bankruptcy_rate": bankruptcy_rate,
                "max_drawdown_pct": max_drawdown,
                "sharpe_ratio": sharpe_ratio,
            },
            "exit_reasons": exit_reasons,
        }

    def compare_strategies(self) -> dict:
        """
        Compare all 3 stop-loss strategies.

        Returns:
            Dictionary with comparison results
        """
        results = {}

        # Strategy 1: Only 30% stop-loss
        def sl_func_1(entry_price):
            return entry_price * (1 - self.config.stop_loss_pct)

        results["strategy_1"] = self.simulate_strategy(
            sl_func_1,
            "Strategy 1: Only 30% Stop-Loss",
            {"stop_loss_pct": self.config.stop_loss_pct},
        )

        # Strategy 2: Only $0.80 absolute floor
        def sl_func_2(entry_price):
            return self.config.stop_loss_absolute

        results["strategy_2"] = self.simulate_strategy(
            sl_func_2,
            "Strategy 2: Only $0.80 Absolute Floor",
            {"absolute_floor": self.config.stop_loss_absolute},
        )

        # Strategy 3: Both 30% AND $0.80 absolute floor
        def sl_func_3(entry_price):
            return max(
                entry_price * (1 - self.config.stop_loss_pct),
                self.config.stop_loss_absolute,
            )

        results["strategy_3"] = self.simulate_strategy(
            sl_func_3,
            "Strategy 3: Both 30% + $0.80 Floor",
            {
                "stop_loss_pct": self.config.stop_loss_pct,
                "absolute_floor": self.config.stop_loss_absolute,
            },
        )

        return results


def print_comparison_report(results: dict):
    """Print a detailed comparison report of all strategies."""
    print("=" * 80)
    print("CAPITAL PROJECTION: 1 MONTH TRADING SIMULATION")
    print("=" * 80)
    print(f"Initial Capital: ${4.49:.2f}")
    print(f"Trade Size: ${1.10:.2f}")
    print(f"Simulation Runs: 1000 per strategy")
    print(f"Trading Days: 30")
    print("=" * 80)
    print()

    # Print summary table
    print("📊 FINAL CAPITAL SUMMARY")
    print("-" * 80)
    print(
        f"{'Strategy':<45} | {'Mean':>12} | {'Median':>10} | {'5th %ile':>10} | {'95th %ile':>10}"
    )
    print("-" * 80)

    for key, result in results.items():
        stats = result["statistics"]
        name = result["strategy_name"]
        print(
            f"{name:<45} | ${stats['mean_final_capital']:>10.2f} | ${stats['median_final_capital']:>8.2f} | "
            f"${stats['percentile_5']:>8.2f} | ${stats['percentile_95']:>8.2f}"
        )

    print("-" * 80)
    print()

    # Print trade statistics
    print("📈 TRADE STATISTICS")
    print("-" * 80)
    print(
        f"{'Strategy':<45} | {'Win Rate':>10} | {'Avg Win':>10} | {'Avg Loss':>10} | {'Sharpe':>8}"
    )
    print("-" * 80)

    for key, result in results.items():
        ts = result["trade_stats"]
        rm = result["risk_metrics"]
        name = result["strategy_name"]
        print(
            f"{name:<45} | {ts['win_rate']:>8.1f}% | ${ts['avg_win']:>8.2f} | ${ts['avg_loss']:>8.2f} | {rm['sharpe_ratio']:>6.2f}"
        )

    print("-" * 80)
    print()

    # Print risk metrics
    print("⚠️  RISK METRICS")
    print("-" * 80)
    print(f"{'Strategy':<45} | {'Bankruptcy':>12} | {'Max Drawdown':>14}")
    print("-" * 80)

    for key, result in results.items():
        rm = result["risk_metrics"]
        name = result["strategy_name"]
        print(
            f"{name:<45} | {rm['bankruptcy_rate']:>10.1f}% | {rm['max_drawdown_pct']:>12.1f}%"
        )

    print("-" * 80)
    print()

    # Print exit reasons
    print("🚪 EXIT REASON BREAKDOWN")
    print("-" * 80)

    for key, result in results.items():
        name = result["strategy_name"]
        print(f"\n{name}:")
        total = sum(result["exit_reasons"].values())
        for reason, count in sorted(
            result["exit_reasons"].items(), key=lambda x: x[1], reverse=True
        ):
            pct = count / total * 100 if total > 0 else 0
            print(f"  {reason:<20}: {count:5d} ({pct:5.1f}%)")

    print("-" * 80)
    print()

    # Detailed analysis for each strategy
    for key, result in results.items():
        print("=" * 80)
        print(f"DETAILED ANALYSIS: {result['strategy_name']}")
        print("=" * 80)

        stats = result["statistics"]
        ts = result["trade_stats"]
        rm = result["risk_metrics"]

        print(f"\n📊 Final Capital Distribution:")
        print(f"  Mean:     ${stats['mean_final_capital']:>8.2f}")
        print(f"  Median:   ${stats['median_final_capital']:>8.2f}")
        print(f"  Std Dev:  ${stats['std_final_capital']:>8.2f}")
        print(
            f"  Range:    ${stats['min_final_capital']:>8.2f} - ${stats['max_final_capital']:>8.2f}"
        )
        print(f"  5th %ile: ${stats['percentile_5']:>8.2f}")
        print(f"  25th %ile:${stats['percentile_25']:>8.2f}")
        print(f"  75th %ile:${stats['percentile_75']:>8.2f}")
        print(f"  95th %ile:${stats['percentile_95']:>8.2f}")

        print(f"\n📈 Trade Performance:")
        print(f"  Total Trades:     {ts['total_trades']}")
        print(f"  Winning Trades:   {ts['winning_trades']}")
        print(f"  Losing Trades:    {ts['losing_trades']}")
        print(f"  Win Rate:         {ts['win_rate']:.1f}%")
        print(f"  Avg Profit/Trade: ${ts['avg_profit_per_trade']:.4f}")
        print(f"  Avg Win:          ${ts['avg_win']:.4f}")
        print(f"  Avg Loss:         ${ts['avg_loss']:.4f}")

        print(f"\n⚠️  Risk Profile:")
        print(f"  Bankruptcy Rate:  {rm['bankruptcy_rate']:.1f}%")
        print(f"  Max Drawdown:     {rm['max_drawdown_pct']:.1f}%")
        print(f"  Sharpe Ratio:     {rm['sharpe_ratio']:.2f}")

        if result["params"]:
            print(f"\n⚙️  Parameters:")
            for k, v in result["params"].items():
                print(f"  {k}: {v}")

        print()

    # Recommendation
    print("=" * 80)
    print("🎯 RECOMMENDATION")
    print("=" * 80)
    print()

    # Analyze which strategy is best
    best_sharpe = max(
        results.items(), key=lambda x: x[1]["risk_metrics"]["sharpe_ratio"]
    )
    lowest_bankruptcy = min(
        results.items(), key=lambda x: x[1]["risk_metrics"]["bankruptcy_rate"]
    )
    highest_mean = max(
        results.items(), key=lambda x: x[1]["statistics"]["mean_final_capital"]
    )

    print("Based on Monte Carlo simulation (1000 runs x 30 days):")
    print()
    print(f"🏆 Best Sharpe Ratio: {best_sharpe[1]['strategy_name']}")
    print(f"   - Sharpe: {best_sharpe[1]['risk_metrics']['sharpe_ratio']:.2f}")
    print(
        f"   - Mean Capital: ${best_sharpe[1]['statistics']['mean_final_capital']:.2f}"
    )
    print(
        f"   - Bankruptcy Rate: {best_sharpe[1]['risk_metrics']['bankruptcy_rate']:.1f}%"
    )
    print()

    print(f"🛡️  Lowest Bankruptcy Risk: {lowest_bankruptcy[1]['strategy_name']}")
    print(
        f"   - Bankruptcy Rate: {lowest_bankruptcy[1]['risk_metrics']['bankruptcy_rate']:.1f}%"
    )
    print(
        f"   - Mean Capital: ${lowest_bankruptcy[1]['statistics']['mean_final_capital']:.2f}"
    )
    print()

    print(f"💰 Highest Expected Value: {highest_mean[1]['strategy_name']}")
    print(
        f"   - Mean Capital: ${highest_mean[1]['statistics']['mean_final_capital']:.2f}"
    )
    print(f"   - 95th %ile: ${highest_mean[1]['statistics']['percentile_95']:.2f}")
    print(f"   - 5th %ile: ${highest_mean[1]['statistics']['percentile_5']:.2f}")
    print()

    # Final recommendation
    print("=" * 80)
    print("📋 FINAL RECOMMENDATION:")
    print("=" * 80)
    print()

    if lowest_bankruptcy[0] == "strategy_3":
        print("✅ RECOMMENDED: Strategy 3 (Both 30% + $0.80 Floor)")
        print()
        print("Rationale:")
        print("  • Best bankruptcy protection (lowest bankruptcy rate)")
        print("  • Balanced risk/reward profile")
        print("  • Absolute floor protects against extreme price collapses")
        print("  • 30% stop-loss allows reasonable room for normal volatility")
        print("  • Most conservative option for capital preservation")
    elif lowest_bankruptcy[0] == "strategy_2":
        print("✅ RECOMMENDED: Strategy 2 (Only $0.80 Absolute Floor)")
        print()
        print("Rationale:")
        print("  • Best bankruptcy protection")
        print("  • Simple and predictable")
        print("  • Maximum capital preservation")
    else:
        print("✅ RECOMMENDED: Strategy 1 (Only 30% Stop-Loss)")
        print()
        print("Rationale:")
        print("  • Highest expected value")
        print("  • Best Sharpe ratio")
        print("  • Allows for larger profits on winning trades")
        print("  • Higher risk but higher reward")

    print()
    print("=" * 80)


def main():
    """Run the capital projection simulation."""
    config = SimulationConfig()

    print(f"Running Monte Carlo simulation with {config.trading_days} trading days...")
    print(f"Simulating 3 stop-loss strategies...")
    print()

    simulator = CapitalProjectionSimulator(config, num_simulations=1000)
    results = simulator.compare_strategies()

    # Print detailed report
    print_comparison_report(results)

    # Save results to JSON
    output = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "initial_capital": config.initial_capital,
            "trade_size": config.trade_size_usd,
            "stop_loss_pct": config.stop_loss_pct,
            "stop_loss_absolute": config.stop_loss_absolute,
            "take_profit_pct": config.take_profit_pct,
            "trailing_stop_pct": config.trailing_stop_pct,
            "min_confidence": config.min_confidence,
            "daily_loss_limit_pct": config.daily_loss_limit_pct,
            "max_trades_per_day": config.max_trades_per_day,
            "max_capital_per_trade": config.max_capital_per_trade,
            "trading_days": config.trading_days,
            "num_simulations": 1000,
        },
        "results": {
            k: {
                "strategy_name": v["strategy_name"],
                "statistics": {sk: float(sv) for sk, sv in v["statistics"].items()},
                "trade_stats": {
                    sk: float(sv) if isinstance(sv, (int, float)) else sv
                    for sk, sv in v["trade_stats"].items()
                },
                "risk_metrics": {sk: float(sv) for sk, sv in v["risk_metrics"].items()},
                "exit_reasons": v["exit_reasons"],
            }
            for k, v in results.items()
        },
    }

    with open("capital_projection_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n💾 Results saved to: capital_projection_results.json")


if __name__ == "__main__":
    main()
