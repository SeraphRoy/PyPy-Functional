/* This depends on these attributes so that gcc generates a function
   with no code before the asm, and only "blr" after. */
static __attribute__((noinline, optimize("O2")))
void *slp_switch(void *(*save_state)(void*, void*),
                 void *(*restore_state)(void*, void*),
                 void *extra)
{
  void *result;
  __asm__ volatile (
     /* The Stackless version by Kristjan Valur Jonsson,
        ported to s390x by Richard Plangger */

     "lay 15,-64(15)\n"           /* additional stack space to store f8-f15 */
     "stmg 6,15,48(15)\n"

     "std 0,128(15)\n"
     "std 2,136(15)\n"
     "std 4,144(15)\n"
     "std 6,152(15)\n"

     "std 8, 160(15)\n"
     "std 9, 168(15)\n"
     "std 10,176(15)\n"
     "std 11,184(15)\n"
     "std 12,192(15)\n"
     "std 13,200(15)\n"
     "std 14,208(15)\n"
     "std 15,216(15)\n"

     "lgr 10, %[restore_state]\n" /* save 'restore_state' for later */
     "lgr 11, %[extra]\n"         /* save 'extra' for later */
     "lgr 14, %[save_state]\n"    /* move 'save_state' into r14 for branching */
     "lgr 2, 15\n"                /* arg 1: current (old) stack pointer */
     "lgr 3, 11\n"                /* arg 2: extra                       */

     "lay 15,-160(15)\n"          /* create stack frame                 */
     "basr 14, 14\n"              /* call save_state()                  */
     "lay 15, 160(15)\n"          /* destroy stack frame                */

     "cgij 2, 0, 8, zero\n"       /* skip the rest if the return value is null */

     "lgr 15, 2\n"                /* change the stack pointer */

     /* From now on, the stack pointer is modified, but the content of the
        stack is not restored yet.  It contains only garbage here. */
                               /* arg 1: current (new) stack pointer
                                 is already in r2                    */
     "lgr 3, 11\n"             /* arg 2: extra                       */


     "lay 15, -160(15)\n"     /* create temp stack space for callee to use  */
     "basr 14, 10\n"          /* call restore_state()               */
     "lay 15, 160(15)\n"      /* destroy temp stack space           */

     /* The stack's content is now restored. */

     "zero:\n"

     /* Epilogue */
     /* no need */            /* restore stack pointer */

     "ld 0,128(15)\n"
     "ld 2,136(15)\n"
     "ld 4,144(15)\n"
     "ld 6,152(15)\n"

     "ld 8, 160(15)\n"
     "ld 9, 168(15)\n"
     "ld 10,176(15)\n"
     "ld 11,184(15)\n"
     "ld 12,192(15)\n"
     "ld 13,200(15)\n"
     "ld 14,208(15)\n"
     "ld 15,216(15)\n"

     "lmg 6,15,48(15)\n"

     : "=r"(result)         /* output variable: expected to be r2 */
     : [restore_state]"r"(restore_state),       /* input variables */
       [save_state]"r"(save_state),
       [extra]"r"(extra)
  );
  return result;
}
