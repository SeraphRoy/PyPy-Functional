How to add grammar support for our pattern matching
1. In file pypy / pypy / interpreter / pyparser / data / Grammar2.7, define a new matching statement
test: bunch of 'and' 'or' 'not' tests combined with if else and lambda statement
suite: a simple statement or a new stmt at new line

compound_stmt: if_stmt | while_stmt | for_stmt | try_stmt | with_stmt | funcdef | classdef | decorated | matching_stmt
matching_para: expr | test
matching_stmt: 'match' '(' expr ')' ':' with matching_para ':' expr ( with matching_para ':' expr )*

above grammar is likely to cause ambigious but I am going to fix it later.

2. In file pypy / pypy / interpreter / astcompiler / tools / Python.asdl, add a new node for matching statement and matching_para

// not sure how to handle this one yet
matching_para = Expression(expr body)

stmt = FunctionDef(identifier name, arguments args,
                   stmt* body, expr* decorator_list) 
       | ClassDef(identifier name, expr* bases, stmt* body, expr* decorator_list)
       | For(expr target, expr iter, stmt* body, stmt* orelse)
       | While(expr test, stmt* body, stmt* orelse)
       | Matching( expr target, matching_para* mp, expr* vals )

3. In file pypy / pypy / interpreter / astcompiler / ast.py, define a new class Matching
the new Matching class should contain following functions
* __init__
* walkabout(self, visitor)
* mutate_over(self, visitor)
* to_object(self, space)
* from_object(space, w_node)

// haven't figure out how to do it
// no sure if this code can be auto generate or I have to write it through

4. If everything was implemented correctly, now we have a choice. When we visited Matching node in AST, we can either generate the corresponding byte code or we can repalce the matching node with a set of other existing node that will perform the same functionality.

If we decide to generate the bytecode for matching, we will need to modify the pypy / pypy / interpreter / astcompiler / codegen.py to add the rule for matching.

If we decide to repalce the matching node with other existing nodes, we won't need to deal with the byte code.




PYPY bytecode note   http://doc.pypy.org/en/latest/interpreter.html
*　The major differences between pypy and cpython's bytecode interpreter are the overall usage of the object space indirection to perform operations on objects, and the organization of the built-in modules (described here).
*  Interpreting code objects means instantiating and initializing a Frame class and then calling its frame.eval() method
*  use python dis library to display the bytecode of the python code
*  CPython and PyPy are stack-based virtual machines, i.e. they don’t have registers but instead push object to and pull objects from a stack. 
*  interpreter-level is executed directly on the machine and invoking application-level functions leads to an bytecode interpretation indirection. 
*  Frame Classes holds
    *  the local scope holding name-value bindings
    *  a blockstack containing (nested) information regarding the control flow of a function
    *  a value stack where bytecode interpretation pulls object from and puts results on. 
    *  a reference to the globals dictionary, containing module-level name-value bindings
    *  debugging information
*  Code Class 
    *  they are only immutable representations of source code
    *  contains bunch of variable not very releveant to my work now
*  Function and Method classes
    *  func_name - name of function
    *  func_code - code object
    *  func_globals - reference to the global dictionary 
    *  and something else
*  Arguments Class
    *  parsing arguments passed to functions
    *  positional arguments, keyword arguemnts, str args .......
    *  can get bound to a class or instance in which case the first argument to the underlying function becomes the bound object
    *  




