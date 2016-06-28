import py
from rpython.jit.metainterp.compile import ResumeGuardDescr
from rpython.jit.metainterp.history import (ConstInt, INT, REF,
    FLOAT, VECTOR, TargetToken)
from rpython.jit.backend.llsupport.descr import (ArrayDescr, CallDescr,
    unpack_arraydescr, unpack_fielddescr, unpack_interiorfielddescr)
from rpython.jit.backend.llsupport.regalloc import get_scale
from rpython.jit.metainterp.resoperation import (rop, ResOperation,
        VectorOp, VectorGuardOp)
from rpython.rlib.objectmodel import we_are_translated
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.lltypesystem import lltype
from rpython.jit.backend.ppc.locations import imm
from rpython.jit.backend.ppc.arch import IS_BIG_ENDIAN
from rpython.jit.backend.llsupport.vector_ext import VectorExt
from rpython.jit.backend.ppc.arch import PARAM_SAVE_AREA_OFFSET
import rpython.jit.backend.ppc.register as r
import rpython.jit.backend.ppc.condition as c
from rpython.jit.backend.llsupport.asmmemmgr import MachineDataBlockWrapper
from rpython.rtyper.lltypesystem import lltype, rffi

def not_implemented(msg):
    msg = '[ppc/vector_ext] %s\n' % msg
    if we_are_translated():
        llop.debug_print(lltype.Void, msg)
    raise NotImplementedError(msg)

def flush_vec_cc(asm, regalloc, condition, size, result_loc):
    # After emitting an instruction that leaves a boolean result in
    # a condition code (cc), call this.  In the common case, result_loc
    # will be set to SPP by the regalloc, which in this case means
    # "propagate it between this operation and the next guard by keeping
    # it in the cc".  In the uncommon case, result_loc is another
    # register, and we emit a load from the cc into this register.
    assert asm.guard_success_cc == c.cond_none
    if result_loc is r.SPP:
        asm.guard_success_cc = condition
    else:
        resval = result_loc.value
        # either doubleword integer 1 (2x) or word integer 1 (4x)
        ones = regalloc.ivrm.get_scratch_reg().value
        zeros = regalloc.ivrm.get_scratch_reg().value
        asm.mc.vxor(zeros, zeros, zeros)
        if size == 4:
            asm.mc.vspltisw(ones, 1)
        else:
            assert size == 8
            tloc = regalloc.rm.get_scratch_reg()
            asm.mc.load_imm(tloc, asm.VEC_DOUBLE_WORD_ONES)
            asm.mc.lvx(ones, 0, tloc.value)
        asm.mc.vsel(resval, zeros, ones, resval)

class AltiVectorExt(VectorExt):
    pass

class VectorAssembler(object):
    _mixin_ = True

    VEC_DOUBLE_WORD_ONES = 0

    def setup_once_vector(self):
        if IS_BIG_ENDIAN:
            # 2x 64 bit signed integer(1) BE
            data = (b'\x00' * 7 + b'\x01') * 2
        else:
            # 2x 64 bit signed integer(1) LE
            data = (b'\x01' + b'\x00' * 7) * 2
        datablockwrapper = MachineDataBlockWrapper(self.cpu.asmmemmgr, [])
        mem = datablockwrapper.malloc_aligned(len(data), alignment=16)
        datablockwrapper.done()
        addr = rffi.cast(rffi.CArrayPtr(lltype.Char), mem)
        for i in range(len(data)):
            addr[i] = data[i]
        self.VEC_DOUBLE_WORD_ONES = mem

    def emit_vec_load_f(self, op, arglocs, regalloc):
        resloc, baseloc, indexloc, size_loc, ofs, integer_loc, aligned_loc = arglocs
        #src_addr = addr_add(baseloc, ofs_loc, ofs.value, 0)
        assert ofs.value == 0
        itemsize = size_loc.value
        if itemsize == 4:
            self.mc.lxvw4x(resloc.value, indexloc.value, baseloc.value)
        elif itemsize == 8:
            self.mc.lxvd2x(resloc.value, indexloc.value, baseloc.value)

    def emit_vec_load_i(self, op, arglocs, regalloc):
        resloc, baseloc, indexloc, size_loc, ofs, \
            Vhiloc, Vloloc, Vploc, tloc = arglocs
        #src_addr = addr_add(base_loc, ofs_loc, ofs.value, 0)
        assert ofs.value == 0
        Vlo = Vloloc.value
        Vhi = Vhiloc.value
        self.mc.lvx(Vhi, indexloc.value, baseloc.value)
        Vp = Vploc.value
        t = tloc.value
        if IS_BIG_ENDIAN:
            self.mc.lvsl(Vp, indexloc.value, baseloc.value)
        else:
            self.mc.lvsr(Vp, indexloc.value, baseloc.value)
        self.mc.addi(t, baseloc.value, 16)
        self.mc.lvx(Vlo, indexloc.value, t)
        if IS_BIG_ENDIAN:
            self.mc.vperm(resloc.value, Vhi, Vlo, Vp)
        else:
            self.mc.vperm(resloc.value, Vlo, Vhi, Vp)
        #self.mc.trap()

    def _emit_vec_setitem(self, op, arglocs, regalloc):
        # prepares item scale (raw_store does not)
        base_loc, ofs_loc, value_loc, size_loc, baseofs, integer_loc, aligned_loc = arglocs
        scale = get_scale(size_loc.value)
        dest_loc = addr_add(base_loc, ofs_loc, baseofs.value, scale)
        self._vec_store(dest_loc, value_loc, integer_loc.value,
                        size_loc.value, aligned_loc.value)

    genop_discard_vec_setarrayitem_raw = _emit_vec_setitem
    genop_discard_vec_setarrayitem_gc = _emit_vec_setitem

    def emit_vec_store(self, op, arglocs, regalloc):
        baseloc, indexloc, valueloc, sizeloc, baseofs, \
            integer_loc, aligned_loc = arglocs
        #dest_loc = addr_add(base_loc, ofs_loc, baseofs.value, 0)
        assert baseofs.value == 0
        if integer_loc.value:
            Vloloc = regalloc.ivrm.get_scratch_reg()
            Vhiloc = regalloc.ivrm.get_scratch_reg()
            Vploc = regalloc.ivrm.get_scratch_reg()
            tloc = regalloc.rm.get_scratch_reg()
            V1sloc = regalloc.ivrm.get_scratch_reg()
            V1s = V1sloc.value
            V0sloc = regalloc.ivrm.get_scratch_reg()
            V0s = V0sloc.value
            Vmaskloc = regalloc.ivrm.get_scratch_reg()
            Vmask = Vmaskloc.value
            Vlo = Vhiloc.value
            Vhi = Vloloc.value
            Vp = Vploc.value
            t = tloc.value
            Vs = valueloc.value
            # UFF, that is a lot of code for storing unaligned!
            # probably a lot of room for improvement (not locally,
            # but in general for the algorithm)
            self.mc.lvx(Vhi, indexloc.value, baseloc.value)
            #self.mc.lvsr(Vp, indexloc.value, baseloc.value)
            if IS_BIG_ENDIAN:
                self.mc.lvsr(Vp, indexloc.value, baseloc.value)
            else:
                self.mc.lvsl(Vp, indexloc.value, baseloc.value)
            self.mc.addi(t, baseloc.value, 16)
            self.mc.lvx(Vlo, indexloc.value, t)
            self.mc.vspltisb(V1s, -1)
            self.mc.vspltisb(V0s, 0)
            if IS_BIG_ENDIAN:
                self.mc.vperm(Vmask, V0s, V1s, Vp)
            else:
                self.mc.vperm(Vmask, V1s, V0s, Vp)
            self.mc.vperm(Vs, Vs, Vs, Vp)
            self.mc.vsel(Vlo, Vs, Vlo, Vmask)
            self.mc.vsel(Vhi, Vhi, Vs, Vmask)
            self.mc.stvx(Vlo, indexloc.value, t)
            self.mc.stvx(Vhi, indexloc.value, baseloc.value)
        else:
            itemsize = sizeloc.value
            if itemsize == 4:
                self.mc.stxvw4x(valueloc.value, indexloc.value, baseloc.value)
            elif itemsize == 8:
                self.mc.stxvd2x(valueloc.value, indexloc.value, baseloc.value)

    def emit_vec_int_add(self, op, arglocs, regalloc):
        resloc, loc0, loc1, size_loc = arglocs
        size = size_loc.value
        if size == 1:
            self.mc.vaddubm(resloc.value, loc0.value, loc1.value)
        elif size == 2:
            self.mc.vadduhm(resloc.value, loc0.value, loc1.value)
        elif size == 4:
            self.mc.vadduwm(resloc.value, loc0.value, loc1.value)
        elif size == 8:
            self.mc.vaddudm(resloc.value, loc0.value, loc1.value)

    def emit_vec_int_sub(self, op, arglocs, regalloc):
        resloc, loc0, loc1, size_loc = arglocs
        size = size_loc.value
        if size == 1:
            # TODO verify if unsigned subtract is the wanted feature
            self.mc.vsububm(resloc.value, loc0.value, loc1.value)
        elif size == 2:
            # TODO verify if unsigned subtract is the wanted feature
            self.mc.vsubuhm(resloc.value, loc0.value, loc1.value)
        elif size == 4:
            # TODO verify if unsigned subtract is the wanted feature
            self.mc.vsubuwm(resloc.value, loc0.value, loc1.value)
        elif size == 8:
            self.mc.vsubudm(resloc.value, loc0.value, loc1.value)

    def emit_vec_float_add(self, op, arglocs, regalloc):
        resloc, loc0, loc1, itemsize_loc = arglocs
        itemsize = itemsize_loc.value
        if itemsize == 4:
            self.mc.xvaddsp(resloc.value, loc0.value, loc1.value)
        elif itemsize == 8:
            self.mc.xvadddp(resloc.value, loc0.value, loc1.value)

    def emit_vec_float_sub(self, op, arglocs, regalloc):
        resloc, loc0, loc1, itemsize_loc = arglocs
        itemsize = itemsize_loc.value
        if itemsize == 4:
            self.mc.xvsubsp(resloc.value, loc0.value, loc1.value)
        elif itemsize == 8:
            self.mc.xvsubdp(resloc.value, loc0.value, loc1.value)

    def emit_vec_float_mul(self, op, arglocs, regalloc):
        resloc, loc0, loc1, itemsize_loc = arglocs
        itemsize = itemsize_loc.value
        if itemsize == 4:
            self.mc.xvmulsp(resloc.value, loc0.value, loc1.value)
        elif itemsize == 8:
            self.mc.xvmuldp(resloc.value, loc0.value, loc1.value)

    def emit_vec_float_truediv(self, op, arglocs, regalloc):
        resloc, loc0, loc1, itemsize_loc = arglocs
        itemsize = itemsize_loc.value
        if itemsize == 4:
            self.mc.xvdivsp(resloc.value, loc0.value, loc1.value)
        elif itemsize == 8:
            self.mc.xvdivdp(resloc.value, loc0.value, loc1.value)

    def emit_vec_int_mul(self, op, arglocs, regalloc):
        raise NotImplementedError
        pass # TODO

    def emit_vec_int_and(self, op, arglocs, regalloc):
        resloc, loc0, loc1, sizeloc = arglocs
        self.mc.vand(resloc.value, loc0.value, loc1.value)

    def emit_vec_int_or(self, op, arglocs, regalloc):
        resloc, loc0, loc1, sizeloc = arglocs
        self.mc.vor(resloc.value, loc0.value, loc1.value)

    def emit_vec_int_xor(self, op, arglocs, regalloc):
        resloc, loc0, loc1, sizeloc = arglocs
        self.mc.veqv(resloc.value, loc0.value, loc1.value)

    def emit_vec_int_signext(self, op, arglocs, regalloc):
        resloc, loc0 = arglocs
        # TODO
        self.regalloc_mov(loc0, resloc)

    def emit_vec_float_abs(self, op, arglocs, regalloc):
        resloc, argloc, sizeloc = arglocs
        size = sizeloc.value
        if size == 4:
            self.mc.xvabssp(resloc.value, argloc.value)
        elif size == 8:
            self.mc.xvabsdp(resloc.value, argloc.value)
        else:
            notimplemented("[ppc/assembler] float abs for size %d" % size)

    def emit_vec_float_neg(self, op, arglocs, regalloc):
        resloc, argloc, sizeloc = arglocs
        size = sizeloc.value
        if size == 4:
            self.mc.xvnegsp(resloc.value, argloc.value)
        elif size == 8:
            self.mc.xvnegdp(resloc.value, argloc.value)
        else:
            notimplemented("[ppc/assembler] float neg for size %d" % size)

    def emit_guard_vec_guard_true(self, guard_op, guard_token, arglocs, regalloc):
        self._emit_guard(guard_op, arglocs)

    def emit_guard_vec_guard_false(self, guard_op, guard_token, arglocs, regalloc):
        self.guard_success_cc = c.negate(self.guard_success_cc)
        self._emit_guard(guard_op, arglocs)

    #def guard_vector(self, guard_op, regalloc, true):
    #    assert isinstance(guard_op, VectorGuardOp)
    #    arg = guard_op.getarg(0)
    #    assert isinstance(arg, VectorOp)
    #    size = arg.bytesize
    #    temp = regalloc.get_scratch_reg().value
    #    load = arg.bytesize * arg.count - self.cpu.vector_register_size
    #    assert load == 0
    #    if true:
    #        pass
    #        #self.mc.PXOR(temp, temp)
    #        # if the vector is not fully packed blend 1s
    #        #if load < 0:
    #        #    self.mc.PCMPEQQ(temp, temp) # fill with ones
    #        #    self._blend_unused_slots(loc, arg, temp)
    #        #    # reset to zeros
    #        #    self.mc.PXOR(temp, temp)

    #        # cmp with zeros (in temp) creates ones at each slot where it is zero
    #        #self.mc.PCMPEQ(loc, temp, size)
    #        ## temp converted to ones
    #        #self.mc.PCMPEQQ(temp, temp)
    #        ## test if all slots are zero
    #        #self.mc.PTEST(loc, temp)
    #        #self.guard_success_cc = rx86.Conditions['Z']
    #    else:
    #        # if the vector is not fully packed blend 1s
    #        #if load < 0:
    #        #    temp = X86_64_XMM_SCRATCH_REG
    #        #    self.mc.PXOR(temp, temp)
    #        #    self._blend_unused_slots(loc, arg, temp)
    #        #self.mc.PTEST(loc, loc)
    #        self.guard_success_cc = rx86.Conditions['NZ']

    #def _blend_unused_slots(self, loc, arg, temp):
    #    select = 0
    #    bits_used = (arg.count * arg.bytesize * 8)
    #    index = bits_used // 16
    #    while index < 8:
    #        select |= (1 << index)
    #        index += 1
    #    self.mc.PBLENDW_xxi(loc.value, temp.value, select)

    #def _update_at_exit(self, fail_locs, fail_args, faildescr, regalloc):
    #    """ If accumulation is done in this loop, at the guard exit
    #        some vector registers must be adjusted to yield the correct value
    #    """
    #    if not isinstance(faildescr, ResumeGuardDescr):
    #        return
    #    assert regalloc is not None
    #    accum_info = faildescr.rd_vector_info
    #    while accum_info:
    #        pos = accum_info.getpos_in_failargs()
    #        scalar_loc = fail_locs[pos]
    #        vector_loc = accum_info.location
    #        # the upper elements will be lost if saved to the stack!
    #        scalar_arg = accum_info.getoriginal()
    #        assert isinstance(vector_loc, RegLoc)
    #        if not isinstance(scalar_loc, RegLoc):
    #            scalar_loc = regalloc.force_allocate_reg(scalar_arg)
    #        assert scalar_arg is not None
    #        if accum_info.accum_operation == '+':
    #            self._accum_reduce_sum(scalar_arg, vector_loc, scalar_loc)
    #        elif accum_info.accum_operation == '*':
    #            self._accum_reduce_mul(scalar_arg, vector_loc, scalar_loc)
    #        else:
    #            not_implemented("accum operator %s not implemented" %
    #                                        (accum_info.accum_operation)) 
    #        accum_info = accum_info.next()

    #def _accum_reduce_mul(self, arg, accumloc, targetloc):
    #    scratchloc = X86_64_XMM_SCRATCH_REG
    #    self.mov(accumloc, scratchloc)
    #    # swap the two elements
    #    self.mc.SHUFPD_xxi(scratchloc.value, scratchloc.value, 0x01)
    #    self.mc.MULSD(accumloc, scratchloc)
    #    if accumloc is not targetloc:
    #        self.mov(accumloc, targetloc)

    #def _accum_reduce_sum(self, arg, accumloc, targetloc):
    #    # Currently the accumulator can ONLY be the biggest
    #    # size for X86 -> 64 bit float/int
    #    if arg.type == FLOAT:
    #        # r = (r[0]+r[1],r[0]+r[1])
    #        self.mc.HADDPD(accumloc, accumloc)
    #        # upper bits (> 64) are dirty (but does not matter)
    #        if accumloc is not targetloc:
    #            self.mov(accumloc, targetloc)
    #        return
    #    elif arg.type == INT:
    #        scratchloc = X86_64_SCRATCH_REG
    #        self.mc.PEXTRQ_rxi(targetloc.value, accumloc.value, 0)
    #        self.mc.PEXTRQ_rxi(scratchloc.value, accumloc.value, 1)
    #        self.mc.ADD(targetloc, scratchloc)
    #        return

    #    not_implemented("reduce sum for %s not impl." % arg)

    def emit_vec_int_is_true(self, op, arglocs, regalloc):
        resloc, argloc, sizeloc = arglocs
        size = sizeloc.value
        tmp = regalloc.get_scratch_reg().value
        self.mc.vxor(tmp, tmp, tmp)
        # argloc[i] > 0:
        # For an unsigned integer that is equivalent to argloc[i] != 0
        if size == 1:
            self.mc.vcmpgtubx(resloc.value, argloc.value, tmp)
        elif size == 2:
            self.mc.vcmpgtuhx(resloc.value, argloc.value, tmp)
        elif size == 4:
            self.mc.vcmpgtuwx(resloc.value, argloc.value, tmp)
        elif size == 8:
            self.mc.vcmpgtudx(resloc.value, argloc.value, tmp)

    def emit_vec_float_eq(self, op, arglocs, regalloc):
        resloc, loc1, loc2, sizeloc = arglocs
        size = sizeloc.value
        tmp = regalloc.vrm.get_scratch_reg().value
        offloc = regalloc.rm.get_scratch_reg()
        off = offloc.value
        # SP is always 16 byte aligned, and PARAM_SAVE_AREA_OFFSET % 16 == 0
        self.mc.load_imm(offloc, PARAM_SAVE_AREA_OFFSET)
        if size == 4:
            self.mc.xvcmpeqspx(tmp, loc1.value, loc2.value)
            self.mc.stxvw4x(tmp, off, r.SP.value)
        elif size == 8:
            self.mc.xvcmpeqdpx(tmp, loc1.value, loc2.value)
            self.mc.stxvd2x(tmp, off, r.SP.value)
        else:
            notimplemented("[ppc/assembler] float == for size %d" % size)
        self.mc.lvx(resloc.value, off, r.SP.value)
        flush_vec_cc(self, regalloc, c.EQ, op.bytesize, resloc)

    def emit_vec_float_ne(self, op, arglocs, regalloc):
        resloc, loc1, loc2, sizeloc = arglocs
        size = sizeloc.value
        tmp = regalloc.vrm.get_scratch_reg().value
        offloc = regalloc.rm.get_scratch_reg()
        off = offloc.value
        # SP is always 16 byte aligned, and PARAM_SAVE_AREA_OFFSET % 16 == 0
        self.mc.load_imm(offloc, PARAM_SAVE_AREA_OFFSET)
        if size == 4:
            self.mc.xvcmpeqspx(tmp, loc1.value, loc2.value)
            self.mc.stxvw4x(tmp, off, r.SP.value)
        elif size == 8:
            self.mc.xvcmpeqdpx(tmp, loc1.value, loc2.value)
            self.mc.stxvd2x(tmp, off, r.SP.value)
        else:
            notimplemented("[ppc/assembler] float == for size %d" % size)
        res = resloc.value
        self.mc.lvx(res, off, r.SP.value)
        self.mc.vnor(res, res, res) # complement
        flush_vec_cc(self, regalloc, c.NE, op.bytesize, resloc)

    def emit_vec_cast_int_to_float(self, op, arglocs, regalloc):
        res, l0 = arglocs
        offloc = regalloc.rm.get_scratch_reg()
        off = offloc.value
        # SP is always 16 byte aligned, and PARAM_SAVE_AREA_OFFSET % 16 == 0
        self.mc.load_imm(offloc, PARAM_SAVE_AREA_OFFSET)
        self.mc.stvx(l0.value, off, r.SP.value)
        self.mc.lxvd2x(res.value, off, r.SP.value)
        self.mc.xvcvsxddp(res.value, res.value)

    def emit_vec_int_eq(self, op, arglocs, regalloc):
        res, l0, l1, sizeloc = arglocs
        size = sizeloc.value
        if size == 1:
            self.vcmpequbx(res.value, l0.value, l1.value)
        elif size == 2:
            self.vcmpequhx(res.value, l0.value, l1.value)
        elif size == 4:
            self.vcmpequwx(res.value, l0.value, l1.value)
        elif size == 8:
            self.vcmpequdx(res.value, l0.value, l1.value)

    def emit_vec_int_ne(self, op, arglocs, regalloc):
        res, l0, l1, sizeloc = arglocs
        size = sizeloc.value
        tmp = regalloc.get_scratch_reg().value
        self.mc.vxor(tmp, tmp, tmp)
        if size == 1:
            self.vcmpequb(res.value, l0.value, l1.value)
            self.vcmpequbx(res.value, res.value, tmp)
        elif size == 2:
            self.vcmpequh(res.value, l0.value, l1.value)
            self.vcmpequhx(res.value, res.value, tmp)
        elif size == 4:
            self.vcmpequw(res.value, l0.value, l1.value)
            self.vcmpequwx(res.value, res.value, tmp)
        elif size == 8:
            self.vcmpequd(res.value, l0.value, l1.value)
            self.vcmpequdx(res.value, res.value, tmp)

    #def genop_vec_cast_float_to_int(self, op, arglocs, regalloc):
    #    self.mc.CVTPD2DQ(resloc, arglocs[0])
    #def genop_vec_cast_singlefloat_to_float(self, op, arglocs, regalloc):
    #    self.mc.CVTPS2PD(resloc, arglocs[0])

    #def genop_vec_expand_f(self, op, arglocs, regalloc):
    #    srcloc, sizeloc = arglocs
    #    size = sizeloc.value
    #    if isinstance(srcloc, ConstFloatLoc):
    #        # they are aligned!
    #        self.mc.MOVAPD(resloc, srcloc)
    #    elif size == 4:
    #        # the register allocator forces src to be the same as resloc
    #        # r = (s[0], s[0], r[0], r[0])
    #        # since resloc == srcloc: r = (r[0], r[0], r[0], r[0])
    #        self.mc.SHUFPS_xxi(resloc.value, srcloc.value, 0)
    #    elif size == 8:
    #        self.mc.MOVDDUP(resloc, srcloc)
    #    else:
    #        raise AssertionError("float of size %d not supported" % (size,))

    #def genop_vec_expand_i(self, op, arglocs, regalloc):
    #    srcloc, sizeloc = arglocs
    #    if not isinstance(srcloc, RegLoc):
    #        self.mov(srcloc, X86_64_SCRATCH_REG)
    #        srcloc = X86_64_SCRATCH_REG
    #    assert not srcloc.is_xmm
    #    size = sizeloc.value
    #    if size == 1:
    #        self.mc.PINSRB_xri(resloc.value, srcloc.value, 0)
    #        self.mc.PSHUFB(resloc, heap(self.expand_byte_mask_addr))
    #    elif size == 2:
    #        self.mc.PINSRW_xri(resloc.value, srcloc.value, 0)
    #        self.mc.PINSRW_xri(resloc.value, srcloc.value, 4)
    #        self.mc.PSHUFLW_xxi(resloc.value, resloc.value, 0)
    #        self.mc.PSHUFHW_xxi(resloc.value, resloc.value, 0)
    #    elif size == 4:
    #        self.mc.PINSRD_xri(resloc.value, srcloc.value, 0)
    #        self.mc.PSHUFD_xxi(resloc.value, resloc.value, 0)
    #    elif size == 8:
    #        self.mc.PINSRQ_xri(resloc.value, srcloc.value, 0)
    #        self.mc.PINSRQ_xri(resloc.value, srcloc.value, 1)
    #    else:
    #        raise AssertionError("cannot handle size %d (int expand)" % (size,))

    #def genop_vec_pack_i(self, op, arglocs, regalloc):
    #    resultloc, sourceloc, residxloc, srcidxloc, countloc, sizeloc = arglocs
    #    assert isinstance(resultloc, RegLoc)
    #    assert isinstance(sourceloc, RegLoc)
    #    size = sizeloc.value
    #    srcidx = srcidxloc.value
    #    residx = residxloc.value
    #    count = countloc.value
    #    # for small data type conversion this can be quite costy
    #    # NOTE there might be some combinations that can be handled
    #    # more efficiently! e.g.
    #    # v2 = pack(v0,v1,4,4)
    #    si = srcidx
    #    ri = residx
    #    k = count
    #    while k > 0:
    #        if size == 8:
    #            if resultloc.is_xmm and sourceloc.is_xmm: # both xmm
    #                self.mc.PEXTRQ_rxi(X86_64_SCRATCH_REG.value, sourceloc.value, si)
    #                self.mc.PINSRQ_xri(resultloc.value, X86_64_SCRATCH_REG.value, ri)
    #            elif resultloc.is_xmm: # xmm <- reg
    #                self.mc.PINSRQ_xri(resultloc.value, sourceloc.value, ri)
    #            else: # reg <- xmm
    #                self.mc.PEXTRQ_rxi(resultloc.value, sourceloc.value, si)
    #        elif size == 4:
    #            if resultloc.is_xmm and sourceloc.is_xmm:
    #                self.mc.PEXTRD_rxi(X86_64_SCRATCH_REG.value, sourceloc.value, si)
    #                self.mc.PINSRD_xri(resultloc.value, X86_64_SCRATCH_REG.value, ri)
    #            elif resultloc.is_xmm:
    #                self.mc.PINSRD_xri(resultloc.value, sourceloc.value, ri)
    #            else:
    #                self.mc.PEXTRD_rxi(resultloc.value, sourceloc.value, si)
    #        elif size == 2:
    #            if resultloc.is_xmm and sourceloc.is_xmm:
    #                self.mc.PEXTRW_rxi(X86_64_SCRATCH_REG.value, sourceloc.value, si)
    #                self.mc.PINSRW_xri(resultloc.value, X86_64_SCRATCH_REG.value, ri)
    #            elif resultloc.is_xmm:
    #                self.mc.PINSRW_xri(resultloc.value, sourceloc.value, ri)
    #            else:
    #                self.mc.PEXTRW_rxi(resultloc.value, sourceloc.value, si)
    #        elif size == 1:
    #            if resultloc.is_xmm and sourceloc.is_xmm:
    #                self.mc.PEXTRB_rxi(X86_64_SCRATCH_REG.value, sourceloc.value, si)
    #                self.mc.PINSRB_xri(resultloc.value, X86_64_SCRATCH_REG.value, ri)
    #            elif resultloc.is_xmm:
    #                self.mc.PINSRB_xri(resultloc.value, sourceloc.value, ri)
    #            else:
    #                self.mc.PEXTRB_rxi(resultloc.value, sourceloc.value, si)
    #        si += 1
    #        ri += 1
    #        k -= 1

    #genop_vec_unpack_i = genop_vec_pack_i

    #def genop_vec_pack_f(self, op, arglocs, resultloc):
    #    resloc, srcloc, residxloc, srcidxloc, countloc, sizeloc = arglocs
    #    assert isinstance(resloc, RegLoc)
    #    assert isinstance(srcloc, RegLoc)
    #    count = countloc.value
    #    residx = residxloc.value
    #    srcidx = srcidxloc.value
    #    size = sizeloc.value
    #    if size == 4:
    #        si = srcidx
    #        ri = residx
    #        k = count
    #        while k > 0:
    #            if resloc.is_xmm:
    #                src = srcloc.value
    #                if not srcloc.is_xmm:
    #                    # if source is a normal register (unpack)
    #                    assert count == 1
    #                    assert si == 0
    #                    self.mov(srcloc, X86_64_XMM_SCRATCH_REG)
    #                    src = X86_64_XMM_SCRATCH_REG.value
    #                select = ((si & 0x3) << 6)|((ri & 0x3) << 4)
    #                self.mc.INSERTPS_xxi(resloc.value, src, select)
    #            else:
    #                self.mc.PEXTRD_rxi(resloc.value, srcloc.value, si)
    #            si += 1
    #            ri += 1
    #            k -= 1
    #    elif size == 8:
    #        assert resloc.is_xmm
    #        if srcloc.is_xmm:
    #            if srcidx == 0:
    #                if residx == 0:
    #                    # r = (s[0], r[1])
    #                    self.mc.MOVSD(resloc, srcloc)
    #                else:
    #                    assert residx == 1
    #                    # r = (r[0], s[0])
    #                    self.mc.UNPCKLPD(resloc, srcloc)
    #            else:
    #                assert srcidx == 1
    #                if residx == 0:
    #                    # r = (s[1], r[1])
    #                    if resloc != srcloc:
    #                        self.mc.UNPCKHPD(resloc, srcloc)
    #                    self.mc.SHUFPD_xxi(resloc.value, resloc.value, 1)
    #                else:
    #                    assert residx == 1
    #                    # r = (r[0], s[1])
    #                    if resloc != srcloc:
    #                        self.mc.SHUFPD_xxi(resloc.value, resloc.value, 1)
    #                        self.mc.UNPCKHPD(resloc, srcloc)
    #                    # if they are equal nothing is to be done

    #genop_vec_unpack_f = genop_vec_pack_f

class VectorRegalloc(object):
    _mixin_ = True

    def force_allocate_vector_reg(self, op):
        forbidden_vars = self.vrm.temp_boxes
        if op.type == FLOAT:
            return self.vrm.force_allocate_reg(op, forbidden_vars)
        else:
            return self.ivrm.force_allocate_reg(op, forbidden_vars)

    def ensure_vector_reg(self, box):
        if box.type == FLOAT:
            return self.vrm.make_sure_var_in_reg(box,
                               forbidden_vars=self.vrm.temp_boxes)
        else:
            return self.ivrm.make_sure_var_in_reg(box,
                               forbidden_vars=self.ivrm.temp_boxes)

    def _prepare_load(self, op):
        descr = op.getdescr()
        assert isinstance(descr, ArrayDescr)
        assert not descr.is_array_of_pointers() and \
               not descr.is_array_of_structs()
        itemsize, ofs, _ = unpack_arraydescr(descr)
        integer = not (descr.is_array_of_floats() or descr.getconcrete_type() == FLOAT)
        aligned = False
        args = op.getarglist()
        a0 = op.getarg(0)
        a1 = op.getarg(1)
        base_loc = self.ensure_reg(a0)
        ofs_loc = self.ensure_reg(a1)
        result_loc = self.force_allocate_vector_reg(op)
        return [result_loc, base_loc, ofs_loc, imm(itemsize), imm(ofs),
                imm(integer), imm(aligned)]

    def _prepare_load_i(self, op):
        descr = op.getdescr()
        assert isinstance(descr, ArrayDescr)
        assert not descr.is_array_of_pointers() and \
               not descr.is_array_of_structs()
        itemsize, ofs, _ = unpack_arraydescr(descr)
        args = op.getarglist()
        a0 = op.getarg(0)
        a1 = op.getarg(1)
        base_loc = self.ensure_reg(a0)
        ofs_loc = self.ensure_reg(a1)
        result_loc = self.force_allocate_vector_reg(op)
        tloc = self.rm.get_scratch_reg()
        Vhiloc = self.ivrm.get_scratch_reg()
        Vloloc = self.ivrm.get_scratch_reg()
        Vploc = self.ivrm.get_scratch_reg()
        return [result_loc, base_loc, ofs_loc, imm(itemsize), imm(ofs),
                Vhiloc, Vloloc, Vploc, tloc]

    prepare_vec_load_i = _prepare_load_i
    prepare_vec_load_f = _prepare_load

    def prepare_vec_arith(self, op):
        a0 = op.getarg(0)
        a1 = op.getarg(1)
        assert isinstance(op, VectorOp)
        size = op.bytesize
        args = op.getarglist()
        loc0 = self.ensure_vector_reg(a0)
        loc1 = self.ensure_vector_reg(a1)
        resloc = self.force_allocate_vector_reg(op)
        return [resloc, loc0, loc1, imm(size)]

    prepare_vec_int_add = prepare_vec_arith
    prepare_vec_int_sub = prepare_vec_arith
    prepare_vec_int_mul = prepare_vec_arith
    prepare_vec_float_add = prepare_vec_arith
    prepare_vec_float_sub = prepare_vec_arith
    prepare_vec_float_mul = prepare_vec_arith
    prepare_vec_float_truediv = prepare_vec_arith

    # logic functions
    prepare_vec_int_and = prepare_vec_arith
    prepare_vec_int_or = prepare_vec_arith
    prepare_vec_int_xor = prepare_vec_arith

    prepare_vec_float_eq = prepare_vec_arith
    prepare_vec_float_ne = prepare_vec_float_eq
    prepare_vec_int_eq = prepare_vec_float_eq
    prepare_vec_int_ne = prepare_vec_float_eq
    del prepare_vec_arith


    def prepare_vec_store(self, op):
        descr = op.getdescr()
        assert isinstance(descr, ArrayDescr)
        assert not descr.is_array_of_pointers() and \
               not descr.is_array_of_structs()
        itemsize, ofs, _ = unpack_arraydescr(descr)
        a0 = op.getarg(0)
        a1 = op.getarg(1)
        a2 = op.getarg(2)
        baseloc = self.ensure_reg(a0)
        ofsloc = self.ensure_reg(a1)
        valueloc = self.ensure_vector_reg(a2)

        integer = not (descr.is_array_of_floats() or descr.getconcrete_type() == FLOAT)
        aligned = False
        return [baseloc, ofsloc, valueloc,
                imm(itemsize), imm(ofs), imm(integer), imm(aligned)]

    def prepare_vec_int_signext(self, op):
        assert isinstance(op, VectorOp)
        a0 = op.getarg(0)
        loc0 = self.ensure_vector_reg(a0)
        resloc = self.force_allocate_vector_reg(op)
        return [resloc, loc0]

    def prepare_vec_arith_unary(self, op):
        a0 = op.getarg(0)
        loc0 = self.ensure_vector_reg(a0)
        resloc = self.force_allocate_vector_reg(op)
        sizeloc = imm(op.bytesize)
        return [resloc, loc0, sizeloc]

    prepare_vec_float_neg = prepare_vec_arith_unary
    prepare_vec_float_abs = prepare_vec_arith_unary
    del prepare_vec_arith_unary

    #def prepare_vec_pack_i(self, op):
    #    # new_res = vec_pack_i(res, src, index, count)
    #    assert isinstance(op, VectorOp)
    #    arg = op.getarg(1)
    #    index = op.getarg(2)
    #    count = op.getarg(3)
    #    assert isinstance(index, ConstInt)
    #    assert isinstance(count, ConstInt)
    #    args = op.getarglist()
    #    srcloc = self.make_sure_var_in_reg(arg, args)
    #    resloc =  self.xrm.force_result_in_reg(op, op.getarg(0), args)
    #    residx = index.value # where to put it in result?
    #    srcidx = 0
    #    arglocs = [resloc, srcloc, imm(residx), imm(srcidx),
    #               imm(count.value), imm(op.bytesize)]
    #    self.perform(op, arglocs, resloc)

    #prepare_vec_pack_f = prepare_vec_pack_i

    #def prepare_vec_unpack_i(self, op):
    #    assert isinstance(op, VectorOp)
    #    index = op.getarg(1)
    #    count = op.getarg(2)
    #    assert isinstance(index, ConstInt)
    #    assert isinstance(count, ConstInt)
    #    args = op.getarglist()
    #    srcloc = self.make_sure_var_in_reg(op.getarg(0), args)
    #    if op.is_vector():
    #        resloc =  self.xrm.force_result_in_reg(op, op.getarg(0), args)
    #        size = op.bytesize
    #    else:
    #        # unpack into iX box
    #        resloc =  self.force_allocate_reg(op, args)
    #        arg = op.getarg(0)
    #        assert isinstance(arg, VectorOp)
    #        size = arg.bytesize
    #    residx = 0
    #    args = op.getarglist()
    #    arglocs = [resloc, srcloc, imm(residx), imm(index.value), imm(count.value), imm(size)]
    #    self.perform(op, arglocs, resloc)

    #prepare_vec_unpack_f = prepare_vec_unpack_i

    #def prepare_vec_expand_f(self, op):
    #    assert isinstance(op, VectorOp)
    #    arg = op.getarg(0)
    #    args = op.getarglist()
    #    if arg.is_constant():
    #        resloc = self.xrm.force_allocate_reg(op)
    #        srcloc = self.xrm.expand_float(op.bytesize, arg)
    #    else:
    #        resloc = self.xrm.force_result_in_reg(op, arg, args)
    #        srcloc = resloc
    #    self.perform(op, [srcloc, imm(op.bytesize)], resloc)

    #def prepare_vec_expand_i(self, op):
    #    assert isinstance(op, VectorOp)
    #    arg = op.getarg(0)
    #    args = op.getarglist()
    #    if arg.is_constant():
    #        srcloc = self.rm.convert_to_imm(arg)
    #    else:
    #        srcloc = self.make_sure_var_in_reg(arg, args)
    #    resloc = self.xrm.force_allocate_reg(op, args)
    #    self.perform(op, [srcloc, imm(op.bytesize)], resloc)

    def prepare_vec_int_is_true(self, op):
        arg = op.getarg(0)
        assert isinstance(arg, VectorOp)
        argloc = self.ensure_vector_reg(arg)
        resloc = self.force_allocate_vector_reg(op)
        return [resloc, argloc, imm(arg.bytesize)]

    #def _prepare_vec(self, op):
    #    # pseudo instruction, needed to create a new variable
    #    self.xrm.force_allocate_reg(op)

    #prepare_vec_i = _prepare_vec
    #prepare_vec_f = _prepare_vec

    def prepare_vec_cast_float_to_int(self, op):
        l0 = self.ensure_vector_reg(op.getarg(0))
        res = self.force_allocate_vector_reg(op)
        return [res, l0]

    prepare_vec_cast_int_to_float = prepare_vec_cast_float_to_int
    #prepare_vec_cast_float_to_singlefloat = prepare_vec_cast_float_to_int
    #prepare_vec_cast_singlefloat_to_float = prepare_vec_cast_float_to_int

    #def prepare_vec_guard_true(self, op):
    #    arg = op.getarg(0)
    #    loc = self.loc(arg)
    #    self.assembler.guard_vector(op, self.loc(arg), True)
    #    self.perform_guard(op, [], None)

    #def prepare_vec_guard_false(self, op):
    #    arg = op.getarg(0)
    #    loc = self.loc(arg)
    #    self.assembler.guard_vector(op, self.loc(arg), False)
    #    self.perform_guard(op, [], None)

