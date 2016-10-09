# Generated by pypy/tool/import_cffi.py
import cffi

ffi = cffi.FFI()

ffi.embedding_api("""
    int add1(int, int);
""")

ffi.embedding_init_code(r"""
    import sys, time
    sys.stdout.write("preparing")
    for i in range(3):
        sys.stdout.flush()
        time.sleep(0.02)
        sys.stdout.write(".")
    sys.stdout.write("\n")

    from _add1_cffi import ffi

    int(ord("A"))    # check that built-ins are there

    @ffi.def_extern()
    def add1(x, y):
        sys.stdout.write("adding %d and %d\n" % (x, y))
        sys.stdout.flush()
        return x + y
""")

ffi.set_source("_add1_cffi", """
""")

fn = ffi.compile(verbose=True)
print('FILENAME: %s' % (fn,))
