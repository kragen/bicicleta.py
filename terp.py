"""
Interpreter for a dialect of Bicicleta. I changed the 'if' syntax and
left out some things.
"""

from __future__ import division
import sys; sys.setrecursionlimit(7500)
from functools import reduce

from peglet import OneResult, Parser, hug


# Top level

def run(program):
    if isinstance(program, str):
        program = parse(program)
    return program.eval(initial_env).show()


# Objects

class BicicletaObject(dict):    # ('bob' for short)
    parent = None
    primval = None
    def __missing__(self, slot):
        self[slot] = value = self.call(slot, self)
        return value
    def list_slots(self):
        return frozenset(self.methods.keys())

class Prim(BicicletaObject):
    def __init__(self, primval, methods):
        self.primval = primval
        self.methods = methods
    def call(self, slot, recipient):
        return self.methods[slot](recipient)
    def show(self, prim=repr):
        return prim(self.primval)

class PrimOp1(BicicletaObject):
    def __init__(self, arg0, fn):
        self.arg0 = arg0
        self.fn = fn
    def call(self, slot, recipient):
        if slot != '()': raise KeyError(slot)
        return self.fn(self.arg0.primval, recipient['arg1'])
    def list_slots(self):
        return frozenset(['()'])
    def show(self, prim=repr):
        return 'PrimOp1(%r, %r)' % (self.arg0.show(prim), self.fn)

class Extension(BicicletaObject):
    def __init__(self, parent, methods):
        self.parent = parent
        self.methods = methods
    def call(self, slot, recipient):
        try:
            method = self.methods[slot]
        except KeyError:
            return self.parent.call(slot, recipient)
        else:
            return method(recipient)
    def list_slots(self):
        return frozenset(self.methods.keys()) | self.parent.list_slots()
    def show(self, prim=repr):
        return '{%s}' % ', '.join(sorted(self.list_slots()))


# Primitive objects

class Number(Prim):
    def __init__(self, n):
        self.primval = n
    methods = {
        '+':  lambda me: PrimOp1(me, num_add),
        '-':  lambda me: PrimOp1(me, num_sub),
        '*':  lambda me: PrimOp1(me, num_mul),
        '/':  lambda me: PrimOp1(me, num_div),
        '**': lambda me: PrimOp1(me, num_pow),
        '==': lambda me: PrimOp1(me, prim_eq),
        '<':  lambda me: PrimOp1(me, prim_lt),
    }

def num_add(v1, v2): return Number(v1 + v2.primval)
def num_sub(v1, v2): return Number(v1 - v2.primval)
def num_mul(v1, v2): return Number(v1 * v2.primval)
def num_div(v1, v2): return Number(v1 / v2.primval)
def num_pow(v1, v2): return Number(v1 ** v2.primval)

def prim_eq(v1, v2): return Claim(v1 == v2.primval)
def prim_lt(v1, v2): return Claim(v1 < v2.primval)
# XXX should < on different types be an error?

class String(Prim):
    def __init__(self, s):
        self.primval = s
    methods = {
        '==': lambda me: PrimOp1(me, prim_eq),
        '<':  lambda me: PrimOp1(me, prim_lt),
        '%':  lambda me: PrimOp1(me, string_substitute),
    }

def string_substitute(template, bob):
    import re
    return String(re.sub(r'{(.*?)}', lambda m: bob[m.group(1)].show(str),
                         template))

def Claim(value):
    assert isinstance(value, bool)
    return true_claim if value else false_claim

true_claim  = Prim(None, {'if': lambda me: pick_so})
false_claim = Prim(None, {'if': lambda me: pick_not})
pick_so     = Prim(None, {'()': lambda doing: doing['so']})
pick_not    = Prim(None, {'()': lambda doing: doing['not']})


# Evaluation

class VarRef(object):
    def __init__(self, name):
        self.name = name
    def __repr__(self):
        return self.name
    def eval(self, env):
        return env[self.name]

class Literal(object):
    def __init__(self, value):
        self.value = value
    def __repr__(self):
        return self.value.show()
    def eval(self, env):
        return self.value

class Call(object):
    def __init__(self, receiver, slot):
        self.receiver = receiver
        self.slot = slot
    def __repr__(self):
        return '%s.%s' % (self.receiver, self.slot)
    def eval(self, env):
        return self.receiver.eval(env)[self.slot]

def make_extend(base, name, bindings):
    extend = SelflessExtend if name is None else Extend
    # (We needn't special-case this; it's an optimization.)
    return extend(base, name, bindings)

class Extend(object):
    def __init__(self, base, name, bindings):
        self.base = base
        self.name = name
        self.bindings = bindings
    def __repr__(self):
        return '%s{%s%s}' % (self.base,
                             self.name + ': ' if self.name else '',
                               ', '.join('%s=%s' % binding
                                         for binding in self.bindings))
    def eval(self, env):
        return Extension(self.base.eval(env),
                         {slot: make_slot_thunk(self.name, expr, env)
                          for slot, expr in self.bindings})

class SelflessExtend(Extend):
    def eval(self, env):
        return Extension(self.base.eval(env),
                         {slot: make_selfless_slot_thunk(expr, env)
                          for slot, expr in self.bindings})

def make_selfless_slot_thunk(expr, env):
    return lambda _: expr.eval(env)

def make_slot_thunk(name, expr, env):
    def thunk(rcvr):
        new_env = dict(env)
        new_env[name] = rcvr
        return expr.eval(new_env)
    return thunk

initial_env = {'<>': Prim(None, {})}


# Parser

program_grammar = r"""
program     = expr _ !.
expr        = factor infixes                attach_all
factor      = primary affixes               attach_all

primary     = name                          VarRef
            | _ (\d*\.\d+)                  float Number Literal
            | _ (\d+)                       int   Number Literal
            | _ "([^"\\]*)"                       String Literal
            | _ \( _ expr \)
            | empty derive                  attach

affixes     = affix affixes | 
affix       = _ [.] name                    defer_dot
            | derive
            | _ \( bindings _ \)            defer_funcall
            | _ \[ bindings _ \]            defer_squarecall

derive      = _ { name _ : bindings _ }     defer_derive
            | _ { nameless bindings _ }     defer_derive
bindings    = binds                         name_positions
binds       = binding newline binds
            | binding _ , binds
            | binding
            | 
binding     = name _ [=] expr               hug
            | positional expr               hug

infixes     = infix infixes | 
infix       = infix_op factor               defer_infix
infix_op    = _ !lone_eq opchars
opchars     = ([-~`!@$%^&*+<>?/|\\=]+)
lone_eq     = [=] !opchars

name        = _ ([A-Za-z_][A-Za-z_0-9]*)
            | _ '([^'\\]*)'

newline     = blanks \n
blanks      = blank blanks | 
blank       = !\n (?:\s|#.*)

_           = (?:\s|#.*)*
"""
# TODO: support backslashes in '' and ""
# TODO: foo(name: x=y) [if actually wanted]

def empty(): return VarRef('<>')
def nameless(): return None
def positional(): return None

def name_positions(*bindings):
    return tuple((('arg%d' % i if slot is None else slot), expr)
                 for i, (slot, expr) in enumerate(bindings, 1))

def attach_all(expr, *affixes):    return reduce(attach, affixes, expr)
def attach(expr, affix):           return affix[0](expr, *affix[1:])

def defer_dot(name):               return Call, name
def defer_derive(name, bindings):  return make_extend, name, bindings
def defer_funcall(bindings):       return mk_funcall, '()', bindings
def defer_squarecall(bindings):    return mk_funcall, '[]', bindings
def defer_infix(operator, expr):   return mk_infix, operator, expr

def mk_funcall(expr, slot, bindings):
    "  foo(x=y) ==> foo{x=y}.'()'  "
    return Call(make_extend(expr, nameless(), bindings), slot)

def mk_infix(left, operator, right):
    "   x + y ==> x.'+'(_=y)  "
    return mk_funcall(Call(left, operator), '()', (('arg1', right),))

parse = OneResult(Parser(program_grammar, int=int, float=float, **globals()))


# Crude tests and benchmarks

## parse("x ++ y{a=b} <*> z.foo")
#. x.++{arg1=y{a=b}}.().<*>{arg1=z.foo}.()

## wtf = parse("{x=42, y=55}.x")
## wtf
#. <>{x=42, y=55}.x
## run(wtf)
#. '42'

## run("{y=42, x=55, z=137}.x")
#. '55'

## parse("137")
#. 137
## parse("137[yo=dude]")
#. 137{yo=dude}.[]

## adding = parse("137.'+' {arg1=1}.'()'")
## adding
#. 137.+{arg1=1}.()
## run(adding)
#. '138'

## run("137.5 - 2 - 1")
#. '134.5'

## run("(136 < 137).if(so=1, not=2)")
#. '1'
## run("(137 < 137).if(so=1, not=2)")
#. '2'
## run("137.'<' {arg1=137}.'()'.if(so=1, not=2)")
#. '2'

## cmping = parse("(137 == 1).if(so=42, not=168)")
## repr(cmping) == repr(parse("137.'=='{arg1=1}.'()'.if{so=42, not=168}.'()'"))
#. True
## run(cmping)
#. '168'

## run('"howdy"')
#. "'howdy'"
## run('("hello" == "aloha").if(so=42, not=168)')
#. '168'
## run('("hello" == "hello").if(so=42, not=168)')
#. '42'

test_extend = parse("""
    {main:
     three = {me: x = 3, xx = me.x + me.x},
     four = main.three{x=4},
     result = main.three.xx + main.four.xx
    }.result
""")
## run(test_extend)
#. '14'

## run('"hey {x} and {why}" % {x=84/2, why=136+1}')
#. "'hey 42.0 and 137'"
## run("5**3")
#. '125'

def make_fac(n):
    fac = parse("""
{env: 
 fac = {fac:   # fac for factorial
        '()' = (fac.n == 0).if(so  = 1,
                               not = fac.n * env.fac(n = fac.n-1))}
}.fac(n=%d)""" % n)
    return fac

fac = make_fac(4)
## fac
#. <>{env: fac=<>{fac: ()=fac.n.=={arg1=0}.().if{so=1, not=fac.n.*{arg1=env.fac{n=fac.n.-{arg1=1}.()}.()}.()}.()}}.fac{n=4}.()
## run(fac)
#. '24'

def make_fib(n):
    fib = parse("""
{env:
 fib = {fib:
        '()' = (fib.n < 2).if(so = 1,
                              not = env.fib(n=fib.n-1) + env.fib(n=fib.n-2))}
}.fib(n=%d)
    """ % n)
    return fib

## run(make_fib(5))
#. '8'

def make_tak():
    program = parse("""
{env:
 tak = {tak: 
          '()' = (tak.y < (tak.x)).if(
              so = env.tak(x=env.tak(x=tak.x-1, y=tak.y, z=tak.z),
                           y=env.tak(x=tak.y-1, y=tak.z, z=tak.x),
                           z=env.tak(x=tak.z-1, y=tak.x, z=tak.y)),
              not = tak.z)
         }
    }.tak(x=18, y=12, z=6)""")
    return program

def make_tarai():
    # TARAI is like TAK, but it's much faster with lazy evaluation.
    # It was Takeuchi's original function.
    program = parse("""
{env:
 tarai = {tarai: 
          '()' = (tarai.y < (tarai.x)).if(
              so = env.tarai(x=env.tarai(x=tarai.x-1, y=tarai.y, z=tarai.z),
                             y=env.tarai(x=tarai.y-1, y=tarai.z, z=tarai.x),
                             z=env.tarai(x=tarai.z-1, y=tarai.x, z=tarai.y)),
              not = tarai.y)
         }
    }.tarai(x=18, y=12, z=6)""")
    return program

itersum3 = parse("""
{env:
 outer = {outer: 
   i=0, sum=0,
   '()' = (outer.i == 0).if(
       so = outer.sum,
       not = env.outer(i=outer.i-1, sum=outer.sum + env.mid(i=outer.i)))},
 mid = {mid: 
   i=0, sum=0,
   '()' = (mid.i == 0).if(
       so = mid.sum,
       not = env.mid(i=mid.i-1, sum=mid.sum + env.inner(i=mid.i)))},
 inner = {inner:
   i=0, sum=0,
   '()' = (inner.i == 0).if(
       so = inner.sum,
       not = env.inner(i=inner.i-1, sum=inner.sum + inner.i))},
 main = env.outer(i = 40)
}.main
""")

def timed(f):
    import time
    start = time.clock()
    result = f()
    return time.clock() - start, result

def bench2():
    tarai = make_tarai()
    print(timed(lambda: run(tarai)))

def bench3():
    tak = make_tak()
    print(timed(lambda: run(tak)))

if __name__ == '__main__':
    bench2()
    print(timed(lambda: run(itersum3)))
    fib = make_fib(20)
    print(timed(lambda: run(fib)))
    bench3()
