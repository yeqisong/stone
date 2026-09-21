"""雪球会话洗白器：playwright headless 过阿里云 WAF JS 挑战，获取/重置 cookie 集。

P0 标定结论（design/06 §9.2/§9.3）：
- 雪球主域挂阿里云 WAF JS 挑战，纯 HTTP（含 curl-cffi TLS 指纹模拟）无法通过；
- playwright headless 可稳定通过（chromium 需 executable_path 直指定，playwright 1.55
  不支持 ubuntu26.04 的自动安装路径）；
- 匿名 xq_a_token 按 IP 签发（同 IP 重取不变），"洗白"洗的是 WAF 会话 cookie（acw_tc 族）；
- 触发评分挑战后重过首页即清零（实测 2s 即恢复）。
"""
import time

from loguru import logger

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'


def find_chromium() -> str | None:
    """定位本机 playwright 缓存里的 chromium 可执行文件（chrome-linux* 布局）。"""
    import glob
    import os
    candidates = sorted(glob.glob(os.path.expanduser(
        '~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome')))
    return candidates[-1] if candidates else None


def wash(max_retry: int = 3) -> dict[str, str]:
    """过一次雪球首页 WAF 挑战，返回全套 cookie（dict）。

    失败重试（指数退避），全部失败抛 RuntimeError——调用方（guard）应暂停消费并告警。
    """
    from playwright.sync_api import sync_playwright, Error as PWError

    exe = find_chromium()
    if not exe:
        raise RuntimeError('未找到 playwright chromium（~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome）')

    last_err = None
    for attempt in range(1, max_retry + 1):
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=exe,
                                            args=['--no-sandbox'])
                try:
                    ctx = browser.new_context(locale='zh-CN', user_agent=UA,
                                              viewport={'width': 1440, 'height': 900})
                    page = ctx.new_page()
                    page.goto('https://xueqiu.com/', wait_until='domcontentloaded', timeout=30000)
                    cookies: dict[str, str] = {}
                    for _ in range(45):
                        cookies = {c['name']: c['value'] for c in ctx.cookies()}
                        if 'xq_a_token' in cookies:
                            break
                        page.wait_for_timeout(1000)
                    if 'xq_a_token' not in cookies:
                        raise RuntimeError('过首页后 45s 仍无 xq_a_token')
                    return cookies
                finally:
                    browser.close()
        except (PWError, RuntimeError, Exception) as e:  # noqa: B014 - 统一退避重试
            last_err = e
            logger.warning(f'雪球洗白第 {attempt} 次失败: {e}')
            time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f'雪球洗白连续 {max_retry} 次失败: {last_err}')
