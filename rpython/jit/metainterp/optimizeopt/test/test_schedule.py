import py

from rpython.jit.metainterp.history import TargetToken, JitCellToken, TreeLoop
from rpython.jit.metainterp.optimizeopt.util import equaloplists
from rpython.jit.metainterp.optimizeopt.vectorize import (VecScheduleData,
        Pack, NotAProfitableLoop, VectorizingOptimizer)
from rpython.jit.metainterp.optimizeopt.dependency import Node
from rpython.jit.metainterp.optimizeopt.test.test_util import LLtypeMixin
from rpython.jit.metainterp.optimizeopt.test.test_dependency import DependencyBaseTest
from rpython.jit.metainterp.optimizeopt.test.test_vectorize import (FakeMetaInterpStaticData,
        FakeJitDriverStaticData)
from rpython.jit.metainterp.resoperation import rop, ResOperation
from rpython.jit.tool.oparser import parse as opparse
from rpython.jit.tool.oparser_model import get_model

class SchedulerBaseTest(DependencyBaseTest):

    def parse(self, source, inc_label_jump=True):
        ns = {
            'double': self.floatarraydescr,
            'float': self.singlefloatarraydescr,
            'long': self.intarraydescr,
            'int': self.int32arraydescr,
        }
        loop = opparse("        [p0,p1,p2,p3,p4,p5,i0,i1,i2,i3,i4,i5,i6,i7,i8,i9,f0,f1,f2,f3,f4,f5]\n" + source + \
                       "\n        jump(p0,p1,p2,p3,p4,p5,i0,i1,i2,i3,i4,i5,i6,i7,i8,i9,f0,f1,f2,f3,f4,f5)",
                       cpu=self.cpu,
                       namespace=ns)
        if inc_label_jump:
            token = JitCellToken()
            loop.operations = \
                [ResOperation(rop.LABEL, loop.inputargs, None, descr=TargetToken(token))] + \
                loop.operations
            return loop

        del loop.operations[-1]
        return loop

    def pack(self, loop, l, r):
        return [Node(op,1+l+i) for i,op in enumerate(loop.operations[1+l:1+r])]

    def schedule(self, loop_orig, packs, vec_reg_size=16, prepend_invariant=False):
        loop = get_model(False).ExtendedTreeLoop("loop")
        loop.original_jitcell_token = loop_orig.original_jitcell_token
        loop.inputargs = loop_orig.inputargs

        ops = []
        vsd = VecScheduleData(vec_reg_size)
        for pack in packs:
            if len(pack) == 1:
                ops.append(pack[0].getoperation())
            else:
                for op in vsd.as_vector_operation(Pack(pack)):
                    ops.append(op)
        loop.operations = ops
        if prepend_invariant:
            loop.operations = vsd.invariant_oplist + ops
        return loop

    def assert_operations_match(self, loop_a, loop_b):
        assert equaloplists(loop_a.operations, loop_b.operations)

class Test(SchedulerBaseTest, LLtypeMixin):
    def test_schedule_split_load(self):
        loop1 = self.parse("""
        i10 = raw_load(p0, i0, descr=float)
        i11 = raw_load(p0, i1, descr=float)
        i12 = raw_load(p0, i2, descr=float)
        i13 = raw_load(p0, i3, descr=float)
        i14 = raw_load(p0, i4, descr=float)
        i15 = raw_load(p0, i5, descr=float)
        """)
        pack1 = self.pack(loop1, 0, 6)
        loop2 = self.schedule(loop1, [pack1])
        loop3 = self.parse("""
        v1[i32#4] = vec_raw_load(p0, i0, 4, descr=float)
        i14 = raw_load(p0, i4, descr=float)
        i15 = raw_load(p0, i5, descr=float)
        """, False)
        self.assert_equal(loop2, loop3)

    def test_int_to_float(self):
        loop1 = self.parse("""
        i10 = raw_load(p0, i0, descr=long)
        i11 = raw_load(p0, i1, descr=long)
        f10 = cast_int_to_float(i10)
        f11 = cast_int_to_float(i11)
        """)
        pack1 = self.pack(loop1, 0, 2)
        pack2 = self.pack(loop1, 2, 4)
        loop2 = self.schedule(loop1, [pack1, pack2])
        loop3 = self.parse("""
        v1[i64#2] = vec_raw_load(p0, i0, 2, descr=long)
        v2[i32#2] = vec_int_signext(v1[i64#2], 4)
        v3[f64#2] = vec_cast_int_to_float(v2[i32#2])
        """, False)
        self.assert_equal(loop2, loop3)

    def test_scalar_pack(self):
        loop1 = self.parse("""
        i10 = int_add(i0, 73)
        i11 = int_add(i1, 73)
        """)
        pack1 = self.pack(loop1, 0, 2)
        loop2 = self.schedule(loop1, [pack1], prepend_invariant=True)
        loop3 = self.parse("""
        v1[i64#2] = vec_box(2)
        v2[i64#2] = vec_int_pack(v1[i64#2], i0, 0, 1)
        v3[i64#2] = vec_int_pack(v2[i64#2], i1, 1, 1)
        v4[i64#2] = vec_int_expand(73)
        v5[i64#2] = vec_int_add(v3[i64#2], v4[i64#2])
        """, False)
        self.assert_equal(loop2, loop3)

        loop1 = self.parse("""
        f10 = float_add(f0, 73.0)
        f11 = float_add(f1, 73.0)
        """)
        pack1 = self.pack(loop1, 0, 2)
        loop2 = self.schedule(loop1, [pack1], prepend_invariant=True)
        loop3 = self.parse("""
        v1[f64#2] = vec_box(2)
        v2[f64#2] = vec_float_pack(v1[f64#2], f0, 0, 1)
        v3[f64#2] = vec_float_pack(v2[f64#2], f1, 1, 1)
        v4[f64#2] = vec_float_expand(73.0)
        v5[f64#2] = vec_float_add(v3[f64#2], v4[f64#2])
        """, False)
        self.assert_equal(loop2, loop3)
