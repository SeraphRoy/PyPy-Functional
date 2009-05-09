import py
from pypy.rlib.jit import JitDriver
from pypy.jit.metainterp.test.test_basic import LLJitMixin, OOJitMixin
from pypy.jit.metainterp.simple_optimize import Optimizer
from pypy.jit.metainterp.policy import StopAtXPolicy


class RecursiveTests:

    def test_simple_recursion(self):
        myjitdriver = JitDriver(greens=[], reds=['n', 'm'])
        def f(n):
            m = n - 2
            while True:
                myjitdriver.jit_merge_point(n=n, m=m)
                n -= 1
                if m == n:
                    return main(n) * 2
                myjitdriver.can_enter_jit(n=n, m=m)
        def main(n):
            if n > 0:
                return f(n+1)
            else:
                return 1
        res = self.meta_interp(main, [20], optimizer=Optimizer)
        assert res == main(20)

    def test_simple_recursion_with_exc(self):
        myjitdriver = JitDriver(greens=[], reds=['n', 'm'])
        class Error(Exception):
            pass
        
        def f(n):
            m = n - 2
            while True:
                myjitdriver.jit_merge_point(n=n, m=m)
                n -= 1
                if n == 10:
                    raise Error
                if m == n:
                    try:
                        return main(n) * 2
                    except Error:
                        return 2
                myjitdriver.can_enter_jit(n=n, m=m)
        def main(n):
            if n > 0:
                return f(n+1)
            else:
                return 1
        res = self.meta_interp(main, [20], optimizer=Optimizer)
        assert res == main(20)

    def test_recursion_three_times(self):
        myjitdriver = JitDriver(greens=[], reds=['n', 'm', 'total'])
        def f(n):
            m = n - 3
            total = 0
            while True:
                myjitdriver.jit_merge_point(n=n, m=m, total=total)
                n -= 1
                total += main(n)
                if m == n:
                    return total + 5
                myjitdriver.can_enter_jit(n=n, m=m, total=total)
        def main(n):
            if n > 0:
                return f(n)
            else:
                return 1
        print
        for i in range(1, 11):
            print '%3d %9d' % (i, f(i))
        res = self.meta_interp(main, [10], optimizer=Optimizer)
        assert res == main(10)
        self.check_enter_count_at_most(10)

    def test_bug_1(self):
        myjitdriver = JitDriver(greens=[], reds=['n', 'i', 'stack'])
        def opaque(n, i):
            if n == 1 and i == 19:
                for j in range(20):
                    res = f(0)      # recurse repeatedly, 20 times
                    assert res == 0
        def f(n):
            stack = [n]
            i = 0
            while i < 20:
                myjitdriver.can_enter_jit(n=n, i=i, stack=stack)
                myjitdriver.jit_merge_point(n=n, i=i, stack=stack)
                opaque(n, i)
                i += 1
            return stack.pop()
        res = self.meta_interp(f, [1], optimizer=Optimizer, repeat=2,
                               policy=StopAtXPolicy(opaque))
        assert res == 1


class TestLLtype(RecursiveTests, LLJitMixin):
    pass

class TestOOtype(RecursiveTests, OOJitMixin):
    def test_simple_recursion_with_exc(self):
        py.test.skip("Fails")
