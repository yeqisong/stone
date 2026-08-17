"""数据源管理器：健康检查 + 优先级选源 + 自动 fallback。

设计参考：design/data-source-adapter-design.md 第六章

用法：
    from crawler.adapters.manager import get_manager

    manager = get_manager()
    source = manager.get_source()              # 获取最优可用源
    rows, name = manager.fetch_with_fallback(  # 带自动切换的拉取
        "fetch_stock_kline", codes, start, end
    )
"""
import time
from typing import Optional, Tuple, Any, List
from loguru import logger

from crawler.adapters.base import DataSourceAdapter


class DataSourceManager:
    """数据源管理器。

    职责：
    1. 管理多个数据源适配器（按优先级排序）
    2. 定期探测各源健康状态（缓存 5 分钟）
    3. 提供 get_source() 返回最优可用源
    4. 提供 fetch_with_fallback() 自动切换机制
    """

    def __init__(self, cache_ttl: int = 300):
        """
        Args:
            cache_ttl: 健康检查缓存有效期（秒），默认 5 分钟
        """
        self._sources: List[DataSourceAdapter] = []
        self._supplements: List[DataSourceAdapter] = []  # 补充器（baostock），不参与选源
        self._health_cache: dict[str, Tuple[bool, float]] = {}  # {name: (healthy, timestamp)}
        self._cache_ttl = cache_ttl

    @property
    def sources(self) -> List[DataSourceAdapter]:
        """返回已注册的数据源列表（按优先级排序）。"""
        return list(self._sources)

    def register(self, adapter: DataSourceAdapter) -> None:
        """注册数据源适配器。

        Args:
            adapter: 实现了 DataSourceAdapter 接口的适配器实例
        """
        # 避免重复注册
        for existing in self._sources:
            if existing.name == adapter.name:
                logger.warning(f"[DataSource] 适配器 {adapter.name} 已注册，跳过重复注册")
                return
        self._sources.append(adapter)
        self._sources.sort(key=lambda s: s.priority)
        logger.info(f"[DataSource] 注册适配器: {adapter.name} (priority={adapter.priority})")

    def check_all_health(self) -> dict[str, bool]:
        """检查所有已注册数据源的健康状态。

        使用缓存避免频繁探测（TTL 由 _cache_ttl 控制）。

        Returns:
            {adapter_name: is_healthy}
        """
        result = {}
        for src in self._sources:
            cached = self._health_cache.get(src.name)
            # 缓存未过期则直接使用
            if cached and (time.time() - cached[1]) < self._cache_ttl:
                result[src.name] = cached[0]
                continue
            # 执行健康检查
            try:
                healthy = src.check_health()
            except Exception as e:
                logger.warning(f"[DataSource] {src.name} 健康检查异常: {e}")
                healthy = False
            self._health_cache[src.name] = (healthy, time.time())
            result[src.name] = healthy
            status = "✅ 可用" if healthy else "❌ 不可用"
            logger.info(f"[DataSource] {src.name} 健康检查: {status}")
        return result

    def invalidate_cache(self, name: Optional[str] = None) -> None:
        """手动清除健康缓存。

        Args:
            name: 指定适配器名称，None 表示清除全部
        """
        if name:
            self._health_cache.pop(name, None)
        else:
            self._health_cache.clear()

    def get_source(self) -> DataSourceAdapter:
        """返回唯一主数据源（tushare）。

        v3.2 简化：不再多源 fallback。tushare 不可用即失败（配额耗尽由
        QuotaExhausted 异常表达，不属于"源不可用"）。

        Raises:
            RuntimeError: tushare 未注册或健康检查失败
        """
        if not self._sources:
            raise RuntimeError("未注册任何数据源适配器")
        health = self.check_all_health()
        for src in self._sources:
            if health.get(src.name, False):
                return src
        status_str = ", ".join(f"{s.name}=❌" for s in self._sources)
        raise RuntimeError(f"主数据源不可用: [{status_str}]")

    def get_supplement(self, name: str = "baostock"):
        """返回字段补充器（baostock），未注册返回 None。"""
        for s in self._supplements:
            if s.name == name:
                return s
        return None

    def get_health_summary(self) -> dict:
        """获取健康状态摘要（供 API 接口使用）。

        主源（tushare）+ 补充器（baostock）的健康状态并列展示；
        active_source 恒为主源。

        Returns:
            {
                "sources": [{"name": ..., "priority": ..., "healthy": ..., "checked_at": ...}],
                "active_source": "tushare" | None
            }
        """
        from datetime import datetime
        all_adapters = list(self._sources) + list(self._supplements)
        health = self.check_all_health()
        sources = []
        active = None
        for src in all_adapters:
            cached = self._health_cache.get(src.name)
            checked_at = datetime.fromtimestamp(cached[1]).isoformat() if cached else None
            healthy = health.get(src.name, False)
            sources.append({
                "name": src.name,
                "priority": src.priority,
                "healthy": healthy,
                "checked_at": checked_at,
            })
            if healthy and active is None and src in self._sources:
                active = src.name
        return {"sources": sources, "active_source": active}

    def supplement_healthy(self, name: str = "baostock") -> bool:
        """补充器健康检查（带 TTL 缓存）。

        补充器不可用时调用方应跳过补充（主字段照常入库），
        避免在慢速/不可用源上阻塞主流程（如 ETF 复权串行拉取）。
        """
        cached = self._health_cache.get(name)
        if cached and (time.time() - cached[1]) < self._cache_ttl:
            return cached[0]
        sup = self.get_supplement(name)
        if sup is None:
            return False
        try:
            healthy = sup.check_health()
        except Exception:
            healthy = False
        self._health_cache[name] = (healthy, time.time())
        return healthy

    def fetch_with_fallback(self, method_name: str, *args, **kwargs) -> Tuple[Any, str]:
        """数据拉取（v3.2 简化：仅走主源 tushare，无 fallback）。

        Args:
            method_name: 适配器方法名（如 "fetch_stock_kline"）
            *args, **kwargs: 传给方法的参数

        Returns:
            (数据结果, 数据源名称)

        Raises:
            RuntimeError: tushare 不可用或方法失败
        """
        if not self._sources:
            raise RuntimeError("未注册任何数据源适配器")
        src = self._sources[0]
        fn = getattr(src, method_name, None)
        if fn is None:
            raise RuntimeError(f"{src.name} 未实现 {method_name}")
        try:
            result = fn(*args, **kwargs)
            return result, src.name
        except Exception as e:
            raise RuntimeError(f"主数据源 {src.name}.{method_name} 失败: {type(e).__name__}: {str(e)[:200]}") from e

    def __repr__(self):
        names = [f"{s.name}(p={s.priority})" for s in self._sources]
        return f"<DataSourceManager sources=[{', '.join(names)}]>"


# ── 全局单例 ──

_manager: Optional[DataSourceManager] = None


def get_manager() -> DataSourceManager:
    """获取全局 DataSourceManager 单例。

    v3.2 架构简化：tushare 为唯一主数据源；
    baostock 降级为"字段补充器"（ROE/营收净利、ETF 复权），
    不再参与选源/fallback，仅保留健康状态供状态页展示。
    """
    global _manager
    if _manager is not None:
        return _manager

    _manager = DataSourceManager()

    # 注册 TuShare 主源（数据获取的唯一来源）
    try:
        from crawler.adapters.tushare_adapter import TuShareAdapter
        _manager.register(TuShareAdapter())
    except ImportError:
        logger.debug("[DataSource] TuShareAdapter 未安装，跳过注册")
    except Exception as e:
        logger.warning(f"[DataSource] TuShareAdapter 注册失败: {e}")

    # Baostock 补充器（仅健康展示，不注册为可拉取源）
    try:
        from crawler.adapters.baostock_adapter import BaostockAdapter
        _manager._supplements.append(BaostockAdapter())
        logger.info("[DataSource] 注册 baostock 补充器（ROE/营收净利/ETF复权）")
    except ImportError:
        logger.debug("[DataSource] BaostockAdapter 未安装，跳过")
    except Exception as e:
        logger.warning(f"[DataSource] BaostockAdapter 注册失败: {e}")

    return _manager


def reset_manager() -> None:
    """重置全局单例（仅用于测试）。"""
    global _manager
    _manager = None
