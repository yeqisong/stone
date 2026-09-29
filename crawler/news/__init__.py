"""资讯采集（design/07 第 1 层「原文」）。

- sources.py  各源 fetch 函数（纯 HTTP，无 DB）
- match.py    文本 → 个股匹配（纯函数）
- collect.py  采集编排（DB 感知）：快讯增量子 + 关注池分片轮转 + 入库
"""
