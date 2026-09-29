"""批量 LLM 公共件单测（无网络、无 DB）。

这些函数处理的是「错了不会报错、只会静默丢数据」的形态，故用真实见过的坏输出做回归：
- 模型先吐半截再吐完整数组（首版 find('[')+rfind(']') 会拼成非法 JSON，整批 10 条全丢）
- 围栏代码块、前后缀说明、嵌套小数组
- 幻觉 id、非法枚举值
- 折半重试：整批失败但折半后部分成功 → 成功的必须全部保下来
"""
import json

from scripts import llm_batch as lb


class TestExtractJsonArray:
    def test_plain(self):
        assert lb.extract_json_array('[{"id":1}]') == [{'id': 1}]

    def test_fenced(self):
        got = lb.extract_json_array('```json\n[{"id": 1, "s": 1}]\n```')
        assert got == [{'id': 1, 's': 1}]

    def test_prefix_explanation(self):
        got = lb.extract_json_array('好的，结果如下：\n[{"id":2}]')
        assert got == [{'id': 2}]

    def test_half_then_full_takes_longest(self):
        # 模型先吐一个截断数组再吐完整的：必须取吃掉文本最多的那个
        content = '[{"id":1,"s":1}\n[{"id":1,"s":1},{"id":2,"s":0},{"id":3,"s":-1}]'
        got = lb.extract_json_array(content)
        assert got == [{'id': 1, 's': 1}, {'id': 2, 's': 0}, {'id': 3, 's': -1}]

    def test_nested_inner_array_not_taken(self):
        got = lb.extract_json_array('前置说明 [1,2] 后置 [{"id":9,"s":1}]')
        assert got == [{'id': 9, 's': 1}]

    def test_no_array(self):
        assert lb.extract_json_array('抱歉，我无法完成') is None

    def test_empty(self):
        assert lb.extract_json_array('') is None
        assert lb.extract_json_array(None) is None


class TestPickByIds:
    def test_drops_hallucinated_ids(self):
        arr = [{'id': 1}, {'id': 99}, {'id': 2}]
        assert lb.pick_by_ids(arr, [1, 2]) == [{'id': 1}, {'id': 2}]

    def test_accepts_str_and_int_ids(self):
        assert lb.pick_by_ids([{'id': '7'}], [7]) == [{'id': '7'}]

    def test_skips_non_dict(self):
        assert lb.pick_by_ids([1, 'x', {'id': 1}], [1]) == [{'id': 1}]

    def test_empty(self):
        assert lb.pick_by_ids(None, [1]) == []
        assert lb.pick_by_ids([], [1]) == []


class TestRetrySplit:
    def test_success_first_try(self):
        calls = []

        def call(b):
            calls.append(list(b))
            return [x * 10 for x in b]
        assert lb.retry_split(call, [1, 2]) == [10, 20]
        assert calls == [[1, 2]]

    def test_halving_recovers_partial(self):
        # 整批必失败；折半后只有含 3 的批次失败 → 其余必须全部保下来
        def call(b):
            if 3 in b:
                return None
            return list(b)
        got = lb.retry_split(call, [1, 2, 3, 4])
        assert sorted(got) == [1, 2, 4]

    def test_single_failure_gives_nothing(self):
        assert lb.retry_split(lambda b: None, [1]) == []

    def test_empty_result_counts_as_failure_and_halves(self):
        # 回归（2026-09-29）：调用方把「模型回了但一条没回收到」也表示成 []，
        # 旧版 is not None 会当成功收下 → 整批静默丢失、零重试、零日志
        calls = []

        def call(b):
            calls.append(list(b))
            return [] if len(b) > 2 else list(b)
        got = lb.retry_split(call, [1, 2, 3, 4])
        assert sorted(got) == [1, 2, 3, 4]        # 折半后救回
        assert len(calls) > 1

    def test_all_empty_ends_up_with_nothing(self):
        got = lb.retry_split(lambda b: [], [1, 2, 3, 4])
        assert got == []

    def test_empty_batch(self):
        assert lb.retry_split(lambda b: [1], []) == []


class TestRunBatches:
    def test_empty(self):
        r = lb.run_batches([], lambda b: b, lambda b, r: None)
        assert r == {'done': 0, 'fail': 0, 'total': 0, 'req': 0, 'elapsed': 0.0}

    def test_counts_done_and_fail(self):
        written = []
        items = list(range(5))
        # 批次大小 2 → [0,1] [2,3] [4]；让第二批判定为坏批
        def run_one(b):
            return None if b == [2, 3] else list(b)
        r = lb.run_batches(items, run_one, lambda b, res: written.extend(res),
                           workers=2, rpm=1000, batch_size=2)
        assert r['total'] == 5 and r['req'] == 3
        assert r['done'] == 3 and r['fail'] == 2
        assert sorted(written) == [0, 1, 4]

    def test_partial_results_count_rest_as_fail(self):
        # 折半重试后仍丢的单条要计入 fail（不能只看批成败）
        def run_one(b):
            return [x for x in b if x != 1]
        r = lb.run_batches([1, 2], run_one, lambda b, res: None, workers=1, rpm=1000,
                           batch_size=2)
        assert r['done'] == 1 and r['fail'] == 1

    def test_llm_json_batch_retries_on_empty_array(self, monkeypatch):
        # 模型只回空数组 → 必须当失败重试，而不是当成功收下（同 retry_split 的回归）
        class _Msg:
            content = '```json\n[]\n```'

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        class _Completions:
            n = 0

            def create(self, **kw):
                _Completions.n += 1
                return _Resp()

        class _Client:
            chat = type('C', (), {'completions': _Completions()})()

        monkeypatch.setattr(lb.time, 'sleep', lambda s: None)
        assert lb.llm_json_batch(_Client(), 'm', 'p', fail_max=2, log_tag='t') is None
        assert _Completions.n == 2      # 空数组走了重试，没有一次就当成功

    def test_progress_cb_receives_running_totals(self):
        seen = []
        lb.run_batches(list(range(4)), lambda b: list(b),
                       lambda b, res: None, workers=2, rpm=1000, batch_size=2,
                       progress_cb=lambda d, f, t: seen.append((d, f, t)))
        assert seen and all(t == 4 for _, _, t in seen)
        assert seen[-1][0] == 4 and seen[-1][1] == 0
