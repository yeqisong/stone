"""KEPL 公式解析器 — v2.2 lark 重构。

变更：
- v2.2: 用 lark PEG 解析器替换手写递归下降 → 产生可求值的表达式树 AST
- v2.1: 手写递归下降 + 扁平 AST（functions/fields 数组）
- v2.0: 初版

架构：
  KEPL 公式 → Lark LALR(1) 解析 → ParseTree → _tree_to_ast() → KepLAST 节点树
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Union
import re

import lark

# ── AST 节点数据类 ──

@dataclass
class FieldRef:
    """裸字段引用：close, volume, open 等。"""
    name: str

@dataclass
class NumLit:
    """数值字面量。"""
    value: float

@dataclass
class BoolLit:
    """布尔字面量：true / false。"""
    value: bool

@dataclass
class FuncCall:
    """函数调用：ma(close, 5), ema(rsi(close,14), 5) 等。"""
    name: str
    args: List[Expr] = field(default_factory=list)

@dataclass
class BinOp:
    """二元运算：+ - * /"""
    op: str
    left: Expr
    right: Expr


Expr = Union[FieldRef, NumLit, BoolLit, FuncCall, BinOp]


# ── KEPL 语法（EBNF，LALR(1)）──

KEPL_GRAMMAR = r"""
?start: expr

expr: term ((PLUS | MINUS) term)*
term: factor ((MUL | DIV) factor)*
?factor: NUMBER   -> num
       | BOOL     -> bool
       | CNAME    -> field
       | func_call
       | LPAR expr RPAR

func_call: CNAME LPAR arg_list? RPAR
arg_list: arg (COMMA arg)*
?arg: expr
    | MINUS NUMBER    -> neg_num

PLUS: "+"
MINUS: "-"
MUL: "*"
DIV: "/"
LPAR: "("
RPAR: ")"
COMMA: ","

%import common.CNAME
%import common.NUMBER
%import common.WS

BOOL.2: "true" | "false"

%ignore WS
"""

# ── 系统内置函数注册表 ──
# 注意：与此处注册的每个算子必须在 scripts/feature_compute.py _BUILTIN_REGISTRY
# 有实现、且在 app/api/kepl.py _OPERATOR_DOCS 有文档（/kepl/functions 端点强校验）。
TIME_SERIES_FUNCTIONS = {
    'ma', 'ema', 'rsi', 'std', 'atr', 'pct_change',
    'dif', 'dea', 'macd_hist', 'boll_upper', 'boll_mid', 'boll_lower',
    # Alpha158 移植算子（design/05 M5）：滚动窗口时序
    'ref', 'hhv', 'llv', 'ts_quantile', 'ts_rank', 'slope', 'rsquare', 'resi',
    'imax', 'imin', 'ts_corr', 'max2', 'min2',
}

CROSS_SECTIONAL_FUNCTIONS = {
    'avg', 'sum', 'max', 'min', 'rank', 'quantile', 'zscore', 'neut'
}

ALL_FUNCTIONS = TIME_SERIES_FUNCTIONS | CROSS_SECTIONAL_FUNCTIONS


# ── ParseTree → KepLAST (manual walker, avoids Transformer complexity) ──

def _binop_from_children(node) -> Expr:
    """从 expr/term 节点的子节点构建左结合 BinOp 链。

    子节点模式: [operand, op_token, operand, op_token, operand, ...]
    op_token 类型为 PLUS/MINUS/MUL/DIV。
    """
    # 过滤括号和逗号（它们可能因 ?factor inline 出现在子节点中）
    meaningful = []
    for child in node.children:
        if isinstance(child, lark.Token):
            t = child.type
            if t in ('PLUS', 'MINUS', 'MUL', 'DIV'):
                meaningful.append(child)
            # 跳过 LPAR, RPAR, COMMA
        else:
            meaningful.append(child)

    operands = []
    operators = []
    for item in meaningful:
        if isinstance(item, lark.Token):
            operators.append(item.value)
        else:
            operands.append(_tree_to_ast(item))

    if not operators:
        return operands[0] if operands else None
    result = operands[0]
    for i, op in enumerate(operators):
        result = BinOp(op=op, left=result, right=operands[i + 1])
    return result


def _tree_to_ast(node) -> Expr:
    """将 lark ParseTree 递归转换为 KepLAST 表达式树。

    处理：
    - expr/term: 收集子节点，检测运算符构建 BinOp
    - func_call: 提取函数名和参数列表
    - num/field: 叶子节点
    """
    if isinstance(node, lark.Token):
        if node.type == 'NUMBER':
            return NumLit(float(node.value))
        elif node.type == 'CNAME':
            return FieldRef(str(node.value))
        # literal operators like '+', '-', '*', '/', ',', '(', ')'
        return str(node.value)

    # Tree node
    data = node.data

    if data == 'num':
        return _tree_to_ast(node.children[0])
    elif data == 'bool':
        child = node.children[0]
        if isinstance(child, lark.Token):
            return BoolLit(child.value == 'true')
        return BoolLit(False)
    elif data == 'field':
        # CNAME → FieldRef
        child = node.children[0]
        if isinstance(child, lark.Token):
            return FieldRef(str(child.value))
        return _tree_to_ast(child)
    elif data == 'func_call':
        # Children: [CNAME, (LPAR), arg_list?, (RPAR)]
        # LPAR/RPAR may appear due to ?factor inline
        name = None
        args = []
        for child in node.children:
            if isinstance(child, lark.Token):
                if child.type == 'CNAME' and name is None:
                    name = str(child.value)
                # skip LPAR, RPAR, COMMA
            else:
                if child.data == 'arg_list':
                    args = _tree_to_ast(child)  # returns list
                elif name is None:
                    # might be nested expression as function name (unusual)
                    pass
        return FuncCall(name=name or '?', args=args if isinstance(args, list) else [])
    elif data == 'arg_list':
        args = []
        for child in node.children:
            if isinstance(child, lark.Token):
                continue  # skip COMMA
            args.append(_tree_to_ast(child))
        return args
    elif data == 'neg_num':
        # 参数位负数字面量：ref(close, -1)（负偏移=未来值，显式语义）
        num = node.children[-1]
        return NumLit(-float(num.value))
    elif data == 'factor':
        # factor may appear as explicit node when using named terminals (even with ? prefix).
        # Skip LPAR/RPAR wrapper and return the expression inside.
        for child in node.children:
            if isinstance(child, lark.Token):
                if child.type in ('LPAR', 'RPAR'):
                    continue
                # field or num
                return _tree_to_ast(child)
            else:
                return _tree_to_ast(child)
        return None
    elif data == 'expr':
        return _binop_from_children(node)
    elif data == 'term':
        return _binop_from_children(node)
    elif data == 'start':
        return _tree_to_ast(node.children[0]) if node.children else None

    return None


# ── 辅助：表达式树 → 旧版扁平 AST（向后兼容）──

def _tree_to_flat_ast(tree: Expr) -> dict:
    functions = []
    fields = []
    seen_fn = set()
    seen_field = set()

    def walk(node):
        if isinstance(node, FieldRef):
            if node.name not in seen_field:
                fields.append({"name": node.name, "type": "bare"})
                seen_field.add(node.name)
        elif isinstance(node, NumLit):
            pass
        elif isinstance(node, BoolLit):
            pass
        elif isinstance(node, FuncCall):
            fn_type = "time_series" if node.name in TIME_SERIES_FUNCTIONS else (
                "cross_sectional" if node.name in CROSS_SECTIONAL_FUNCTIONS else "custom"
            )
            if node.name not in seen_fn:
                functions.append({"name": node.name, "type": fn_type})
                seen_fn.add(node.name)
            for arg in node.args:
                walk(arg)
        elif isinstance(node, BinOp):
            walk(node.left)
            walk(node.right)

    walk(tree)
    return {
        "type": "expression",
        "functions": functions,
        "fields": fields,
        "sources": [],
        "external_refs": [],
        "dependencies": [],
    }


# ── Lark 实例（模块级单例，首次 ~50ms）──

_lark_parser: Optional[lark.Lark] = None


def _get_parser() -> lark.Lark:
    global _lark_parser
    if _lark_parser is None:
        _lark_parser = lark.Lark(KEPL_GRAMMAR, parser="lalr")
    return _lark_parser


# ── API 入口 ──

def parse_kepl(formula: str, entity: str = 'stock') -> Dict:
    """解析 KEPL 公式，返回结构化结果。

    Returns:
        {"ok": True/False, "ast": {...flat...}, "tree": KepLAST, "errors": [...]}
    """
    if not formula or not formula.strip():
        return {"ok": False, "errors": [{"code": "FEAT_001", "message": "公式为空", "pos": 0}]}

    try:
        parser = _get_parser()
        parse_tree = parser.parse(formula.strip())
        tree = _tree_to_ast(parse_tree)

        if tree is None:
            return {"ok": False, "errors": [{"code": "FEAT_002", "message": "无法解析公式", "pos": 0}]}

        flat_ast = _tree_to_flat_ast(tree)

        return {
            "ok": True,
            "ast": flat_ast,
            "tree": tree,
            "errors": [],
        }
    except lark.UnexpectedInput as e:
        return {"ok": False, "errors": [{"code": "FEAT_002", "message": f"语法错误: {str(e)[:120]}", "pos": getattr(e, 'pos_in_stream', 0)}]}
    except lark.UnexpectedToken as e:
        return {"ok": False, "errors": [{"code": "FEAT_002", "message": f"语法错误: 意外的符号 '{e.token}'", "pos": getattr(e, 'pos_in_stream', 0)}]}
    except Exception as e:
        return {"ok": False, "errors": [{"code": "FEAT_002", "message": str(e)[:200], "pos": 0}]}


def parse_to_tree(formula: str) -> Optional[Expr]:
    """解析 KEPL 公式，仅返回可求值表达式树。"""
    r = parse_kepl(formula)
    if r.get("ok") and r.get("tree"):
        return r["tree"]
    return None
