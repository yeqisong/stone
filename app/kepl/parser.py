"""KEPL 公式解析器 — v2.0 重构 迭代 2.1。

实现：
- 词法分析（Tokenizer）
- 语法校验（6 类错误拦截）
- AST 提取（函数名、参数类型、实体引用）
"""
import re
from typing import List, Dict, Optional, Tuple
from enum import Enum


class TokenType(Enum):
    IDENT = "ident"          # close, ma, stock
    NUMBER = "number"        # 5, 20.5
    OP = "op"                # + - * / ( ) [ ] , .
    STRING = "string"        # '000300.SH'
    DOT = "dot"              # .
    EOF = "eof"


class Token:
    def __init__(self, typ: TokenType, value: str, pos: int):
        self.typ = typ
        self.value = value
        self.pos = pos

    def __repr__(self):
        return f"{self.typ}:{self.value}"


class KepLError(Exception):
    def __init__(self, code: str, message: str, pos: int = 0):
        self.code = code   # FEAT_002, FEAT_005, etc.
        self.message = message
        self.pos = pos
        super().__init__(message)


# ── 系统内置函数注册表 ──
TIME_SERIES_FUNCTIONS = {
    'ma', 'ema', 'rsi', 'std', 'atr', 'roc', 'bias', 'return_n', 'pct_change',
    'dif', 'dea', 'macd_hist', 'boll_upper', 'boll_mid', 'boll_lower',
    'corr', 'boll', 'sma', 'obv'
}

CROSS_SECTIONAL_FUNCTIONS = {
    'avg', 'sum', 'max', 'min', 'rank', 'quantile', 'zscore'
}

ALL_FUNCTIONS = TIME_SERIES_FUNCTIONS | CROSS_SECTIONAL_FUNCTIONS

# 预置数据源
DATA_SOURCES = {'stock', 'etf', 'index', 'financial', 'macro', 'north'}


# ── 词法分析器 ──
class KepLTokenizer:
    """KEPL 公式 → Token 序列。"""

    _TOKEN_PATTERNS = [
        (r'\d+\.?\d*', TokenType.NUMBER),
        (r'[a-zA-Z_][a-zA-Z0-9_]*', TokenType.IDENT),
        (r"'(?:[^'\\]|\\.)*'", TokenType.STRING),  # '000300.SH'
        (r'"(?:[^"\\]|\\.)*"', TokenType.STRING),  # "000300.SH"
        (r'\.', TokenType.DOT),
        (r'[+\-*/()\[\],]', TokenType.OP),
        (r'\s+', None),  # skip whitespace
    ]

    def __init__(self, source: str):
        self.source = source
        self.pos = 0

    def tokenize(self) -> List[Token]:
        tokens = []
        pos = 0
        while pos < len(self.source):
            matched = False
            for pattern, typ in self._TOKEN_PATTERNS:
                m = re.match(pattern, self.source[pos:])
                if m:
                    if typ is not None:
                        tokens.append(Token(typ, m.group(), pos))
                    pos += len(m.group())
                    matched = True
                    break
            if not matched:
                raise KepLError("FEAT_002", f"无法识别的字符 '{self.source[pos]}'", pos)
        tokens.append(Token(TokenType.EOF, '', pos))
        return tokens


# ── 校验器 ──
class KepLValidator:
    """KEPL 公式校验，返回 AST 或错误。"""

    def __init__(self, tokens: List[Token], entity: str = 'stock'):
        self.tokens = tokens
        self.entity = entity  # stock/etf/index/global
        self.pos = 0
        self.errors: List[Dict] = []
        self.ast: Dict = {
            "type": "expression",
            "functions": [],      # 引用的函数列表
            "fields": [],          # 引用的字段列表
            "sources": [],         # 引用的数据源列表
            "external_refs": [],   # 外部引用列表
            "dependencies": [],    # 依赖的特征名列表
        }

    def current(self) -> Token:
        return self.tokens[min(self.pos, len(self.tokens)-1)]

    def eat(self, expected: TokenType = None) -> Token:
        t = self.current()
        if expected and t.typ != expected:
            raise KepLError("FEAT_002", f"期望 {expected.value}，实际 '{t.value}'", t.pos)
        self.pos += 1
        return t

    def validate(self) -> Dict:
        """执行完整校验，返回 AST + errors。"""
        try:
            self._parse_expression()
        except KepLError as e:
            self.errors.append({"code": e.code, "message": e.message, "pos": e.pos})
        return {"ok": len(self.errors) == 0, "ast": self.ast, "errors": self.errors}

    def _parse_expression(self):
        """递归下降解析表达式：term (op term)*。"""
        self._parse_term()
        while self.current().typ == TokenType.OP and self.current().value in '+-*/':
            self.eat(TokenType.OP)
            self._parse_term()

    def _parse_term(self):
        """term → function_call | field_ref | number | '(' expression ')'。"""
        t = self.current()

        if t.typ == TokenType.NUMBER:
            # 普通数字
            self.eat()
            return

        if t.typ == TokenType.OP and t.value == '(':
            self.eat()
            self._parse_expression()
            self.eat(TokenType.OP)  # expect ')'
            return

        if t.typ == TokenType.IDENT:
            ident = t.value

            # 检查下一个 token 是否为 '('（函数调用）
            if self._peek_is('('):
                self._parse_function_call(ident)
                return

            # 检查下一个 token 是否为 '.'
            if self._peek_is('.'):
                self._parse_dot_ref(ident)
                return

            # 否则为裸字段引用（如 close）
            self._parse_bare_field(ident)
            return

        raise KepLError("FEAT_002", f"意外的 '{t.value}'", t.pos)

    def _peek_is(self, *values) -> bool:
        """前瞻检查下一个非 EOF token 的值。"""
        for i in range(1, 5):  # 跳过最多5个 token
            tok = self.tokens[self.pos + i] if self.pos + i < len(self.tokens) else None
            if tok and tok.typ == TokenType.EOF:
                return False
            if tok:
                return tok.value in values
        return False

    def _peek_type(self, typ: TokenType) -> bool:
        """前瞻检查下一个 token 的类型。"""
        if self.pos + 1 >= len(self.tokens):
            return False
        return self.tokens[self.pos + 1].typ == typ

    def _parse_bare_field(self, ident: str):
        """解析裸字段引用（如 close, volume）。"""
        if self.entity == 'global':
            raise KepLError("FEAT_005",
                            "全局（Global）实体不存在当前行，公式中禁止使用裸字段，请显式指定数据源。",
                            self.current().pos)
        self.eat()
        self.ast["fields"].append({"name": ident, "type": "bare"})

    def _parse_dot_ref(self, prefix: str):
        """解析 . 引用链: prefix.field 或 prefix['key'].field。"""
        self.eat()  # prefix ident
        self.eat(TokenType.DOT)  # .

        # 可能是 prefix['key'].field
        key = None
        is_dynamic = False
        if self.current().typ == TokenType.OP and self.current().value == '[':
            self.eat()  # [
            kt = self.current()
            if kt.typ == TokenType.STRING:
                key = kt.value.strip("'\"").strip()
                self.eat()
            elif kt.typ == TokenType.IDENT:
                if kt.value == 'current':
                    is_dynamic = True
                    self.eat()
                    self.eat(TokenType.DOT)  # .
                    key = self.eat().value  # field after current.
                else:
                    raise KepLError("FEAT_002",
                                    f"动态映射中括号内请使用 current.属性名 格式，如 index[current.industry_code].close",
                                    kt.pos)
            else:
                raise KepLError("FEAT_002", "中括号内需要字符串或 current.属性", kt.pos)
            self.eat(TokenType.OP)  # ]

        if self.current().typ == TokenType.DOT:
            self.eat()  # .
            field = self.eat().value  # field name after .
        else:
            field = None

        if prefix in DATA_SOURCES:
            # 数据源引用: stock.close, index['000300'].close
            if field is None:
                # 没有具体字段，只有数据源. 或 数据源['xxx'] →
                if key:
                    self.ast["external_refs"].append({"source": prefix, "key": key, "dynamic": is_dynamic, "field": None})
                else:
                    raise KepLError("FEAT_002",
                                    f"数据源'{prefix}'是集合类型，请使用 ['代码'] 指定个体，或使用 avg({prefix}.close) 进行聚合。",
                                    self.current().pos)
            else:
                self.ast["external_refs"].append({"source": prefix, "key": key, "field": field, "dynamic": is_dynamic})
            self.ast["sources"].append(prefix)
        elif prefix == 'current':
            # current.field 引用
            self.ast["fields"].append({"name": field or "?", "type": "current"})
        else:
            # unknown prefix
            raise KepLError("FEAT_003", f"未注册的数据源或函数 '{prefix}'", self.current().pos)

    def _parse_function_call(self, name: str):
        """解析函数调用：name(arg1, arg2, ...)。"""
        self.eat()  # function name
        self.eat(TokenType.OP)  # (

        args = []
        depth = 1
        while depth > 0 and self.current().typ != TokenType.EOF:
            t = self.current()
            if t.typ == TokenType.OP:
                if t.value == '(':
                    depth += 1
                    self.eat()
                    continue
                elif t.value == ')':
                    depth -= 1
                    if depth == 0:
                        self.eat()
                        break
                    self.eat()
                    continue
                elif t.value == ',':
                    self.eat()
                    continue
            # 递归解析参数中的表达式
            self._parse_expression()

        # 校验函数调用规则
        self._validate_function_call(name, args)

        # 校验未注册函数
        if name not in ALL_FUNCTIONS:
            # 允许自定义函数（从 functions 表查）
            self.ast["functions"].append({"name": name, "type": "custom"})
        elif name in TIME_SERIES_FUNCTIONS:
            self.ast["functions"].append({"name": name, "type": "time_series"})
        elif name in CROSS_SECTIONAL_FUNCTIONS:
            self.ast["functions"].append({"name": name, "type": "cross_sectional"})

    def _validate_function_call(self, name: str, args: list):
        """校验函数参数是否符合 KEPL 规则。"""
        if name not in ALL_FUNCTIONS:
            return  # 自定义函数不做参数校验

        # 获取第一个参数的 token
        # （简化：仅检查函数名是否在白名单中，详细参数校验放到特征注册时做）


# ── API 入口 ──
def parse_kepl(formula: str, entity: str = 'stock') -> Dict:
    """解析 KEPL 公式，返回结构化结果。"""
    try:
        tokenizer = KepLTokenizer(formula)
        tokens = tokenizer.tokenize()
        validator = KepLValidator(tokens, entity)
        return validator.validate()
    except KepLError as e:
        return {"ok": False, "errors": [{"code": e.code, "message": e.message, "pos": e.pos}]}
    except Exception as e:
        return {"ok": False, "errors": [{"code": "FEAT_002", "message": str(e), "pos": 0}]}
