import sys

from rpython.annotator import model as annmodel
from rpython.flowspace.operation import op_appendices
from rpython.rlib import objectmodel, jit
from rpython.rlib.rarithmetic import intmask, longlongmask, r_int, r_longlong
from rpython.rlib.rarithmetic import r_uint, r_ulonglong, r_longlonglong
from rpython.rtyper.error import TyperError
from rpython.rtyper.lltypesystem.lltype import (Signed, Unsigned, Bool, Float,
    Char, UniChar, UnsignedLongLong, SignedLongLong, build_number, Number,
    cast_primitive, typeOf, SignedLongLongLong)
from rpython.rtyper.rfloat import FloatRepr
from rpython.rtyper.rmodel import inputconst, log
from rpython.tool.pairtype import pairtype
from rpython.rtyper.lltypesystem.lloperation import llop


class IntegerRepr(FloatRepr):
    def __init__(self, lowleveltype, opprefix):
        self.lowleveltype = lowleveltype
        self._opprefix = opprefix
        self.as_int = self

    @property
    def opprefix(self):
        if self._opprefix is None:
            raise TyperError("arithmetic not supported on %r, its size is too small" %
                             self.lowleveltype)
        return self._opprefix

    def convert_const(self, value):
        if isinstance(value, objectmodel.Symbolic):
            return value
        T = typeOf(value)
        if isinstance(T, Number) or T is Bool:
            return cast_primitive(self.lowleveltype, value)
        raise TyperError("not an integer: %r" % (value,))

    def get_ll_eq_function(self):
        if getattr(self, '_opprefix', '?') is None:
            return ll_eq_shortint
        return None

    def get_ll_ge_function(self):
        return None
    get_ll_gt_function = get_ll_ge_function
    get_ll_lt_function = get_ll_ge_function
    get_ll_le_function = get_ll_ge_function

    def get_ll_hash_function(self):
        if (sys.maxint == 2147483647 and
            self.lowleveltype in (SignedLongLong, UnsignedLongLong)):
            return ll_hash_long_long
        return ll_hash_int

    get_ll_fasthash_function = get_ll_hash_function

    def get_ll_dummyval_obj(self, rtyper, s_value):
        # if >= 0, then all negative values are special
        if s_value.nonneg and self.lowleveltype is Signed:
            return signed_repr    # whose ll_dummy_value is -1
        else:
            return None

    ll_dummy_value = -1

    def rtype_chr(_, hop):
        vlist = hop.inputargs(Signed)
        if hop.has_implicit_exception(ValueError):
            hop.exception_is_here()
            hop.gendirectcall(ll_check_chr, vlist[0])
        else:
            hop.exception_cannot_occur()
        return hop.genop('cast_int_to_char', vlist, resulttype=Char)

    def rtype_unichr(_, hop):
        vlist = hop.inputargs(Signed)
        if hop.has_implicit_exception(ValueError):
            hop.exception_is_here()
            hop.gendirectcall(ll_check_unichr, vlist[0])
        else:
            hop.exception_cannot_occur()
        return hop.genop('cast_int_to_unichar', vlist, resulttype=UniChar)

    def rtype_bool(self, hop):
        assert self is self.as_int   # rtype_is_true() is overridden in BoolRepr
        vlist = hop.inputargs(self)
        return hop.genop(self.opprefix + 'is_true', vlist, resulttype=Bool)

    #Unary arithmetic operations

    def rtype_abs(self, hop):
        self = self.as_int
        vlist = hop.inputargs(self)
        if hop.s_result.unsigned:
            return vlist[0]
        else:
            return hop.genop(self.opprefix + 'abs', vlist, resulttype=self)

    def rtype_abs_ovf(self, hop):
        self = self.as_int
        if hop.s_result.unsigned:
            raise TyperError("forbidden uint_abs_ovf")
        else:
            return _rtype_call_helper(hop, 'abs_ovf')

    def rtype_invert(self, hop):
        self = self.as_int
        vlist = hop.inputargs(self)
        return hop.genop(self.opprefix + 'invert', vlist, resulttype=self)

    def rtype_neg(self, hop):
        self = self.as_int
        vlist = hop.inputargs(self)
        if hop.s_result.unsigned:
            # implement '-r_uint(x)' with unsigned subtraction '0 - x'
            zero = self.lowleveltype._defl()
            vlist.insert(0, hop.inputconst(self.lowleveltype, zero))
            return hop.genop(self.opprefix + 'sub', vlist, resulttype=self)
        else:
            return hop.genop(self.opprefix + 'neg', vlist, resulttype=self)

    def rtype_neg_ovf(self, hop):
        self = self.as_int
        if hop.s_result.unsigned:
            # this is supported (and turns into just 0-x) for rbigint.py
            hop.exception_cannot_occur()
            return self.rtype_neg(hop)
        else:
            return _rtype_call_helper(hop, 'neg_ovf')

    def rtype_pos(self, hop):
        self = self.as_int
        vlist = hop.inputargs(self)
        return vlist[0]

    def rtype_int(self, hop):
        if self.lowleveltype in (Unsigned, UnsignedLongLong):
            raise TyperError("use intmask() instead of int(r_uint(...))")
        vlist = hop.inputargs(Signed)
        hop.exception_cannot_occur()
        return vlist[0]

    def rtype_float(_, hop):
        vlist = hop.inputargs(Float)
        hop.exception_cannot_occur()
        return vlist[0]

    @jit.elidable
    def ll_str(self, i):
        from rpython.rtyper.lltypesystem.ll_str import ll_int2dec
        return ll_int2dec(i)

    def rtype_hex(self, hop):
        from rpython.rtyper.lltypesystem.ll_str import ll_int2hex
        self = self.as_int
        varg = hop.inputarg(self, 0)
        true = inputconst(Bool, True)
        return hop.gendirectcall(ll_int2hex, varg, true)

    def rtype_oct(self, hop):
        from rpython.rtyper.lltypesystem.ll_str import ll_int2oct
        self = self.as_int
        varg = hop.inputarg(self, 0)
        true = inputconst(Bool, True)
        return hop.gendirectcall(ll_int2oct, varg, true)


_integer_reprs = {}
def getintegerrepr(lltype, prefix=None):
    try:
        return _integer_reprs[lltype]
    except KeyError:
        pass
    repr = _integer_reprs[lltype] = IntegerRepr(lltype, prefix)
    return repr

class __extend__(annmodel.SomeInteger):
    def rtyper_makerepr(self, rtyper):
        lltype = build_number(None, self.knowntype)
        return getintegerrepr(lltype)

    def rtyper_makekey(self):
        return self.__class__, self.knowntype

signed_repr = getintegerrepr(Signed, 'int_')
signedlonglong_repr = getintegerrepr(SignedLongLong, 'llong_')
signedlonglonglong_repr = getintegerrepr(SignedLongLongLong, 'lllong_')
unsigned_repr = getintegerrepr(Unsigned, 'uint_')
unsignedlonglong_repr = getintegerrepr(UnsignedLongLong, 'ullong_')

class __extend__(pairtype(IntegerRepr, IntegerRepr)):

    def convert_from_to((r_from, r_to), v, llops):
        if r_from.lowleveltype == Signed and r_to.lowleveltype == Unsigned:
            log.debug('explicit cast_int_to_uint')
            return llops.genop('cast_int_to_uint', [v], resulttype=Unsigned)
        if r_from.lowleveltype == Unsigned and r_to.lowleveltype == Signed:
            log.debug('explicit cast_uint_to_int')
            return llops.genop('cast_uint_to_int', [v], resulttype=Signed)
        if r_from.lowleveltype == Signed and r_to.lowleveltype == SignedLongLong:
            return llops.genop('cast_int_to_longlong', [v], resulttype=SignedLongLong)
        if r_from.lowleveltype == SignedLongLong and r_to.lowleveltype == Signed:
            return llops.genop('truncate_longlong_to_int', [v], resulttype=Signed)
        return llops.genop('cast_primitive', [v], resulttype=r_to.lowleveltype)

    #arithmetic

    def rtype_add(_, hop):
        return _rtype_template(hop, 'add')
    rtype_inplace_add = rtype_add

    def rtype_add_ovf(_, hop):
        func = 'add_ovf'
        if hop.r_result.opprefix == 'int_':
            if hop.args_s[1].nonneg:
                func = 'add_nonneg_ovf'
            elif hop.args_s[0].nonneg:
                hop = hop.copy()
                hop.swap_fst_snd_args()
                func = 'add_nonneg_ovf'
        return _rtype_call_helper(hop, func)

    def rtype_sub(_, hop):
        return _rtype_template(hop, 'sub')
    rtype_inplace_sub = rtype_sub

    def rtype_sub_ovf(_, hop):
        return _rtype_call_helper(hop, 'sub_ovf')

    def rtype_mul(_, hop):
        return _rtype_template(hop, 'mul')
    rtype_inplace_mul = rtype_mul

    def rtype_mul_ovf(_, hop):
        return _rtype_call_helper(hop, 'mul_ovf')

    def rtype_floordiv(_, hop):
        return _rtype_call_helper(hop, 'floordiv', [ZeroDivisionError])
    rtype_inplace_floordiv = rtype_floordiv

    def rtype_floordiv_ovf(_, hop):
        return _rtype_call_helper(hop, 'floordiv_ovf', [ZeroDivisionError])

    # turn 'div' on integers into 'floordiv'
    rtype_div         = rtype_floordiv
    rtype_inplace_div = rtype_inplace_floordiv
    rtype_div_ovf     = rtype_floordiv_ovf

    # 'def rtype_truediv' is delegated to the superclass FloatRepr

    def rtype_mod(_, hop):
        return _rtype_call_helper(hop, 'mod', [ZeroDivisionError])
    rtype_inplace_mod = rtype_mod

    def rtype_mod_ovf(_, hop):
        return _rtype_call_helper(hop, 'mod_ovf', [ZeroDivisionError])

    def rtype_xor(_, hop):
        return _rtype_template(hop, 'xor')
    rtype_inplace_xor = rtype_xor

    def rtype_and_(_, hop):
        return _rtype_template(hop, 'and')
    rtype_inplace_and = rtype_and_

    def rtype_or_(_, hop):
        return _rtype_template(hop, 'or')
    rtype_inplace_or = rtype_or_

    def rtype_lshift(_, hop):
        return _rtype_template(hop, 'lshift')
    rtype_inplace_lshift = rtype_lshift

    def rtype_lshift_ovf(_, hop):
        return _rtype_call_helper(hop, 'lshift_ovf')

    def rtype_rshift(_, hop):
        return _rtype_template(hop, 'rshift')
    rtype_inplace_rshift = rtype_rshift

    #comparisons: eq is_ ne lt le gt ge

    def rtype_eq(_, hop):
        return _rtype_compare_template(hop, 'eq')

    rtype_is_ = rtype_eq

    def rtype_ne(_, hop):
        return _rtype_compare_template(hop, 'ne')

    def rtype_lt(_, hop):
        return _rtype_compare_template(hop, 'lt')

    def rtype_le(_, hop):
        return _rtype_compare_template(hop, 'le')

    def rtype_gt(_, hop):
        return _rtype_compare_template(hop, 'gt')

    def rtype_ge(_, hop):
        return _rtype_compare_template(hop, 'ge')

#Helper functions

def _rtype_template(hop, func):
    """Write a simple operation implementing the given 'func'.
    It must be an operation that cannot raise.
    """
    if '_ovf' in func or (func.startswith(('mod', 'floordiv'))
                              and not hop.s_result.unsigned):
        raise TyperError("%r should not be used here any more" % (func,))

    r_result = hop.r_result
    if r_result.lowleveltype == Bool:
        repr = signed_repr
    else:
        repr = r_result
    if func.startswith(('lshift', 'rshift')):
        repr2 = signed_repr
    else:
        repr2 = repr
    vlist = hop.inputargs(repr, repr2)
    hop.exception_cannot_occur()

    prefix = repr.opprefix
    v_res = hop.genop(prefix+func, vlist, resulttype=repr)
    v_res = hop.llops.convertvar(v_res, repr, r_result)
    return v_res


def _rtype_call_helper(hop, func, implicit_excs=[]):
    """Write a call to a helper implementing the given 'func'.
    It can raise OverflowError if 'func' ends with '_ovf'.
    Other possible exceptions can be specified in 'implicit_excs'.
    """
    any_implicit_exception = False
    if func.endswith('_ovf'):
        if hop.s_result.unsigned:
            raise TyperError("forbidden unsigned " + func)
        else:
            hop.has_implicit_exception(OverflowError)
            any_implicit_exception = True

    for implicit_exc in implicit_excs:
        if hop.has_implicit_exception(implicit_exc):
            appendix = op_appendices[implicit_exc]
            func += '_' + appendix
            any_implicit_exception = True

    if not any_implicit_exception:
        if not func.startswith(('mod', 'floordiv')):
            return _rtype_template(hop, func)
        if hop.s_result.unsigned:
            return _rtype_template(hop, func)

    repr = hop.r_result
    assert repr.lowleveltype != Bool
    if func in ('abs_ovf', 'neg_ovf'):
        vlist = hop.inputargs(repr)
    else:
        if func.startswith(('lshift', 'rshift')):
            vlist = hop.inputargs(repr, signed_repr)
        else:
            vlist = hop.inputargs(repr, repr)
    if any_implicit_exception:
        hop.exception_is_here()
    else:
        hop.exception_cannot_occur()

    llfunc = globals()['ll_' + repr.opprefix + func]
    v_result = hop.gendirectcall(llfunc, *vlist)
    assert v_result.concretetype == repr.lowleveltype
    return v_result


INT_BITS_1 = r_int.BITS - 1
LLONG_BITS_1 = r_longlong.BITS - 1
LLLONG_BITS_1 = r_longlonglong.BITS - 1
INT_MIN = int(-(1 << INT_BITS_1))


# ---------- floordiv ----------

def ll_int_floordiv(x, y):
    # Python, and RPython, assume that integer division truncates
    # towards -infinity.  However, in C, integer division truncates
    # towards 0.  So assuming that, we need to apply a correction
    # in the right cases.
    r = llop.int_floordiv(Signed, x, y)            # <= truncates like in C
    p = r * y
    if y < 0: u = p - x
    else:     u = x - p
    return r + (u >> INT_BITS_1)

def ll_int_floordiv_zer(x, y):
    if y == 0:
        raise ZeroDivisionError("integer division")
    return ll_int_floordiv(x, y)

def ll_uint_floordiv_zer(x, y):
    if y == 0:
        raise ZeroDivisionError("unsigned integer division")
    return llop.uint_floordiv(Unsigned, x, y)

def ll_int_floordiv_ovf(x, y):
    # JIT: intentionally not short-circuited to produce only one guard
    # and to remove the check fully if one of the arguments is known
    if (x == -sys.maxint - 1) & (y == -1):
        raise OverflowError("integer division")
    return ll_int_floordiv(x, y)

def ll_int_floordiv_ovf_zer(x, y):
    if y == 0:
        raise ZeroDivisionError("integer division")
    return ll_int_floordiv_ovf(x, y)

def ll_llong_floordiv(x, y):
    r = llop.llong_floordiv(SignedLongLong, x, y)  # <= truncates like in C
    p = r * y
    if y < 0: u = p - x
    else:     u = x - p
    return r + (u >> LLONG_BITS_1)

def ll_llong_floordiv_zer(x, y):
    if y == 0:
        raise ZeroDivisionError("longlong division")
    return ll_llong_floordiv(x, y)

def ll_ullong_floordiv_zer(x, y):
    if y == 0:
        raise ZeroDivisionError("unsigned longlong division")
    return llop.ullong_floordiv(UnsignedLongLong, x, y)

def ll_lllong_floordiv(x, y):
    r = llop.lllong_floordiv(SignedLongLongLong, x, y) # <= truncates like in C
    p = r * y
    if y < 0: u = p - x
    else:     u = x - p
    return r + (u >> LLLONG_BITS_1)

def ll_lllong_floordiv_zer(x, y):
    if y == 0:
        raise ZeroDivisionError("longlonglong division")
    return ll_lllong_floordiv(x, y)


# ---------- mod ----------

def ll_int_mod(x, y):
    r = llop.int_mod(Signed, x, y)                 # <= truncates like in C
    if y < 0: u = -r
    else:     u = r
    return r + (y & (u >> INT_BITS_1))

def ll_int_mod_zer(x, y):
    if y == 0:
        raise ZeroDivisionError
    return ll_int_mod(x, y)

def ll_uint_mod_zer(x, y):
    if y == 0:
        raise ZeroDivisionError
    return llop.uint_mod(Unsigned, x, y)

def ll_int_mod_ovf(x, y):
    # see comment in ll_int_floordiv_ovf
    if (x == -sys.maxint - 1) & (y == -1):
        raise OverflowError
    return ll_int_mod(x, y)

def ll_int_mod_ovf_zer(x, y):
    if y == 0:
        raise ZeroDivisionError
    return ll_int_mod_ovf(x, y)

def ll_llong_mod(x, y):
    r = llop.llong_mod(SignedLongLong, x, y)       # <= truncates like in C
    if y < 0: u = -r
    else:     u = r
    return r + (y & (u >> LLONG_BITS_1))

def ll_llong_mod_zer(x, y):
    if y == 0:
        raise ZeroDivisionError
    return ll_llong_mod(x, y)

def ll_ullong_mod_zer(x, y):
    if y == 0:
        raise ZeroDivisionError
    return llop.ullong_mod(UnsignedLongLong, x, y)

def ll_lllong_mod(x, y):
    r = llop.lllong_mod(SignedLongLongLong, x, y)  # <= truncates like in C
    if y < 0: u = -r
    else:     u = r
    return r + (y & (u >> LLLONG_BITS_1))

def ll_lllong_mod_zer(x, y):
    if y == 0:
        raise ZeroDivisionError
    return ll_lllong_mod(x, y)


# ---------- add, sub, mul ----------

@jit.oopspec("int.add_ovf(x, y)")
def ll_int_add_ovf(x, y):
    r = intmask(r_uint(x) + r_uint(y))
    if r^x < 0 and r^y < 0:
        raise OverflowError("integer addition")
    return r

@jit.oopspec("int.add_ovf(x, y)")
def ll_int_add_nonneg_ovf(x, y):     # y can be assumed >= 0
    r = intmask(r_uint(x) + r_uint(y))
    if r < x:
        raise OverflowError("integer addition")
    return r

@jit.oopspec("int.sub_ovf(x, y)")
def ll_int_sub_ovf(x, y):
    r = intmask(r_uint(x) - r_uint(y))
    if r^x < 0 and r^~y < 0:
        raise OverflowError("integer subtraction")
    return r

@jit.oopspec("int.mul_ovf(a, b)")
def ll_int_mul_ovf(a, b):
    if INT_BITS_1 < LLONG_BITS_1:
        rr = r_longlong(a) * r_longlong(b)
        r = intmask(rr)
        if r_longlong(r) != rr:
            raise OverflowError("integer multiplication")
        return r
    else:
        longprod = intmask(a * b)
        doubleprod = float(a) * float(b)
        doubled_longprod = float(longprod)

        # Fast path for normal case:  small multiplicands, and no info
        # is lost in either method.
        if doubled_longprod == doubleprod:
            return longprod

        # Somebody somewhere lost info.  Close enough, or way off?  Note
        # that a != 0 and b != 0 (else doubled_longprod == doubleprod == 0).
        # The difference either is or isn't significant compared to the
        # true value (of which doubleprod is a good approximation).
        # absdiff/absprod <= 1/32 iff 32 * absdiff <= absprod -- 5 good
        # bits is "close enough"
        if 32.0 * abs(doubled_longprod - doubleprod) <= abs(doubleprod):
            return longprod

        raise OverflowError("integer multiplication")


# ---------- lshift, neg, abs ----------

def ll_int_lshift_ovf(x, y):
    result = x << y
    if (result >> y) != x:
        raise OverflowError("x<<y loosing bits or changing sign")
    return result

def ll_int_neg_ovf(x):
    if jit.we_are_jitted():
        return ll_int_sub_ovf(0, x)
    if x == INT_MIN:
        raise OverflowError
    return -x

def ll_int_abs_ovf(x):
    if x == INT_MIN:
        raise OverflowError
    return abs(x)


#Helper functions for comparisons

def _rtype_compare_template(hop, func):
    s_int1, s_int2 = hop.args_s
    if s_int1.unsigned or s_int2.unsigned:
        if not s_int1.nonneg or not s_int2.nonneg:
            raise TyperError("comparing a signed and an unsigned number")

    repr = hop.rtyper.getrepr(annmodel.unionof(s_int1, s_int2)).as_int
    vlist = hop.inputargs(repr, repr)
    hop.exception_is_here()
    return hop.genop(repr.opprefix + func, vlist, resulttype=Bool)


#

def ll_hash_int(n):
    return intmask(n)

def ll_hash_long_long(n):
    return intmask(intmask(n) + 9 * intmask(n >> 32))

def ll_eq_shortint(n, m):
    return intmask(n) == intmask(m)
ll_eq_shortint.no_direct_compare = True

def ll_check_chr(n):
    if 0 <= n <= 255:
        return
    else:
        raise ValueError

def ll_check_unichr(n):
    from rpython.rlib.runicode import MAXUNICODE
    if 0 <= n <= MAXUNICODE:
        return
    else:
        raise ValueError

#
# _________________________ Conversions _________________________

class __extend__(pairtype(IntegerRepr, FloatRepr)):
    def convert_from_to((r_from, r_to), v, llops):
        if r_from.lowleveltype == Unsigned and r_to.lowleveltype == Float:
            log.debug('explicit cast_uint_to_float')
            return llops.genop('cast_uint_to_float', [v], resulttype=Float)
        if r_from.lowleveltype == Signed and r_to.lowleveltype == Float:
            log.debug('explicit cast_int_to_float')
            return llops.genop('cast_int_to_float', [v], resulttype=Float)
        if r_from.lowleveltype == SignedLongLong and r_to.lowleveltype == Float:
            log.debug('explicit cast_longlong_to_float')
            return llops.genop('cast_longlong_to_float', [v], resulttype=Float)
        if r_from.lowleveltype == UnsignedLongLong and r_to.lowleveltype == Float:
            log.debug('explicit cast_ulonglong_to_float')
            return llops.genop('cast_ulonglong_to_float', [v], resulttype=Float)
        return NotImplemented

class __extend__(pairtype(FloatRepr, IntegerRepr)):
    def convert_from_to((r_from, r_to), v, llops):
        if r_from.lowleveltype == Float and r_to.lowleveltype == Unsigned:
            log.debug('explicit cast_float_to_uint')
            return llops.genop('cast_float_to_uint', [v], resulttype=Unsigned)
        if r_from.lowleveltype == Float and r_to.lowleveltype == Signed:
            log.debug('explicit cast_float_to_int')
            return llops.genop('cast_float_to_int', [v], resulttype=Signed)
        if r_from.lowleveltype == Float and r_to.lowleveltype == SignedLongLong:
            log.debug('explicit cast_float_to_longlong')
            return llops.genop('cast_float_to_longlong', [v], resulttype=SignedLongLong)
        if r_from.lowleveltype == Float and r_to.lowleveltype == UnsignedLongLong:
            log.debug('explicit cast_float_to_ulonglong')
            return llops.genop('cast_float_to_ulonglong', [v], resulttype=UnsignedLongLong)
        return NotImplemented
