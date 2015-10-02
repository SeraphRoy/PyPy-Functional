from rpython.jit.backend.llsupport.regalloc import (RegisterManager, FrameManager,
                                                    TempBox, compute_vars_longevity,
                                                    BaseRegalloc)
from rpython.jit.backend.ppc.arch import (WORD, MY_COPY_OF_REGS, IS_PPC_32)
from rpython.jit.codewriter import longlong
from rpython.jit.backend.ppc.jump import (remap_frame_layout,
                                          remap_frame_layout_mixed)
from rpython.jit.backend.ppc.locations import imm, get_fp_offset
from rpython.jit.backend.ppc.helper.regalloc import _check_imm_arg, check_imm_box
from rpython.jit.backend.ppc.helper import regalloc as helper
from rpython.jit.metainterp.history import (Const, ConstInt, ConstFloat, ConstPtr,
                                            Box, BoxPtr,
                                            INT, REF, FLOAT)
from rpython.jit.metainterp.history import JitCellToken, TargetToken
from rpython.jit.metainterp.resoperation import rop
from rpython.jit.backend.ppc import locations
from rpython.rtyper.lltypesystem import rffi, lltype, rstr, llmemory
from rpython.rtyper.lltypesystem.lloperation import llop
from rpython.rtyper.annlowlevel import cast_instance_to_gcref
from rpython.jit.backend.llsupport import symbolic
from rpython.jit.backend.llsupport.descr import ArrayDescr
import rpython.jit.backend.ppc.register as r
import rpython.jit.backend.ppc.condition as c
from rpython.jit.backend.llsupport.descr import unpack_arraydescr
from rpython.jit.backend.llsupport.descr import unpack_fielddescr
from rpython.jit.backend.llsupport.descr import unpack_interiorfielddescr
from rpython.jit.backend.llsupport.gcmap import allocate_gcmap
from rpython.rlib.objectmodel import we_are_translated
from rpython.jit.codewriter.effectinfo import EffectInfo
from rpython.rlib import rgc
from rpython.rlib.rarithmetic import r_uint

LIMIT_LOOP_BREAK = 15000      # should be much smaller than 32 KB

# xxx hack: set a default value for TargetToken._arm_loop_code.  If 0, we know
# that it is a LABEL that was not compiled yet.
TargetToken._ppc_loop_code = 0

class TempInt(TempBox):
    type = INT

    def __repr__(self):
        return "<TempInt at %s>" % (id(self),)

class TempPtr(TempBox):
    type = REF

    def __repr__(self):
        return "<TempPtr at %s>" % (id(self),)

class TempFloat(TempBox):
    type = FLOAT

    def __repr__(self):
        return "<TempFloat at %s>" % (id(self),)


class FPRegisterManager(RegisterManager):
    all_regs              = r.MANAGED_FP_REGS
    box_types             = [FLOAT]
    save_around_call_regs = r.VOLATILES_FLOAT
    assert set(save_around_call_regs).issubset(all_regs)

    def convert_to_adr(self, c):
        assert isinstance(c, ConstFloat)
        adr = self.assembler.datablockwrapper.malloc_aligned(8, 8)
        x = c.getfloatstorage()
        rffi.cast(rffi.CArrayPtr(longlong.FLOATSTORAGE), adr)[0] = x
        return adr

    def convert_to_imm(self, c):
        adr = self.convert_to_adr(c)
        return locations.ConstFloatLoc(adr)

    def __init__(self, longevity, frame_manager=None, assembler=None):
        RegisterManager.__init__(self, longevity, frame_manager, assembler)

    def call_result_location(self, v):
        return r.f1

    def ensure_reg(self, box):
        if isinstance(box, Const):
            loc = self.get_scratch_reg()
            immadrvalue = self.convert_to_adr(box)
            mc = self.assembler.mc
            mc.load_imm(r.SCRATCH, immadrvalue)
            mc.lfdx(loc.value, 0, r.SCRATCH.value)
        else:
            assert box in self.temp_boxes
            loc = self.make_sure_var_in_reg(box,
                    forbidden_vars=self.temp_boxes)
        return loc

    def get_scratch_reg(self):
        box = TempFloat()
        reg = self.force_allocate_reg(box, forbidden_vars=self.temp_boxes)
        self.temp_boxes.append(box)
        return reg


class PPCRegisterManager(RegisterManager):
    all_regs              = r.MANAGED_REGS
    box_types             = None       # or a list of acceptable types
    no_lower_byte_regs    = all_regs
    save_around_call_regs = r.VOLATILES
    frame_reg             = r.SPP
    assert set(save_around_call_regs).issubset(all_regs)

    REGLOC_TO_COPY_AREA_OFS = {
        r.r5:   MY_COPY_OF_REGS + 0 * WORD,
        r.r6:   MY_COPY_OF_REGS + 1 * WORD,
        r.r7:   MY_COPY_OF_REGS + 2 * WORD,
        r.r8:   MY_COPY_OF_REGS + 3 * WORD,
        r.r9:   MY_COPY_OF_REGS + 4 * WORD,
        r.r10:  MY_COPY_OF_REGS + 5 * WORD,
        r.r11:  MY_COPY_OF_REGS + 6 * WORD,
        r.r12:  MY_COPY_OF_REGS + 7 * WORD,
        r.r14:  MY_COPY_OF_REGS + 8 * WORD,
        r.r15:  MY_COPY_OF_REGS + 9 * WORD,
        r.r16:  MY_COPY_OF_REGS + 10 * WORD,
        r.r17:  MY_COPY_OF_REGS + 11 * WORD,
        r.r18:  MY_COPY_OF_REGS + 12 * WORD,
        r.r19:  MY_COPY_OF_REGS + 13 * WORD,
        r.r20:  MY_COPY_OF_REGS + 14 * WORD,
        r.r21:  MY_COPY_OF_REGS + 15 * WORD,
        r.r22:  MY_COPY_OF_REGS + 16 * WORD,
        r.r23:  MY_COPY_OF_REGS + 17 * WORD,
        r.r24:  MY_COPY_OF_REGS + 18 * WORD,
        r.r25:  MY_COPY_OF_REGS + 19 * WORD,
        r.r26:  MY_COPY_OF_REGS + 20 * WORD,
        r.r27:  MY_COPY_OF_REGS + 21 * WORD,
        r.r28:  MY_COPY_OF_REGS + 22 * WORD,
        r.r29:  MY_COPY_OF_REGS + 23 * WORD,
        r.r30:  MY_COPY_OF_REGS + 24 * WORD,
    }

    def __init__(self, longevity, frame_manager=None, assembler=None):
        RegisterManager.__init__(self, longevity, frame_manager, assembler)

    def call_result_location(self, v):
        return r.r3

    def convert_to_int(self, c):
        if isinstance(c, ConstInt):
            return rffi.cast(lltype.Signed, c.value)
        else:
            assert isinstance(c, ConstPtr)
            return rffi.cast(lltype.Signed, c.value)

    def convert_to_imm(self, c):
        val = self.convert_to_int(c)
        return locations.ImmLocation(val)

    def ensure_reg(self, box):
        if isinstance(box, Const):
            loc = self.get_scratch_reg()
            immvalue = self.convert_to_int(box)
            self.assembler.mc.load_imm(loc, immvalue)
        else:
            assert box in self.temp_boxes
            loc = self.make_sure_var_in_reg(box,
                    forbidden_vars=self.temp_boxes)
        return loc

    def get_scratch_reg(self):
        box = TempBox()
        reg = self.force_allocate_reg(box, forbidden_vars=self.temp_boxes)
        self.temp_boxes.append(box)
        return reg


class PPCFrameManager(FrameManager):
    def __init__(self, base_ofs):
        FrameManager.__init__(self)
        self.used = []
        self.base_ofs = base_ofs

    def frame_pos(self, loc, box_type):
        #return locations.StackLocation(loc, get_fp_offset(self.base_ofs, loc), box_type)
        return locations.StackLocation(loc, get_fp_offset(self.base_ofs, loc), box_type)

    @staticmethod
    def frame_size(type):
        return 1

    @staticmethod
    def get_loc_index(loc):
        assert isinstance(loc, locations.StackLocation)
        return loc.position


class Regalloc(BaseRegalloc):

    def __init__(self, assembler=None):
        self.cpu = assembler.cpu
        #self.frame_manager = PPCFrameManager(self.cpu.get_baseofs_of_frame_field())
        self.assembler = assembler
        self.jump_target_descr = None
        self.final_jump_op = None

    def _prepare(self,  inputargs, operations, allgcrefs):
        cpu = self.assembler.cpu
        self.fm = PPCFrameManager(cpu.get_baseofs_of_frame_field())
        operations = cpu.gc_ll_descr.rewrite_assembler(cpu, operations,
                                                       allgcrefs)
        # compute longevity of variables
        longevity, last_real_usage = compute_vars_longevity(
                                                    inputargs, operations)
        self.longevity = longevity
        self.last_real_usage = last_real_usage
        self.rm = PPCRegisterManager(self.longevity,
                                     frame_manager = self.fm,
                                     assembler = self.assembler)
        self.fprm = FPRegisterManager(self.longevity, frame_manager = self.fm,
                                      assembler = self.assembler)
        return operations

    def prepare_loop(self, inputargs, operations, looptoken, allgcrefs):
        operations = self._prepare(inputargs, operations, allgcrefs)
        self._set_initial_bindings(inputargs, looptoken)
        # note: we need to make a copy of inputargs because possibly_free_vars
        # is also used on op args, which is a non-resizable list
        self.possibly_free_vars(list(inputargs))
        self.min_bytes_before_label = 4    # for redirect_call_assembler()
        return operations

    def prepare_bridge(self, inputargs, arglocs, operations, allgcrefs,
                       frame_info):
        operations = self._prepare(inputargs, operations, allgcrefs)
        self._update_bindings(arglocs, inputargs)
        self.min_bytes_before_label = 0
        return operations

    def ensure_next_label_is_at_least_at_position(self, at_least_position):
        self.min_bytes_before_label = max(self.min_bytes_before_label,
                                          at_least_position)

    def _update_bindings(self, locs, inputargs):
        # XXX this should probably go to llsupport/regalloc.py
        used = {}
        i = 0
        for loc in locs:
            if loc is None: # xxx bit kludgy
                loc = r.SPP
            arg = inputargs[i]
            i += 1
            if loc.is_reg():
                if loc is r.SPP:
                    self.rm.bindings_to_frame_reg[arg] = None
                else:
                    self.rm.reg_bindings[arg] = loc
                    used[loc] = None
            elif loc.is_fp_reg():
                self.fprm.reg_bindings[arg] = loc
                used[loc] = None
            else:
                assert loc.is_stack()
                self.fm.bind(arg, loc)
        self.rm.free_regs = []
        for reg in self.rm.all_regs:
            if reg not in used:
                self.rm.free_regs.append(reg)
        self.fprm.free_regs = []
        for reg in self.fprm.all_regs:
            if reg not in used:
                self.fprm.free_regs.append(reg)
        self.possibly_free_vars(list(inputargs))
        self.fm.finish_binding()
        self.rm._check_invariants()
        self.fprm._check_invariants()

    def get_final_frame_depth(self):
        return self.fm.get_frame_depth()

    def possibly_free_var(self, var):
        if var is not None:
            if var.type == FLOAT:
                self.fprm.possibly_free_var(var)
            else:
                self.rm.possibly_free_var(var)

    def possibly_free_vars(self, vars):
        for var in vars:
            self.possibly_free_var(var)

    def possibly_free_vars_for_op(self, op):
        for i in range(op.numargs()):
            var = op.getarg(i)
            self.possibly_free_var(var)

    def force_allocate_reg(self, var):
        if var.type == FLOAT:
            forbidden_vars = self.fprm.temp_boxes
            return self.fprm.force_allocate_reg(var, forbidden_vars)
        else:
            forbidden_vars = self.rm.temp_boxes
            return self.rm.force_allocate_reg(var, forbidden_vars)

    def force_allocate_reg_or_cc(self, var):
        assert var.type == INT
        if self.next_op_can_accept_cc(self.operations, self.rm.position):
            # hack: return the SPP location to mean "lives in CC".  This
            # SPP will not actually be used, and the location will be freed
            # after the next op as usual.
            self.rm.force_allocate_frame_reg(var)
            return r.SPP
        else:
            # else, return a regular register (not SPP).
            return self.force_allocate_reg(var)

    def walk_operations(self, inputargs, operations):
        from rpython.jit.backend.ppc.ppc_assembler import (
            operations as asm_operations)
        i = 0
        self.limit_loop_break = (self.assembler.mc.get_relative_pos() +
                                     LIMIT_LOOP_BREAK)
        self.operations = operations
        while i < len(operations):
            op = operations[i]
            self.assembler.mc.mark_op(op)
            self.rm.position = i
            self.fprm.position = i
            if op.has_no_side_effect() and op.result not in self.longevity:
                i += 1
                self.possibly_free_vars_for_op(op)
                continue
            #
            for j in range(op.numargs()):
                box = op.getarg(j)
                if box.type != FLOAT:
                    self.rm.temp_boxes.append(box)
                else:
                    self.fprm.temp_boxes.append(box)
            #
            opnum = op.getopnum()
            if not we_are_translated() and opnum == -124:
                self._consider_force_spill(op)
            else:
                arglocs = oplist[opnum](self, op)
                asm_operations[opnum](self.assembler, op, arglocs, self)
            self.free_op_vars()
            self.possibly_free_var(op.result)
            self.rm._check_invariants()
            self.fprm._check_invariants()
            if self.assembler.mc.get_relative_pos() > self.limit_loop_break:
                self.assembler.break_long_loop()
                self.limit_loop_break = (self.assembler.mc.get_relative_pos() +
                                             LIMIT_LOOP_BREAK)
            i += 1
        assert not self.rm.reg_bindings
        assert not self.fprm.reg_bindings
        self.flush_loop()
        self.assembler.mc.mark_op(None) # end of the loop
        self.operations = None
        for arg in inputargs:
            self.possibly_free_var(arg)

    def flush_loop(self):
        # Emit a nop in the rare case where we have a guard_not_invalidated
        # immediately before a label
        mc = self.assembler.mc
        while self.min_bytes_before_label > mc.get_relative_pos():
            mc.nop()

    def get_gcmap(self, forbidden_regs=[], noregs=False):
        frame_depth = self.fm.get_frame_depth()
        gcmap = allocate_gcmap(self.assembler, frame_depth,
                               r.JITFRAME_FIXED_SIZE)
        for box, loc in self.rm.reg_bindings.iteritems():
            if loc in forbidden_regs:
                continue
            if box.type == REF and self.rm.is_still_alive(box):
                assert not noregs
                assert loc.is_reg()
                val = self.assembler.cpu.all_reg_indexes[loc.value]
                gcmap[val // WORD // 8] |= r_uint(1) << (val % (WORD * 8))
        for box, loc in self.fm.bindings.iteritems():
            if box.type == REF and self.rm.is_still_alive(box):
                assert isinstance(loc, locations.StackLocation)
                val = loc.get_position() + r.JITFRAME_FIXED_SIZE
                gcmap[val // WORD // 8] |= r_uint(1) << (val % (WORD * 8))
        return gcmap

    def loc(self, var):
        if var.type == FLOAT:
            return self.fprm.loc(var)
        else:
            return self.rm.loc(var)

    def next_instruction(self):
        self.rm.next_instruction()
        self.fprm.next_instruction()

    def force_spill_var(self, var):
        if var.type == FLOAT:
            self.fprm.force_spill_var(var)
        else:
            self.rm.force_spill_var(var)

    def _consider_force_spill(self, op):
        # This operation is used only for testing
        self.force_spill_var(op.getarg(0))

    def before_call(self, force_store=[], save_all_regs=False):
        self.rm.before_call(force_store, save_all_regs)
        self.fprm.before_call(force_store, save_all_regs)

    def after_call(self, v):
        if v.type == FLOAT:
            return self.fprm.after_call(v)
        else:
            return self.rm.after_call(v)

    def call_result_location(self, v):
        if v.type == FLOAT:
            return self.fprm.call_result_location(v)
        else:
            return self.rm.call_result_location(v)

    def ensure_reg(self, box):
        if box.type == FLOAT:
            return self.fprm.ensure_reg(box)
        else:
            return self.rm.ensure_reg(box)

    def ensure_reg_or_16bit_imm(self, box):
        if box.type == FLOAT:
            return self.fprm.ensure_reg(box)
        else:
            if check_imm_box(box):
                return imm(box.getint())
            return self.rm.ensure_reg(box)

    def ensure_reg_or_any_imm(self, box):
        if box.type == FLOAT:
            return self.fprm.ensure_reg(box)
        else:
            if isinstance(box, Const):
                return imm(box.getint())
            return self.rm.ensure_reg(box)

    def get_scratch_reg(self, type):
        if type == FLOAT:
            return self.fprm.get_scratch_reg()
        else:
            return self.rm.get_scratch_reg()

    def free_op_vars(self):
        # free the boxes in the 'temp_boxes' lists, which contain both
        # temporary boxes and all the current operation's arguments
        self.rm.free_temp_vars()
        self.fprm.free_temp_vars()

    # ******************************************************
    # *         P R E P A R E  O P E R A T I O N S         * 
    # ******************************************************


    def void(self, op):
        return []

    prepare_int_add = helper.prepare_int_add_or_mul
    prepare_int_sub = helper.prepare_int_sub
    prepare_int_mul = helper.prepare_int_add_or_mul

    prepare_int_floordiv = helper.prepare_binary_op
    prepare_int_mod = helper.prepare_binary_op
    prepare_int_and = helper.prepare_binary_op
    prepare_int_or = helper.prepare_binary_op
    prepare_int_xor = helper.prepare_binary_op
    prepare_int_lshift = helper.prepare_binary_op
    prepare_int_rshift = helper.prepare_binary_op
    prepare_uint_rshift = helper.prepare_binary_op
    prepare_uint_floordiv = helper.prepare_binary_op

    prepare_int_add_ovf = helper.prepare_binary_op
    prepare_int_sub_ovf = helper.prepare_binary_op
    prepare_int_mul_ovf = helper.prepare_binary_op

    prepare_int_neg = helper.prepare_unary_op
    prepare_int_invert = helper.prepare_unary_op
    prepare_int_signext = helper.prepare_unary_op

    prepare_int_le = helper.prepare_cmp_op
    prepare_int_lt = helper.prepare_cmp_op
    prepare_int_ge = helper.prepare_cmp_op
    prepare_int_gt = helper.prepare_cmp_op
    prepare_int_eq = helper.prepare_cmp_op
    prepare_int_ne = helper.prepare_cmp_op

    prepare_ptr_eq = prepare_int_eq
    prepare_ptr_ne = prepare_int_ne

    prepare_instance_ptr_eq = prepare_ptr_eq
    prepare_instance_ptr_ne = prepare_ptr_ne

    prepare_uint_lt = helper.prepare_cmp_op_unsigned
    prepare_uint_le = helper.prepare_cmp_op_unsigned
    prepare_uint_gt = helper.prepare_cmp_op_unsigned
    prepare_uint_ge = helper.prepare_cmp_op_unsigned

    prepare_int_is_true = helper.prepare_unary_cmp
    prepare_int_is_zero = helper.prepare_unary_cmp

    prepare_float_add = helper.prepare_binary_op
    prepare_float_sub = helper.prepare_binary_op
    prepare_float_mul = helper.prepare_binary_op
    prepare_float_truediv = helper.prepare_binary_op

    prepare_float_lt = helper.prepare_float_cmp
    prepare_float_le = helper.prepare_float_cmp
    prepare_float_eq = helper.prepare_float_cmp
    prepare_float_ne = helper.prepare_float_cmp
    prepare_float_gt = helper.prepare_float_cmp
    prepare_float_ge = helper.prepare_float_cmp
    prepare_float_neg = helper.prepare_unary_op
    prepare_float_abs = helper.prepare_unary_op

    prepare_int_force_ge_zero = helper.prepare_unary_op

    def _prepare_math_sqrt(self, op):
        loc = self.ensure_reg(op.getarg(1))
        self.free_op_vars()
        res = self.fprm.force_allocate_reg(op.result)
        return [loc, res]

    def prepare_cast_float_to_int(self, op):
        loc1 = self.ensure_reg(op.getarg(0))
        self.free_op_vars()
        temp_loc = self.get_scratch_reg(FLOAT)
        res = self.rm.force_allocate_reg(op.result)
        return [loc1, temp_loc, res]

    def prepare_cast_int_to_float(self, op):
        loc1 = self.ensure_reg(op.getarg(0))
        res = self.fprm.force_allocate_reg(op.result)
        return [loc1, res]

    def prepare_convert_float_bytes_to_longlong(self, op):
        loc1 = self.ensure_reg(op.getarg(0))
        res = self.rm.force_allocate_reg(op.result)
        return [loc1, res]

    def prepare_convert_longlong_bytes_to_float(self, op):
        loc1 = self.ensure_reg(op.getarg(0))
        res = self.fprm.force_allocate_reg(op.result)
        return [loc1, res]

    def prepare_finish(self, op):
        descr = op.getdescr()
        fail_descr = cast_instance_to_gcref(descr)
        # we know it does not move, but well
        rgc._make_sure_does_not_move(fail_descr)
        fail_descr = rffi.cast(lltype.Signed, fail_descr)
        if op.numargs() > 0:
            loc = self.ensure_reg(op.getarg(0))
            locs = [loc, imm(fail_descr)]
        else:
            locs = [imm(fail_descr)]
        return locs

    def prepare_call_malloc_gc(self, op):
        return self._prepare_call(op)

    def _prepare_guard(self, op, args=None):
        if args is None:
            args = []
        args.append(imm(self.fm.get_frame_depth()))
        for arg in op.getfailargs():
            if arg:
                args.append(self.loc(arg))
            else:
                args.append(None)
        self.possibly_free_vars(op.getfailargs())
        #
        # generate_quick_failure() produces up to 14 instructions per guard
        self.limit_loop_break -= 14 * 4
        #
        return args

    def load_condition_into_cc(self, box):
        if self.assembler.guard_success_cc == c.cond_none:
            loc = self.ensure_reg(box)
            mc = self.assembler.mc
            mc.cmp_op(0, loc.value, 0, imm=True)
            self.assembler.guard_success_cc = c.NE

    def _prepare_guard_cc(self, op):
        self.load_condition_into_cc(op.getarg(0))
        return self._prepare_guard(op)

    prepare_guard_true = _prepare_guard_cc
    prepare_guard_false = _prepare_guard_cc
    prepare_guard_nonnull = _prepare_guard_cc
    prepare_guard_isnull = _prepare_guard_cc

    def prepare_guard_not_invalidated(self, op):
        pos = self.assembler.mc.get_relative_pos()
        self.ensure_next_label_is_at_least_at_position(pos + 4)
        locs = self._prepare_guard(op)
        return locs

    def prepare_guard_exception(self, op):
        loc = self.ensure_reg(op.getarg(0))
        if op.result in self.longevity:
            resloc = self.force_allocate_reg(op.result)
        else:
            resloc = None
        arglocs = self._prepare_guard(op, [loc, resloc])
        return arglocs

    def prepare_guard_no_exception(self, op):
        arglocs = self._prepare_guard(op)
        return arglocs

    prepare_guard_no_overflow = prepare_guard_no_exception
    prepare_guard_overflow = prepare_guard_no_exception
    prepare_guard_not_forced = prepare_guard_no_exception

    def prepare_guard_value(self, op):
        l0 = self.ensure_reg(op.getarg(0))
        l1 = self.ensure_reg_or_16bit_imm(op.getarg(1))
        arglocs = self._prepare_guard(op, [l0, l1])
        return arglocs

    def prepare_guard_class(self, op):
        x = self.ensure_reg(op.getarg(0))
        y_val = force_int(op.getarg(1).getint())

        arglocs = [x, None, None]

        offset = self.cpu.vtable_offset
        if offset is not None:
            y = r.SCRATCH2
            self.assembler.mc.load_imm(y, y_val)

            assert _check_imm_arg(offset)
            offset_loc = imm(offset)

            arglocs[1] = y
            arglocs[2] = offset_loc

        else:
            # XXX hard-coded assumption: to go from an object to its class
            # we use the following algorithm:
            #   - read the typeid from mem(locs[0]), i.e. at offset 0
            #   - keep the lower half-word read there
            #   - multiply by 4 (on 32-bits only) and use it as an
            #     offset in type_info_group
            #   - add 16/32 bytes, to go past the TYPE_INFO structure
            classptr = y_val
            from rpython.memory.gctypelayout import GCData
            sizeof_ti = rffi.sizeof(GCData.TYPE_INFO)
            type_info_group = llop.gc_get_type_info_group(llmemory.Address)
            type_info_group = rffi.cast(lltype.Signed, type_info_group)
            expected_typeid = classptr - sizeof_ti - type_info_group
            if IS_PPC_32:
                expected_typeid >>= 2
            arglocs[1] = self.ensure_reg_or_16bit_imm(ConstInt(expected_typeid))

        return self._prepare_guard(op, arglocs)

    prepare_guard_nonnull_class = prepare_guard_class

    def compute_hint_frame_locations(self, operations):
        # optimization only: fill in the 'hint_frame_locations' dictionary
        # of rm and xrm based on the JUMP at the end of the loop, by looking
        # at where we would like the boxes to be after the jump.
        op = operations[-1]
        if op.getopnum() != rop.JUMP:
            return
        self.final_jump_op = op
        descr = op.getdescr()
        assert isinstance(descr, TargetToken)
        if descr._ppc_loop_code != 0:
            # if the target LABEL was already compiled, i.e. if it belongs
            # to some already-compiled piece of code
            self._compute_hint_frame_locations_from_descr(descr)
        #else:
        #   The loop ends in a JUMP going back to a LABEL in the same loop.
        #   We cannot fill 'hint_frame_locations' immediately, but we can
        #   wait until the corresponding prepare_op_label() to know where the
        #   we would like the boxes to be after the jump.

    def _compute_hint_frame_locations_from_descr(self, descr):
        arglocs = self.assembler.target_arglocs(descr)
        jump_op = self.final_jump_op
        assert len(arglocs) == jump_op.numargs()
        for i in range(jump_op.numargs()):
            box = jump_op.getarg(i)
            if isinstance(box, Box):
                loc = arglocs[i]
                if loc is not None and loc.is_stack():
                    self.fm.hint_frame_pos[box] = self.fm.get_loc_index(loc)

    def prepare_jump(self, op):
        descr = op.getdescr()
        assert isinstance(descr, TargetToken)
        self.jump_target_descr = descr
        arglocs = self.assembler.target_arglocs(descr)

        # get temporary locs
        tmploc = r.SCRATCH
        fptmploc = r.f0

        # Part about non-floats
        src_locations1 = []
        dst_locations1 = []
        src_locations2 = []
        dst_locations2 = []

        # Build the four lists
        for i in range(op.numargs()):
            box = op.getarg(i)
            src_loc = self.loc(box)
            dst_loc = arglocs[i]
            if box.type != FLOAT:
                src_locations1.append(src_loc)
                dst_locations1.append(dst_loc)
            else:
                src_locations2.append(src_loc)
                dst_locations2.append(dst_loc)

        remap_frame_layout_mixed(self.assembler,
                                 src_locations1, dst_locations1, tmploc,
                                 src_locations2, dst_locations2, fptmploc)
        return []

    def prepare_setfield_gc(self, op):
        ofs, size, _ = unpack_fielddescr(op.getdescr())
        base_loc = self.ensure_reg(op.getarg(0))
        value_loc = self.ensure_reg(op.getarg(1))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        return [value_loc, base_loc, ofs_loc, imm(size)]

    prepare_setfield_raw = prepare_setfield_gc

    def prepare_getfield_gc(self, op):
        ofs, size, sign = unpack_fielddescr(op.getdescr())
        base_loc = self.ensure_reg(op.getarg(0))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        self.free_op_vars()
        res = self.force_allocate_reg(op.result)
        return [base_loc, ofs_loc, res, imm(size), imm(sign)]

    prepare_getfield_raw = prepare_getfield_gc
    prepare_getfield_raw_pure = prepare_getfield_gc
    prepare_getfield_gc_pure = prepare_getfield_gc

    def prepare_increment_debug_counter(self, op):
        base_loc = self.ensure_reg(op.getarg(0))
        temp_loc = r.SCRATCH2
        return [base_loc, temp_loc]

    def prepare_getinteriorfield_gc(self, op):
        t = unpack_interiorfielddescr(op.getdescr())
        ofs, itemsize, fieldsize, sign = t
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        self.free_op_vars()
        result_loc = self.force_allocate_reg(op.result)
        return [base_loc, index_loc, result_loc, ofs_loc,
                imm(itemsize), imm(fieldsize), imm(sign)]

    prepare_getinteriorfield_raw = prepare_getinteriorfield_gc

    def prepare_setinteriorfield_gc(self, op):
        t = unpack_interiorfielddescr(op.getdescr())
        ofs, itemsize, fieldsize, _ = t
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        value_loc = self.ensure_reg(op.getarg(2))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        return [base_loc, index_loc, value_loc, ofs_loc,
                imm(itemsize), imm(fieldsize)]

    prepare_setinteriorfield_raw = prepare_setinteriorfield_gc

    def prepare_arraylen_gc(self, op):
        arraydescr = op.getdescr()
        assert isinstance(arraydescr, ArrayDescr)
        ofs = arraydescr.lendescr.offset
        assert _check_imm_arg(ofs)
        base_loc = self.ensure_reg(op.getarg(0))
        self.free_op_vars()
        res = self.force_allocate_reg(op.result)
        return [res, base_loc, imm(ofs)]

    def prepare_setarrayitem_gc(self, op):
        size, ofs, _ = unpack_arraydescr(op.getdescr())
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        value_loc = self.ensure_reg(op.getarg(2))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        imm_size = imm(size)
        return [base_loc, index_loc, value_loc, ofs_loc,
                imm_size, imm_size]

    prepare_setarrayitem_raw = prepare_setarrayitem_gc

    def prepare_raw_store(self, op):
        size, ofs, _ = unpack_arraydescr(op.getdescr())
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        value_loc = self.ensure_reg(op.getarg(2))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        return [base_loc, index_loc, value_loc, ofs_loc,
                imm(1), imm(size)]

    def prepare_getarrayitem_gc(self, op):
        size, ofs, sign = unpack_arraydescr(op.getdescr())
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        self.free_op_vars()
        result_loc = self.force_allocate_reg(op.result)
        imm_size = imm(size)
        return [base_loc, index_loc, result_loc, ofs_loc,
                imm_size, imm_size, imm(sign)]

    prepare_getarrayitem_raw = prepare_getarrayitem_gc
    prepare_getarrayitem_gc_pure = prepare_getarrayitem_gc

    def prepare_raw_load(self, op):
        size, ofs, sign = unpack_arraydescr(op.getdescr())
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        self.free_op_vars()
        result_loc = self.force_allocate_reg(op.result)
        return [base_loc, index_loc, result_loc, ofs_loc,
                imm(1), imm(size), imm(sign)]

    def prepare_strlen(self, op):
        basesize, itemsize, ofs_length = symbolic.get_array_token(rstr.STR,
                                             self.cpu.translate_support_code)
        base_loc = self.ensure_reg(op.getarg(0))
        self.free_op_vars()
        result_loc = self.force_allocate_reg(op.result)
        return [base_loc, imm(ofs_length), result_loc, imm(WORD), imm(0)]

    def prepare_strgetitem(self, op):
        basesize, itemsize, _ = symbolic.get_array_token(rstr.STR,
                                    self.cpu.translate_support_code)
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(basesize))
        self.free_op_vars()
        result_loc = self.force_allocate_reg(op.result)
        imm_size = imm(itemsize)
        return [base_loc, index_loc, result_loc, ofs_loc,
                imm_size, imm_size, imm(0)]

    def prepare_strsetitem(self, op):
        basesize, itemsize, _ = symbolic.get_array_token(rstr.STR,
                                    self.cpu.translate_support_code)
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        value_loc = self.ensure_reg(op.getarg(2))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(basesize))
        imm_size = imm(itemsize)
        return [base_loc, index_loc, value_loc, ofs_loc,
                imm_size, imm_size]

    def prepare_copystrcontent(self, op):
        src_ptr_loc = self.ensure_reg(op.getarg(0))
        dst_ptr_loc = self.ensure_reg(op.getarg(1))
        src_ofs_loc = self.ensure_reg_or_any_imm(op.getarg(2))
        dst_ofs_loc = self.ensure_reg_or_any_imm(op.getarg(3))
        length_loc  = self.ensure_reg_or_any_imm(op.getarg(4))
        self._spill_before_call(save_all_regs=False)
        return [src_ptr_loc, dst_ptr_loc,
                src_ofs_loc, dst_ofs_loc, length_loc]

    prepare_copyunicodecontent = prepare_copystrcontent

    def prepare_unicodelen(self, op):
        basesize, itemsize, ofs_length = symbolic.get_array_token(rstr.UNICODE,
                                             self.cpu.translate_support_code)
        base_loc = self.ensure_reg(op.getarg(0))
        self.free_op_vars()
        result_loc = self.force_allocate_reg(op.result)
        return [base_loc, imm(ofs_length), result_loc, imm(WORD), imm(0)]

    def prepare_unicodegetitem(self, op):
        basesize, itemsize, _ = symbolic.get_array_token(rstr.UNICODE,
                                    self.cpu.translate_support_code)
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(basesize))
        self.free_op_vars()
        result_loc = self.force_allocate_reg(op.result)
        imm_size = imm(itemsize)
        return [base_loc, index_loc, result_loc, ofs_loc,
                imm_size, imm_size, imm(0)]

    def prepare_unicodesetitem(self, op):
        basesize, itemsize, _ = symbolic.get_array_token(rstr.UNICODE,
                                    self.cpu.translate_support_code)
        base_loc = self.ensure_reg(op.getarg(0))
        index_loc = self.ensure_reg_or_any_imm(op.getarg(1))
        value_loc = self.ensure_reg(op.getarg(2))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(basesize))
        imm_size = imm(itemsize)
        return [base_loc, index_loc, value_loc, ofs_loc,
                imm_size, imm_size]

    prepare_same_as = helper.prepare_unary_op
    prepare_cast_ptr_to_int = prepare_same_as
    prepare_cast_int_to_ptr = prepare_same_as

    def get_oopspecindex(self, op):
        descr = op.getdescr()
        assert descr is not None
        effectinfo = descr.get_extra_info()
        if effectinfo is not None:
            return effectinfo.oopspecindex
        return EffectInfo.OS_NONE

    def prepare_call(self, op):
        oopspecindex = self.get_oopspecindex(op)
        if oopspecindex == EffectInfo.OS_MATH_SQRT:
            return self._prepare_math_sqrt(op)
        return self._prepare_call(op)

    def _spill_before_call(self, save_all_regs=False):
        # spill variables that need to be saved around calls
        self.fprm.before_call(save_all_regs=save_all_regs)
        if not save_all_regs:
            gcrootmap = self.assembler.cpu.gc_ll_descr.gcrootmap
            if gcrootmap and gcrootmap.is_shadow_stack:
                save_all_regs = 2
        self.rm.before_call(save_all_regs=save_all_regs)

    def _prepare_call(self, op, save_all_regs=False):
        args = []
        args.append(None)
        for i in range(op.numargs()):
            args.append(self.loc(op.getarg(i)))
        self._spill_before_call(save_all_regs)
        if op.result:
            resloc = self.after_call(op.result)
            args[0] = resloc
        return args

    def prepare_call_malloc_nursery(self, op):
        self.rm.force_allocate_reg(op.result, selected_reg=r.RES)
        self.rm.temp_boxes.append(op.result)
        tmp_box = TempInt()
        self.rm.force_allocate_reg(tmp_box, selected_reg=r.RSZ)
        self.rm.temp_boxes.append(tmp_box)
        return []

    def prepare_call_malloc_nursery_varsize_frame(self, op):
        sizeloc = self.ensure_reg(op.getarg(0))
        # sizeloc must be in a register, but we can free it now
        # (we take care explicitly of conflicts with r.RES or r.RSZ)
        self.free_op_vars()
        # the result will be in r.RES
        self.rm.force_allocate_reg(op.result, selected_reg=r.RES)
        self.rm.temp_boxes.append(op.result)
        # we need r.RSZ as a temporary
        tmp_box = TempInt()
        self.rm.force_allocate_reg(tmp_box, selected_reg=r.RSZ)
        self.rm.temp_boxes.append(tmp_box)
        return [sizeloc]

    def prepare_call_malloc_nursery_varsize(self, op):
        # the result will be in r.RES
        self.rm.force_allocate_reg(op.result, selected_reg=r.RES)
        self.rm.temp_boxes.append(op.result)
        # we need r.RSZ as a temporary
        tmp_box = TempInt()
        self.rm.force_allocate_reg(tmp_box, selected_reg=r.RSZ)
        self.rm.temp_boxes.append(tmp_box)
        # length_box always survives: it's typically also present in the
        # next operation that will copy it inside the new array.  Make
        # sure it is in a register different from r.RES and r.RSZ.  (It
        # should not be a ConstInt at all.)
        length_box = op.getarg(2)
        lengthloc = self.ensure_reg(length_box)
        return [lengthloc]

    prepare_debug_merge_point = void
    prepare_jit_debug = void
    prepare_keepalive = void
    prepare_enter_portal_frame = void
    prepare_leave_portal_frame = void

    def prepare_cond_call_gc_wb(self, op):
        arglocs = [self.ensure_reg(op.getarg(0))]
        return arglocs

    def prepare_cond_call_gc_wb_array(self, op):
        arglocs = [self.ensure_reg(op.getarg(0)),
                   self.ensure_reg_or_16bit_imm(op.getarg(1)),
                   None]
        if arglocs[1].is_reg():
            arglocs[2] = self.get_scratch_reg(INT)
        return arglocs

    def prepare_force_token(self, op):
        res_loc = self.force_allocate_reg(op.result)
        return [res_loc]

    def prepare_label(self, op):
        descr = op.getdescr()
        assert isinstance(descr, TargetToken)
        inputargs = op.getarglist()
        arglocs = [None] * len(inputargs)
        #
        # we use force_spill() on the boxes that are not going to be really
        # used any more in the loop, but that are kept alive anyway
        # by being in a next LABEL's or a JUMP's argument or fail_args
        # of some guard
        position = self.rm.position
        for arg in inputargs:
            assert isinstance(arg, Box)
            if self.last_real_usage.get(arg, -1) <= position:
                self.force_spill_var(arg)
        #
        # we need to make sure that no variable is stored in spp (=r31)
        for arg in inputargs:
            assert self.loc(arg) is not r.SPP, (
                "variable stored in spp in prepare_label")
        self.rm.bindings_to_frame_reg.clear()
        #
        for i in range(len(inputargs)):
            arg = inputargs[i]
            assert isinstance(arg, Box)
            loc = self.loc(arg)
            assert loc is not r.SPP
            arglocs[i] = loc
            if loc.is_reg():
                self.fm.mark_as_free(arg)
        #
        # if we are too close to the start of the loop, the label's target may
        # get overridden by redirect_call_assembler().  (rare case)
        self.flush_loop()
        #
        descr._ppc_arglocs = arglocs
        descr._ppc_loop_code = self.assembler.mc.currpos()
        descr._ppc_clt = self.assembler.current_clt
        self.assembler.target_tokens_currently_compiling[descr] = None
        self.possibly_free_vars_for_op(op)
        #
        # if the LABEL's descr is precisely the target of the JUMP at the
        # end of the same loop, i.e. if what we are compiling is a single
        # loop that ends up jumping to this LABEL, then we can now provide
        # the hints about the expected position of the spilled variables.
        jump_op = self.final_jump_op
        if jump_op is not None and jump_op.getdescr() is descr:
            self._compute_hint_frame_locations_from_descr(descr)

    def prepare_call_may_force(self, op):
        return self._prepare_call(op, save_all_regs=True)

    prepare_call_release_gil = prepare_call_may_force

    def prepare_call_assembler(self, op):
        locs = self.locs_for_call_assembler(op)
        self._spill_before_call(save_all_regs=True)
        resloc = self.after_call(op.result)
        return [resloc] + locs

    def prepare_force_spill(self, op):
        self.force_spill_var(op.getarg(0))
        return []

    def prepare_guard_not_forced_2(self, op):
        self.rm.before_call(op.getfailargs(), save_all_regs=True)
        arglocs = self._prepare_guard(op)
        return arglocs

    def prepare_zero_ptr_field(self, op):
        base_loc = self.ensure_reg(op.getarg(0))
        ofs_loc = self.ensure_reg_or_16bit_imm(op.getarg(1))
        value_loc = self.ensure_reg(ConstInt(0))
        return [value_loc, base_loc, ofs_loc, imm(WORD)]

    def prepare_zero_array(self, op):
        itemsize, ofs, _ = unpack_arraydescr(op.getdescr())
        base_loc = self.ensure_reg(op.getarg(0))
        startindex_loc = self.ensure_reg_or_16bit_imm(op.getarg(1))
        length_loc = self.ensure_reg_or_16bit_imm(op.getarg(2))
        ofs_loc = self.ensure_reg_or_16bit_imm(ConstInt(ofs))
        return [base_loc, startindex_loc, length_loc, ofs_loc, imm(itemsize)]

    def prepare_cond_call(self, op):
        self.load_condition_into_cc(op.getarg(0))
        locs = []
        # support between 0 and 4 integer arguments
        assert 2 <= op.numargs() <= 2 + 4
        for i in range(1, op.numargs()):
            loc = self.loc(op.getarg(i))
            assert loc.type != FLOAT
            locs.append(loc)
        return locs

def notimplemented(self, op):
    print "[PPC/regalloc] %s not implemented" % op.getopname()
    raise NotImplementedError(op)

def force_int(intvalue):
    # a hack before transaction: force the intvalue argument through
    # rffi.cast(), to turn Symbolics into real values
    return rffi.cast(lltype.Signed, intvalue)


oplist = [notimplemented] * (rop._LAST + 1)

for key, value in rop.__dict__.items():
    key = key.lower()
    if key.startswith('_'):
        continue
    methname = 'prepare_%s' % key
    if hasattr(Regalloc, methname):
        func = getattr(Regalloc, methname).im_func
        oplist[value] = func
