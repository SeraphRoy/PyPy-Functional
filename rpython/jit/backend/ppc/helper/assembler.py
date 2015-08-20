import rpython.jit.backend.ppc.condition as c
from rpython.rlib.rarithmetic import intmask
from rpython.jit.backend.ppc.arch import MAX_REG_PARAMS, IS_PPC_32, WORD
from rpython.jit.metainterp.history import FLOAT
import rpython.jit.backend.ppc.register as r
from rpython.rtyper.lltypesystem import rffi, lltype

def gen_emit_cmp_op(condition, signed=True, fp=False):
    def f(self, op, arglocs, regalloc):
        l0, l1, res = arglocs
        # do the comparison
        self.mc.cmp_op(0, l0.value, l1.value,
                       imm=l1.is_imm(), signed=signed, fp=fp)
        # After the comparison, place the result
        # in the first bit of the CR
        if condition == c.LT or condition == c.U_LT:
            self.mc.cror(0, 0, 0)
        elif condition == c.LE or condition == c.U_LE:
            self.mc.cror(0, 0, 2)
        elif condition == c.EQ:
            self.mc.cror(0, 2, 2)
        elif condition == c.GE or condition == c.U_GE:
            self.mc.cror(0, 1, 2)
        elif condition == c.GT or condition == c.U_GT:
            self.mc.cror(0, 1, 1)
        elif condition == c.NE:
            self.mc.crnor(0, 2, 2)
        else:
            assert 0, "condition not known"

        resval = res.value 
        # move the content of the CR to resval
        self.mc.mfcr(resval)       
        # zero out everything except of the result
        self.mc.rlwinm(resval, resval, 1, 31, 31)
    return f

def gen_emit_unary_cmp_op(condition):
    def f(self, op, arglocs, regalloc):
        reg, res = arglocs

        self.mc.cmp_op(0, reg.value, 0, imm=True)
        if condition == c.IS_ZERO:
            self.mc.cror(0, 2, 2)
        elif condition == c.IS_TRUE:
            self.mc.cror(0, 0, 1)
        else:
            assert 0, "condition not known"

        self.mc.mfcr(res.value)
        self.mc.rlwinm(res.value, res.value, 1, 31, 31)
    return f

def count_reg_args(args):
    reg_args = 0
    words = 0
    count = 0
    for x in range(min(len(args), MAX_REG_PARAMS)):
        if args[x].type == FLOAT:
            count += 1
            words += 1
        else:
            count += 1
            words += 1
        reg_args += 1
        if words > MAX_REG_PARAMS:
            reg_args = x
            break
    return reg_args

class Saved_Volatiles(object):
    """ used in _gen_leave_jitted_hook_code to save volatile registers
        in ENCODING AREA around calls
    """

    def __init__(self, codebuilder, save_RES=True, save_FLOAT=True):
        self.mc = codebuilder
        self.save_RES = save_RES
        self.save_FLOAT = save_FLOAT
        self.FLOAT_OFFSET = len(r.VOLATILES)

    def __enter__(self):
        """ before a call, volatile registers are saved in ENCODING AREA
        """
        for i, reg in enumerate(r.VOLATILES):
            if not self.save_RES and reg is r.RES:
                continue
            self.mc.store(reg.value, r.SPP.value, i * WORD)
        if self.save_FLOAT:
            for i, reg in enumerate(r.VOLATILES_FLOAT):
                if not self.save_RES and reg is r.f1:
                    continue
                self.mc.stfd(reg.value, r.SPP.value,
                             (i + self.FLOAT_OFFSET) * WORD)

    def __exit__(self, *args):
        """ after call, volatile registers have to be restored
        """
        for i, reg in enumerate(r.VOLATILES):
            if not self.save_RES and reg is r.RES:
                continue
            self.mc.load(reg.value, r.SPP.value, i * WORD)
        if self.save_FLOAT:
            for i, reg in enumerate(r.VOLATILES_FLOAT):
                if not self.save_RES and reg is r.f1:
                    continue
                self.mc.lfd(reg.value, r.SPP.value,
                             (i + self.FLOAT_OFFSET) * WORD)
