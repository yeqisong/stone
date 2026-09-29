"""批量 LLM 调用的公共件（雪球情绪打分、资讯总结共用）。

抽出这份实现的理由：三处都是「错了不会报错、只会静默丢数据」的形态，复制粘贴
必然导致两条链路口径分叉（本仓库忌讳同一口径两套实现，见模型评估链路）。

- extract_json_array  : 从模型输出抠 JSON 数组（容忍围栏/前后缀/多个数组并存）
- llm_json_batch      : 单批调用 + 重试，只负责「问到一段合法 JSON 数组」
- retry_split         : 整批失败时折半重试，单条坏 JSON 只牺牲自己
- run_batches         : RPM 滑动窗口 + 线程池 + 失败计数的主循环
                        （并发只在 LLM 调用；DB 写入经 on_ok 固定在主线程）
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger


def extract_json_array(content: str):
    """从模型输出里抠出 JSON 数组。

    容忍围栏代码块、前后缀说明，以及模型先吐半截再吐完整数组的情形——首版用
    find('[')+rfind(']') 切片，两数组并存时会拼成非法 JSON，整批 10 条全丢。
    取「吃掉最多文本」的那个数组，避免误取嵌套的内层小数组。
    """
    dec = json.JSONDecoder()
    s = (content or '').strip()
    best, best_end = None, -1
    for i, ch in enumerate(s):
        if ch != '[':
            continue
        try:
            obj, end = dec.raw_decode(s[i:])
        except ValueError:
            continue
        if isinstance(obj, list) and end > best_end:
            best, best_end = obj, end
    return best


def pick_by_ids(arr, valid_ids, id_key: str = 'id'):
    """幻觉 id 防御：只保留 id 属于本次请求条目的结果（模型会编出不存在的 id）。"""
    vs = {str(v) for v in valid_ids}
    out = []
    for it in arr or []:
        if not isinstance(it, dict):
            continue
        if str(it.get(id_key)) in vs:
            out.append(it)
    return out


def llm_json_batch(client, model: str, prompt: str, *, fail_max: int = 3,
                   max_tokens: int = 1500, temperature: float = 0,
                   log_tag: str = 'llm'):
    """单批 LLM 调用 → 抠出的 JSON 数组；调用/解析失败返回 None（内部已重试）。

    只负责「问到一段合法 JSON 数组」，条目字段的语义校验由调用方做（各任务不同）。
    """
    for attempt in range(1, max(1, fail_max) + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            arr = extract_json_array(resp.choices[0].message.content)
            if arr is None:
                raise ValueError('输出中找不到合法 JSON 数组')
            return arr
        except Exception as e:
            logger.warning(f'[{log_tag}] 批次失败 第 {attempt} 次: {str(e)[:100]}')
            if attempt < fail_max:
                time.sleep(3 * attempt)
    return None


def retry_split(call_fn, batch, *, log_tag: str = 'llm'):
    """整批失败时折半重试：单条坏 JSON 只牺牲自己，不拖累同批其余条目。

    call_fn(batch) 返回结果列表，失败返回 None。
    """
    if not batch:
        return []
    res = call_fn(batch)
    if res is not None:
        return res
    if len(batch) == 1:
        return []
    mid = len(batch) // 2
    logger.warning(f'[{log_tag}] 批次失败，折半重试 {len(batch)} → {mid}+{len(batch) - mid}')
    return (retry_split(call_fn, batch[:mid], log_tag=log_tag)
            + retry_split(call_fn, batch[mid:], log_tag=log_tag))


def run_batches(items, run_one, on_ok, *, workers: int = 8, rpm: int = 25,
                batch_size: int = 10, progress_cb=None, log_tag: str = 'llm'):
    """批量 LLM 主循环。CLI 与 DAG 节点共用同一份实现，避免口径漂移。

    - 并发只发生在 LLM 调用（run_one 在线程池里跑）；on_ok 固定在主线程被调用，
      保证 DB commit 顺序，也保证 psycopg2 连接不被多线程共享
    - run_one(batch) → 结果列表或 None（失败）；on_ok(batch, results) 负责落库
    - progress_cb(done, fail, total) 每 2 批报一次：LLM 故障时每批要熬 3 次重试
      （3+6s 退避）≈27s，每 10 批就是 270s，逼近 DAG 看门狗 5 分钟阈值（曾实测被误杀）

    返回 {'done','fail','total','req','elapsed'}。
    """
    if not items:
        return {'done': 0, 'fail': 0, 'total': 0, 'req': 0, 'elapsed': 0.0}
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    done = fail = 0
    t0 = time.time()
    t_sub: list = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for b in batches:
            # RPM 滑动窗口闸门（并发数已自然限速，此处兜住 API 硬上限）
            while True:
                now = time.time()
                t_sub[:] = [t for t in t_sub if now - t < 60]
                if len(t_sub) < rpm:
                    break
                time.sleep(min(5.0, 60 - (now - t_sub[0]) + 0.1))
            t_sub.append(time.time())
            futs[ex.submit(run_one, b)] = b

        for n_batch, fut in enumerate(as_completed(futs), 1):
            b = futs[fut]
            results = fut.result() or []
            if not results:
                fail += len(b)
                logger.error(f'[{log_tag}] 批次连续失败，跳过 {len(b)} 条')
            else:
                on_ok(b, results)
                done += len(results)
                fail += len(b) - len(results)   # 折半重试后仍丢的单条计入失败
            if n_batch % (2 if progress_cb else 10) == 0:
                if progress_cb:
                    progress_cb(done, fail, len(items))
                else:
                    el = time.time() - t0
                    logger.info(f'[{log_tag}] 进度 {min(n_batch * batch_size, len(items))}/{len(items)}'
                                f' (done {done}, fail {fail}) {el:.0f}s '
                                f'{n_batch / el * 60:.1f} req/min')
    return {'done': done, 'fail': fail, 'total': len(items), 'req': len(batches),
            'elapsed': time.time() - t0}
