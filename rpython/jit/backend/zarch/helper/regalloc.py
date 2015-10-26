from rpython.jit.metainterp.history import ConstInt, FLOAT
from rpython.jit.backend.zarch.locations import imm

def check_imm(arg, lower_bound=-2**15, upper_bound=2**15-1):
    if isinstance(arg, ConstInt):
        i = arg.getint()
        return lower_bound <= i <= upper_bound
    return False

def _prepare_binary_arith(self, op):
    a0 = op.getarg(0)
    a1 = op.getarg(1)
    if check_imm(a0):
        a0, a1 = a1, a0
    l0 = self.ensure_reg(a0)
    if check_imm(a1):
        l1 = imm(a1.getint())
    else:
        l1 = self.ensure_reg(a1)
    self.free_op_vars()
    self.force_result_in_reg(op, a0)
    return [l0, l1]
