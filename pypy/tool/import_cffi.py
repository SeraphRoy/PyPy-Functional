#!/usr/bin/env python
""" A simple tool for importing the cffi version into pypy, should sync
whatever version you provide. Usage:

import_cffi.py <path-to-cffi>
"""

import sys, py

def mangle(lines):
    for line in lines:
        line = line.replace('from testing', 'from pypy.module.test_lib_pypy.cffi_tests')
        yield line

def main(cffi_dir):
    cffi_dir = py.path.local(cffi_dir)
    pypydir = py.path.local(__file__).join('..', '..')
    cffi_dest = pypydir.join('..', 'lib_pypy', 'cffi')
    cffi_dest.ensure(dir=1)
    test_dest = pypydir.join('module', 'test_lib_pypy', 'cffi_tests')
    test_dest.ensure(dir=1)
    for p in cffi_dir.join('cffi').visit(fil='*.py'):
        cffi_dest.join('..', p.relto(cffi_dir)).write(p.read())
    for p in cffi_dir.join('testing').visit(fil='*.py'):
        path = test_dest.join(p.relto(cffi_dir.join('testing')))
        path.join('..').ensure(dir=1)
        path.write(''.join(mangle(p.readlines())))

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print __doc__
        sys.exit(2)
    main(sys.argv[1])
