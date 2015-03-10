import sys

from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.jit.backend.llgraph.runner import ArrayDescr
from rpython.jit.metainterp.history import TargetToken, JitCellToken, Const
from rpython.jit.metainterp.inliner import Inliner
from rpython.jit.metainterp.optimize import InvalidLoop
from rpython.jit.metainterp.optimizeopt.optimizer import Optimizer, Optimization
from rpython.jit.metainterp.optimizeopt.util import make_dispatcher_method
from rpython.jit.metainterp.resoperation import rop, ResOperation, GuardResOp
from rpython.jit.metainterp.resume import Snapshot
from rpython.jit.metainterp import compile
from rpython.rlib.debug import debug_print, debug_start, debug_stop

def optimize_vector(metainterp_sd, jitdriver_sd, loop, optimizations, start_state=None,
                    export_state=True):
    opt = OptVectorize(metainterp_sd, jitdriver_sd, loop, optimizations)
    opt_loop = opt.propagate_all_forward(start_state, export_state)
    if opt.vectorized:
        return opt_loop
    # vectorization is not possible, propagate only normal optimizations
    opt = Optimizer(metainterp_sd, jitdriver_sd, loop, optimizations)
    opt.propagate_all_forward()
    return loop


class VectorizeOptimizer(Optimizer):
    def setup(self):
        pass

class OptVectorize(Optimization):
    """ Try to unroll the loop and find instructions to group """

    inline_short_preamble = True

    def __init__(self, metainterp_sd, jitdriver_sd, loop, optimizations):
        self.optimizer = VectorizeOptimizer(metainterp_sd, jitdriver_sd,
                                             loop, optimizations)
        self.loop_vectorizer_checker = LoopVectorizeChecker()
        self.vectorized = False

    def _rename_arguments_ssa(self, rename_map, label_args, jump_args):
        # fill the map with the renaming boxes. keys are boxes from the label
        # values are the target boxes.
        for la,ja in zip(label_args, jump_args):
            if la != ja:
                rename_map[la] = ja

    def unroll_loop_iterations(self, loop, unroll_factor):
        label_op = loop.operations[0]
        jump_op = loop.operations[-1]
        operations = loop.operations[1:-1]
        loop.operations = []

        iterations = [[op.clone() for op in operations]]
        label_op_args = [self.getvalue(box).get_key_box() for box in label_op.getarglist()]
        values = [self.getvalue(box) for box in label_op.getarglist()]
        #values[0].make_nonnull(self.optimizer)

        jump_op_args = jump_op.getarglist()

        rename_map = {}
        for unroll_i in range(2, unroll_factor+1):
            # for each unrolling factor the boxes are renamed.
            self._rename_arguments_ssa(rename_map, label_op_args, jump_op_args)
            iteration_ops = []
            for op in operations:
                copied_op = op.clone()

                if copied_op.result is not None:
                    # every result assigns a new box, thus creates an entry
                    # to the rename map.
                    new_assigned_box = copied_op.result.clonebox()
                    rename_map[copied_op.result] = new_assigned_box
                    copied_op.result = new_assigned_box

                args = copied_op.getarglist()
                for i, arg in enumerate(args):
                    try:
                        value = rename_map[arg]
                        copied_op.setarg(i, value)
                    except KeyError:
                        pass

                iteration_ops.append(copied_op)

            # the jump arguments have been changed
            # if label(iX) ... jump(i(X+1)) is called, at the next unrolled loop
            # must look like this: label(i(X+1)) ... jump(i(X+2))

            args = jump_op.getarglist()
            for i, arg in enumerate(args):
                try:
                    value = rename_map[arg]
                    jump_op.setarg(i, value)
                except KeyError:
                    pass
            # map will be rebuilt, the jump operation has been updated already
            rename_map.clear()

            iterations.append(iteration_ops)

        # unwrap the loop nesting.
        loop.operations.append(label_op)
        for iteration in iterations:
            for op in iteration:
                loop.operations.append(op)
        loop.operations.append(jump_op)

        return loop

    def _gather_trace_information(self, loop):
        for op in loop.operations:
            self.loop_vectorizer_checker.inspect_operation(op)

    def get_estimated_unroll_factor(self, force_reg_bytes = -1):
        """ force_reg_bytes used for testing """
        # this optimization is not opaque, and needs info about the CPU
        byte_count = self.loop_vectorizer_checker.smallest_type_bytes
        simd_vec_reg_bytes = 16 # TODO get from cpu
        if force_reg_bytes > 0:
            simd_vec_reg_bytes = force_simd_vec_reg_bytes
        unroll_factor = simd_vec_reg_bytes // byte_count
        return unroll_factor

    def propagate_all_forward(self, starting_state, export_state=True):

        self.optimizer.exporting_state = export_state
        loop = self.optimizer.loop
        self.optimizer.clear_newoperations()

        self._gather_trace_information(loop)

        for op in loop.operations:
            self.loop_vectorizer_checker.inspect_operation(op)

        byte_count = self.loop_vectorizer_checker.smallest_type_bytes
        if byte_count == 0:
            # stop, there is no chance to vectorize this trace
            return loop

        unroll_factor = self.get_estimated_unroll_factor()

        self.unroll_loop_iterations(loop, unroll_factor)


        self.vectorized = True

        return loop

class LoopVectorizeChecker(object):

    def __init__(self):
        self.smallest_type_bytes = 0

    def count_RAW_LOAD(self, op):
        descr = op.getdescr()
        assert isinstance(descr, ArrayDescr) # TODO prove this right
        if not isinstance(descr.A.OF, lltype.Ptr):
            byte_count = rffi.sizeof(descr.A.OF)
            if self.smallest_type_bytes == 0 \
               or byte_count < self.smallest_type_bytes:
                self.smallest_type_bytes = byte_count

    def default_count(self, operation):
        pass

dispatch_opt = make_dispatcher_method(LoopVectorizeChecker, 'count_',
        default=LoopVectorizeChecker.default_count)
LoopVectorizeChecker.inspect_operation = dispatch_opt
