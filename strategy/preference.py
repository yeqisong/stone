"""F-ST-005 全局偏好修饰器。运行时动态调整 3 个信号策略的参数。"""

PREFERENCE_ADJUSTMENTS = {
    "left": {
        "bollinger_daily": {
            "std_mult": -0.3,
            "bandwidth_threshold": 0.02,
            "rsi_oversold": 35,
            "rsi_overbought": 65,
        },
        "volume_price_divergence": {
            "min_gap_daily": -2,
            "min_gap_weekly": -1,
            "min_gap_monthly": -1,
            "vol_ratio_threshold": -0.05,
        },
        "weekly_trend": {
            "require_vol_confirm": False,
            "vol_strong_mult": 1.0,
            "atr_stop_mult": 1.5,
        },
    },
    "balanced": {
        # 不做任何修改，使用各策略默认参数
    },
    "right": {
        "bollinger_daily": {
            "std_mult": 0.3,
            "bandwidth_threshold": 0.0,
            "rsi_oversold": 25,
            "rsi_overbought": 75,
        },
        "volume_price_divergence": {
            "min_gap_daily": 2,
            "min_gap_weekly": 1,
            "min_gap_monthly": 1,
            "vol_ratio_threshold": 0.05,
        },
        "weekly_trend": {
            "require_vol_confirm": True,
            "vol_strong_mult": 1.5,
            "atr_stop_mult": 0.75,
        },
    },
}


def apply_preference(adjusted_params_list):
    """
    闭包函数，返回一个 apply(strategy_name, base_params) -> adjusted_params 的函数。

    用法:
        modifier = apply_preference(load_adjustments_from_db())
        params = modifier("bollinger_daily", {"period": 20, "std_mult": 2.0})
    """
    def _apply(strategy_name: str, base_params: dict) -> dict:
        prefs = adjusted_params_list.get(strategy_name, {})
        if not prefs:
            return base_params
        result = dict(base_params)
        for key, delta in prefs.items():
            if key in result:
                if isinstance(delta, bool):
                    result[key] = delta
                else:
                    result[key] = result[key] + delta
            else:
                result[key] = delta
        return result

    return _apply
