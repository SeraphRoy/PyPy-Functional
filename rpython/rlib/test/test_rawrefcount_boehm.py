import itertools, os, subprocess, py
from hypothesis import given, strategies
from rpython.tool.udir import udir


def setup_module():
    filename = str(udir.join("test-rawrefcount-boehm-check.c"))
    with open(filename, "w") as f:
        print >> f, '#include "gc/gc_mark.h"'
        print >> f, 'void *testing(void) {'
        print >> f, '    return &GC_set_start_callback;'
        print >> f, '}'

    err = os.system("cd '%s' && gcc -c test-rawrefcount-boehm-check.c"
                    % (udir,))
    if err != 0:
        py.test.skip("Boehm GC not installed or too old version")



TEST_CODE = r"""
#define TEST_BOEHM_RAWREFCOUNT
#include "boehm-rawrefcount.c"

static gcobj_t *alloc_gcobj(void)   /* for tests */
{
    gcobj_t *g = GC_MALLOC(1000);
    printf("gc obj: %p\n", g);
    return g;
}

static pyobj_t *alloc_pyobj(void)   /* for tests */
{
    pyobj_t *p = malloc(1000);
    p->ob_refcnt = 1;
    p->ob_pypy_link = 0;
    printf("py obj: %p\n", p);
    return p;
}

static void decref(pyobj_t *p)      /* for tests */
{
    p->ob_refcnt--;
    if (p->ob_refcnt == 0) {
        printf("decref to zero: %p\n", p);
        free(p);
    }
    assert(p->ob_refcnt >= REFCNT_FROM_PYPY ||
           p->ob_refcnt < REFCNT_FROM_PYPY * 0.99);
}

void run_test(void);     /* forward declaration, produced by the test */

int main(void)
{
    run_test();
    while (gc_rawrefcount_next_dead() != NULL)
        ;
    return 0;
}
"""


operations = strategies.sampled_from([
    'new_pyobj',
    'new_gcobj',
    'create_link',
    'from_obj',
    'to_obj',
    'forget_pyobj',
    'forget_gcobj',
    'collect',
    'dead',
    ])


@strategies.composite
def make_code(draw):
    code = []
    pyobjs = []
    gcobjs = []
    num_gcobj = itertools.count()
    num_pyobj = itertools.count()
    links_g2p = {}
    links_p2g = {}

    def new_gcobj():
        varname = 'g%d' % next(num_gcobj)
        code.append('gcobj_t *volatile %s = alloc_gcobj();' % varname)
        gcobjs.append(varname)
        return varname

    def new_pyobj():
        varname = 'p%d' % next(num_pyobj)
        code.append('pyobj_t *%s = alloc_pyobj();' % varname)
        pyobjs.append(varname)
        return varname

    for op in draw(strategies.lists(operations, average_size=250)):
        if op == 'new_gcobj':
            new_gcobj()
        elif op == 'new_pyobj':
            new_pyobj()
        elif op == 'create_link':
            gvars = [varname for varname in gcobjs if varname not in links_g2p]
            if gvars == []:
                gvars.append(new_gcobj())
            pvars = [varname for varname in pyobjs if varname not in links_p2g]
            if pvars == []:
                pvars.append(new_pyobj())
            gvar = draw(strategies.sampled_from(gvars))
            pvar = draw(strategies.sampled_from(pvars))
            code.append(r'printf("create_link %%p-%%p\n", %s, %s); '
                            % (gvar, pvar) +
                        "%s->ob_refcnt += REFCNT_FROM_PYPY; " % pvar +
                        "gc_rawrefcount_create_link_pypy(%s, %s);"
                            % (gvar, pvar))
            links_g2p[gvar] = pvar
            links_p2g[pvar] = gvar
        elif op == 'from_obj':
            if gcobjs:
                prnt = False
                gvar = draw(strategies.sampled_from(gcobjs))
                if gvar not in links_g2p:
                    check = "== NULL"
                elif links_g2p[gvar] in pyobjs:
                    check = "== %s" % (links_g2p[gvar],)
                else:
                    check = "!= NULL"
                    prnt = True
                code.append("assert(gc_rawrefcount_from_obj(%s) %s);"
                            % (gvar, check))
                if prnt:
                    code.append(r'printf("link %%p-%%p\n", %s, '
                        'gc_rawrefcount_from_obj(%s));' % (gvar, gvar))
        elif op == 'to_obj':
            if pyobjs:
                prnt = False
                pvar = draw(strategies.sampled_from(pyobjs))
                if pvar not in links_p2g:
                    check = "== NULL"
                elif links_p2g[pvar] in gcobjs:
                    check = "== %s" % (links_p2g[pvar],)
                else:
                    check = "!= NULL"
                    prnt = True
                code.append("assert(gc_rawrefcount_to_obj(%s) %s);"
                            % (pvar, check))
                if prnt:
                    code.append(r'printf("link %%p-%%p\n", '
                        'gc_rawrefcount_to_obj(%s), %s);' % (pvar, pvar))
        elif op == 'forget_pyobj':
            if pyobjs:
                index = draw(strategies.sampled_from(range(len(pyobjs))))
                pvar = pyobjs.pop(index)
                code.append(r'printf("-p%%p\n", %s); ' % pvar +
                            "decref(%s); %s = NULL;" % (pvar, pvar))
        elif op == 'forget_gcobj':
            if gcobjs:
                index = draw(strategies.sampled_from(range(len(gcobjs))))
                gvar = gcobjs.pop(index)
                code.append(r'printf("-g%%p\n", %s); ' % gvar +
                            "%s = NULL;" % (gvar,))
        elif op == 'collect':
            code.append("GC_gcollect();")
        elif op == 'dead':
            code.append('gc_rawrefcount_next_dead();')
        else:
            assert False, op

    return '\n'.join(code)


@given(make_code())
def test_random(code):
    filename = str(udir.join("test-rawrefcount-boehm.c"))
    with open(filename, "w") as f:
        print >> f, TEST_CODE
        print >> f, 'void run_test(void) {'
        print >> f, code
        print >> f, '}'

    srcdir = os.path.dirname(os.path.dirname(
        os.path.abspath(os.path.join(__file__))))
    srcdir = os.path.join(srcdir, 'src')

    err = os.system("cd '%s' && gcc -Werror -lgc -I%s -o test-rawrefcount-boehm"
                    " test-rawrefcount-boehm.c" % (udir, srcdir))
    assert err == 0
    p = subprocess.Popen("./test-rawrefcount-boehm", stdout=subprocess.PIPE,
                         cwd=str(udir))
    stdout, _ = p.communicate()
    assert p.wait() == 0

    gcobjs = {}
    pyobjs = {}
    links_p2g = {}
    links_g2p = {}
    for line in stdout.splitlines():
        if line.startswith('py obj: '):
            p = line[8:]
            assert not pyobjs.get(p)
            pyobjs[p] = True
            assert p not in links_p2g
        elif line.startswith('gc obj: '):
            g = line[8:]
            assert not gcobjs.get(g)
            gcobjs[g] = True
            if g in links_g2p: del links_g2p[g]
        elif line.startswith('-p'):
            p = line[2:]
            assert pyobjs[p] == True
            pyobjs[p] = False
        elif line.startswith('-g'):
            g = line[2:]
            assert gcobjs[g] == True
            gcobjs[g] = False
        elif line.startswith('decref to zero: '):
            p = line[16:]
            assert pyobjs[p] == False
            assert p not in links_p2g
            del pyobjs[p]
        elif line.startswith('create_link '):
            g, p = line[12:].split('-')
            assert g in gcobjs
            assert p in pyobjs
            assert g not in links_g2p
            assert p not in links_p2g
            links_g2p[g] = p
            links_p2g[p] = g
        elif line.startswith('link '):
            g, p = line[5:].split('-')
            assert g in gcobjs
            assert p in pyobjs
            assert links_g2p[g] == p
            assert links_p2g[p] == g
        elif line.startswith('plist['):
            pass
        elif line.startswith('next_dead: '):
            p = line[11:]
            assert pyobjs[p] == False
            del pyobjs[p]
            del links_p2g[p]
        else:
            assert False, repr(line)
