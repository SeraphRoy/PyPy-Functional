import py

from rpython.jit.metainterp.optimizeopt.test.test_util import (
    LLtypeMixin, BaseTest, FakeMetaInterpStaticData, convert_old_style_to_targets)
from rpython.jit.metainterp.history import TargetToken, JitCellToken, TreeLoop
from rpython.jit.metainterp.optimizeopt.dependency import (DependencyGraph, Dependency,
        IntegralMod, MemoryRef)
from rpython.jit.metainterp.optimizeopt.vectorize import LoopVectorizeInfo
from rpython.jit.metainterp.resoperation import rop, ResOperation

class DepTestHelper(BaseTest):

    def build_dependency(self, ops, refs = False):
        loop = self.parse_loop(ops)
        lvi = LoopVectorizeInfo()
        if refs:
            lvi.track_memory_refs = True
            for i,op in enumerate(loop.operations):
                lvi.index = i
                lvi.inspect_operation(op)
        self.last_graph = DependencyGraph(loop.operations, lvi.memory_refs)
        for i in range(len(self.last_graph.adjacent_list)):
            self.assert_independent(i,i)
        return self.last_graph

    def parse_loop(self, ops):
        loop = self.parse(ops, postprocess=self.postprocess)
        token = JitCellToken()
        loop.operations = [ResOperation(rop.LABEL, loop.inputargs, None, 
                                   descr=TargetToken(token))] + loop.operations
        if loop.operations[-1].getopnum() == rop.JUMP:
            loop.operations[-1].setdescr(token)
        return loop

    def assert_edges(self, graph, edge_list):
        """ Check if all dependencies are met. for complex cases
        adding None instead of a list of integers skips the test.
        This checks both if a dependency forward and backward exists.
        """
        assert len(edge_list) == len(graph.adjacent_list)
        for idx,edges in enumerate(edge_list):
            if edges is None:
                continue
            dependencies = graph.adjacent_list[idx][:]
            for edge in edges:
                dependency = graph.instr_dependency(idx,edge)
                if edge < idx:
                    dependency = graph.instr_dependency(edge, idx)
                assert dependency is not None, \
                   " it is expected that instruction at index" + \
                   " %d depends on instr on index %d but it does not.\n%s" \
                        % (idx, edge, graph)
                dependencies.remove(dependency)
            assert dependencies == [], \
                    "dependencies unexpected %s.\n%s" \
                    % (dependencies,graph)
    def assert_graph_equal(self, ga, gb):
        assert len(ga.adjacent_list) == len(gb.adjacent_list)
        for i in range(len(ga.adjacent_list)):
            la = ga.adjacent_list[i]
            lb = gb.adjacent_list[i]
            assert len(la) == len(lb)
            assert sorted([l.idx_to for l in la]) == \
                   sorted([l.idx_to for l in lb])
            assert sorted([l.idx_from for l in la]) == \
                   sorted([l.idx_from for l in lb])

    def assert_dependencies(self, ops, memref=False, full_check=True):
        graph = self.build_dependency(ops, memref)
        import re
        deps = {}
        for i,line in enumerate(ops.splitlines()):
            dep_pattern = re.compile("#\s*(\d+):")
            dep_match = dep_pattern.search(line)
            if dep_match:
                label = int(dep_match.group(1))
                deps_list = [int(d) for d in line[dep_match.end():].split(',') if len(d) > 0]
                deps[label] = deps_list

        if full_check:
            edges = [ None ] * len(deps)
            for k,l in deps.items():
                edges[k] = l
            for k,l in deps.items():
                for rk in l:
                    if rk > k:
                        edges[rk].append(k)
            self.assert_edges(graph, edges)
        return graph

    def assert_independent(self, a, b):
        assert self.last_graph.independent(a,b), "{a} and {b} are dependent!".format(a=a,b=b)

    def assert_dependent(self, a, b):
        assert not self.last_graph.independent(a,b), "{a} and {b} are independent!".format(a=a,b=b)

    def _write_dot_and_convert_to_svg(self, graph, ops, filename):
        dot = graph.as_dot(ops)
        with open('/home/rich/' + filename + '.dot', 'w') as fd:
            fd.write(dot)
        with open('/home/rich/'+filename+'.svg', 'w') as fd:
            import subprocess
            subprocess.Popen(['dot', '-Tsvg', '/home/rich/'+filename+'.dot'], stdout=fd).communicate()

class BaseTestDependencyGraph(DepTestHelper):
    def test_dependency_empty(self):
        ops = """
        [] # 0: 1
        jump() # 1:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_dependency_of_constant_not_used(self):
        ops = """
        [] # 0: 2
        i1 = int_add(1,1) # 1: 2
        jump() # 2:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_dependency_simple(self):
        ops = """
        [] # 0: 4
        i1 = int_add(1,1) # 1: 2
        i2 = int_add(i1,1) # 2: 3
        guard_value(i2,3) [] # 3: 4
        jump() # 4:
        """
        graph = self.assert_dependencies(ops, full_check=True)
        self.assert_independent(0,1)
        self.assert_independent(0,2)
        self.assert_independent(0,3)
        self.assert_dependent(1,2)
        self.assert_dependent(2,3)
        self.assert_dependent(1,3)
        self.assert_dependent(2,4)
        self.assert_dependent(3,4)

    def test_def_use_jump_use_def(self):
        ops = """
        [i3] # 0: 1
        i1 = int_add(i3,1) # 1: 2, 3
        guard_value(i1,0) [] # 2: 3
        jump(i1) # 3:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_dependency_guard(self):
        ops = """
        [i3] # 0: 2,3
        i1 = int_add(1,1) # 1: 2
        guard_value(i1,0) [i3] # 2: 3
        jump(i3) # 3:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_dependency_guard_2(self):
        ops = """
        [i1] # 0: 1,2,3
        i2 = int_le(i1, 10) # 1: 2
        guard_true(i2) [i1] # 2: 3
        i3 = int_add(i1,1) # 3: 4
        jump(i3) # 4:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_no_edge_duplication(self):
        ops = """
        [i1] # 0: 1,2,3
        i2 = int_lt(i1,10) # 1: 2
        guard_false(i2) [i1] # 2: 3
        i3 = int_add(i1,i1) # 3: 4
        jump(i3) # 4:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_no_edge_duplication_in_guard_failargs(self):
        ops = """
        [i1] # 0: 1,2,3
        i2 = int_lt(i1,10) # 1: 2
        guard_false(i2) [i1,i1,i2,i1,i2,i1] # 2: 3
        jump(i1) # 3:
        """
        self.assert_dependencies(ops, full_check=True)
        self.assert_dependent(0,1)
        self.assert_dependent(0,2)
        self.assert_dependent(0,3)

    def test_dependencies_1(self):
        ops="""
        [i0, i1, i2] # 0: 1,3,6,7,11
        i4 = int_gt(i1, 0) # 1: 2
        guard_true(i4) [] # 2: 3, 11
        i6 = int_sub(i1, 1) # 3: 4
        i8 = int_gt(i6, 0) # 4: 5
        guard_false(i8) [] # 5: 11
        i10 = int_add(i2, 1) # 6: 8
        i12 = int_sub(i0, 1) # 7: 9, 11
        i14 = int_add(i10, 1) # 8: 11
        i16 = int_gt(i12, 0) # 9: 10
        guard_true(i16) [] # 10: 11
        jump(i12, i1, i14) # 11:
        """
        self.assert_dependencies(ops, full_check=True)
        self.assert_independent(6, 2)
        self.assert_independent(6, 1)
        self.assert_dependent(6, 0)

    def test_prevent_double_arg(self):
        ops="""
        [i0, i1, i2] # 0: 1,3
        i4 = int_gt(i1, i0) # 1: 2
        guard_true(i4) [] # 2: 3
        jump(i0, i1, i2) # 3:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_ovf_dep(self):
        ops="""
        [i0, i1, i2] # 0: 2,3
        i4 = int_sub_ovf(1, 0) # 1: 2
        guard_overflow() [i2] # 2: 3
        jump(i0, i1, i2) # 3:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_exception_dep(self):
        ops="""
        [p0, i1, i2] # 0: 1,3
        i4 = call(p0, 1, descr=nonwritedescr) # 1: 2,3
        guard_no_exception() [] # 2: 3
        jump(p0, i1, i2) # 3:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_call_dependency_on_ptr_but_not_index_value(self):
        ops="""
        [p0, p1, i2] # 0: 1,2,3,4,5
        i3 = int_add(i2,1) # 1: 2
        i4 = call(p0, i3, descr=nonwritedescr) # 2: 3,4,5
        guard_no_exception() [i2] # 3: 4,5
        p2 = getarrayitem_gc(p1,i3) # 4: 5
        jump(p2, p1, i3) # 5:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_call_dependency(self):
        ops="""
        [p0, p1, i2, i5] # 0: 1,2,3,4,5
        i3 = int_add(i2,1) # 1: 2
        i4 = call(i5, i3, descr=nonwritedescr) # 2: 3,4,5
        guard_no_exception() [i2] # 3: 4,5
        p2 = getarrayitem_gc(p1,i3) # 4: 5
        jump(p2, p1, i3) # 5:
        """
        self.assert_dependencies(ops, full_check=True)

    def test_setarrayitem_dependency(self):
        ops="""
        [p0, i1]
        setarrayitem_raw(p0, i1, 1, descr=floatarraydescr) # redef p0[i1]
        i2 = getarrayitem_raw(p0, i1, descr=floatarraydescr) # use of redef above
        setarrayitem_raw(p0, i1, 2, descr=floatarraydescr) # redef of p0[i1]
        jump(p0, i2)
        """
        dep_graph = self.build_dependency(ops)
        self.assert_edges(dep_graph,
                [ [1,2,3], [0,2,3], [0,1,4], [0,1,4], [2,3] ])

    def test_setarrayitem_alias_dependency(self):
        # #1 depends on #2, i1 and i2 might alias, reordering would destroy
        # coorectness
        ops="""
        [p0, i1, i2]
        setarrayitem_raw(p0, i1, 1, descr=floatarraydescr) #1
        setarrayitem_raw(p0, i2, 2, descr=floatarraydescr) #2
        jump(p0, i1, i2)
        """
        dep_graph = self.build_dependency(ops)
        self.assert_edges(dep_graph,
                [ [1,2,3], [0,2], [0,1,3], [0,2] ])
        self.assert_dependent(1,2)
        self.assert_dependent(0,3)

    def test_setarrayitem_depend_with_no_memref_info(self):
        ops="""
        [p0, i1] # 0: 1,2,4
        setarrayitem_raw(p0, i1, 1, descr=floatarraydescr) # 1: 3
        i2 = int_add(i1,1) # 2: 3
        setarrayitem_raw(p0, i2, 2, descr=floatarraydescr) # 3: 4
        jump(p0, i1) # 4:
        """
        self.assert_dependencies(ops, full_check=True)
        self.assert_independent(1,2)
        self.assert_dependent(1,3)

    def test_setarrayitem_dont_depend_with_memref_info(self):
        ops="""
        [p0, i1] # 0: 1,2,3,4
        setarrayitem_raw(p0, i1, 1, descr=chararraydescr) # 1: 4
        i2 = int_add(i1,1) # 2: 3
        setarrayitem_raw(p0, i2, 2, descr=chararraydescr) # 3: 4
        jump(p0, i1) # 4:
        """
        self.assert_dependencies(ops, memref=True, full_check=True)
        self.assert_independent(1,2)
        self.assert_independent(1,3) # they modify 2 different cells

class TestLLtype(BaseTestDependencyGraph, LLtypeMixin):
    pass
