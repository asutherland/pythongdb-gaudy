import gdb, gdb.backtrace
from gdb.FrameIterator import FrameIterator
import gdbaudy.bt as gbt
import itertools

#import mozilla.js as mjs

pout = gbt.pout

def get_func_block(funcName):
    return gdb.lookup_symbol(funcName)[0].value

def get_field_def(typeName, fieldName):
    gdbtype = gdb.lookup_type(typeName)
    if gdbtype is None:
        raise Exception("Unable to locate type: " + typeName)
    for field in gdbtype.fields():
        if field.name == fieldName:
            return field
    raise Exception("Unable to locate field '%s' in type '%s'" %
                    (typeName, fieldName))

def offset(typeName, fieldName):
    return int(get_field_def(typeName, fieldName).bitpos) / 8

class JSFrame(object):
    frame_regs = get_field_def("JSStackFrame", "regs")
    regs_pc = get_field_def("JSFrameRegs", "pc")
    frame_script = get_field_def("JSStackFrame", "script")
    script_filename = get_field_def("JSScript", "filename")
    script_lineno = get_field_def("JSScript", "lineno")
    frame_fun = get_field_def("JSStackFrame", "fun")
    func_atom = get_field_def("JSFunction", "atom")
    '''
    Represents a javascript stack frame.
    '''
    def __init__(self, fp):
        self.pc = getfield(getfield(fp, self.frame_regs), self.regs_pc)

        script = getfield(fp, self.frame_script)
        filename_str = getfield(script, self.script_filename)

        self.filename = filename_str.string()
        self.line = getfield(script, self.script_lineno)

        print 'building frame', self.filename, self.line

        #fun = getfield(fp, self.frame_fun)
        #atom = mjs.AtomicjsvalPunPrinter(getfield(fun, self.func_atom))
        #self.func_name = atom.content_string()
        self.func_name = 'punted'
        print '  func:', self.func_name

def getfield(addr, fielddef):
    evalstr = "(%s) *0x%x" % (fielddef.type,
                              addr + fielddef.bitpos / 8)
    print 'evaluating', evalstr, addr, fielddef
    return gdb.parse_and_eval(evalstr)

class JSScratchContext(object):
    cx_fp = get_field_def("JSContext", "fp")
    cx_dormantFrameChain = get_field_def("JSContext", "dormantFrameChain")
    frame_dormantNext = get_field_def("JSStackFrame", "dormantNext")
    frame_down = get_field_def("JSStackFrame", "down")

    '''
    Because the state of a JSContext changes with control-flow, we need to
    reconstruct its state during our traversal of the call stack.
    '''
    def __init__(self, addr):
        '''
        Suck up the current state of the context (that we care about).
        '''
        self.fp = getfield(addr, self.cx_fp)
        self.dormantFrameChain = getfield(addr, self.cx_dormantFrameChain)

    def restoreDormantChain(self):
        self.fp = self.dormantFrameChain
        self.dormantFrameChain = getfield(self.fp, self.frame_dormantNext)

    def popUntilFrame(self, syn_frames, bp, prev_bp):
        '''
        Keep popping frames off this context (and generating synthetic frames)
        until we find a frame whose address is in the range defined by bp and
        prev_bp.  bp > prev_bp
        '''
        while self.fp > bp or prev_bp > self.fp:
            if self.fp == 0:
                raise Exception('We should have a frame!')
            syn_frames.append(JSFrame(self.fp))
            self.fp = getfield(self.fp, self.frame_down)

class JSFrameHelper(object):
    jsinterp = get_func_block("js_Interpret")
    jsexec = get_func_block("js_Execute")
    jsinvoke = get_func_block("js_Invoke")

    jscontext_link_offset = offset("JSContext", "link")

    def __init__(self):
        pass

    def _chew_context_list(self, contextList):
        '''
        Given a JSCList contextList of JSContexts,

        @param contextList the JSCList belonging to a JSRuntime
        '''
        self.contexts = {}

        cur = contextList
        contextListAddr = contextList.address
        while cur['next'] != contextListAddr:
            # get the context
            # force the address into integer space through being a string
            #  since gdb.Value won't let us directly coerce
            context_addr = int(str(cur.address), 16) - self.jscontext_link_offset
            self.contexts[context_addr] = JSScratchContext(context_addr)
            cur = cur['next'].dereference()

    def setup(self):
        contextList = gdb.parse_and_eval(
            "nsXPConnect::gSelf->mRuntime->mJSRuntime->contextList")
        self._chew_context_list(contextList)

    def _get_bp_from_frame(self, frame):
        # ugly attempt to get the bp... we can only get the stack_addr from the
        #  frame_id by stringifying it and hacking it out.  Then, stack_addr
        #  is defined to basically be our stack frame before IP and BP are
        #  pushed on, so we need to add 2 words to actually get the bp
        frame_str = str(frame)
        # len("{stack=")
        return int(frame_str[7:frame_str.find(",")], 16) - 8

    def _get_scx_for_frame(self, frame):
        cx_addr = int(str(frame.read_var("cx")), 16)
        return self.contexts[cx_addr]

    def process_frame(self, frame):
        syn_frames = []
        show_me = True

        pc = frame.pc()
        bp = self._get_bp_from_frame(frame)
        next_frame = frame.newer()
        if next_frame:
            prev_bp = self._get_bp_from_frame(next_frame)
        else:
            # uh, if we had a way to get the stack pointer we could be smart
            #  about this, but nothing in the python API is helping right now,
            #  so let's just assume we have 8k of stack used.
            # (we could probably walk the symbols for this block to do a better
            #  guess, but I don't super-care. right now)
            prev_bp = bp - 8192

        # Hide the interpreter frame; it had to come in via invoke or execute,
        #  and it will do the popping for us...
        if pc >= self.jsinterp.start and pc <= self.jsinterp.end:
            show_me = False

        elif pc >= self.jsinvoke.start and pc <= self.jsinvoke.end:
            scx = self._get_scx_for_frame(frame)
            # there is a locale frame variable, pop until we get to it
            scx.popUntilFrame(syn_frames, bp, prev_bp)
            show_me = False
            
            # this dude just links his frame in.
            pass

        elif pc >= self.jsexec.start and pc <= self.jsexec.end:
            cx = frame.read_var("cx")
            # there is a local 'frame' variable
            scx.popUntilFrame(syn_frames, bp, prev_bp)
            show_me = False
            
            # js_Execute diverts the context's previous fp to
            #  cx->dormantFrameChain and cx->dormantFrameChain to
            #  oldfp->dormantNext.  This means that when we encouter js_Execute,
            #  we want to reverse this operation.
            scx.restoreDormantChain()

        return syn_frames, show_me
            

jsfh = JSFrameHelper()

FRAME_HELPERS = [jsfh]

def mozbt():
    context = gbt.ContextHelper(FRAME_HELPERS)

    frames = []
    iterFrames = FrameIterator (gdb.selected_thread().newest_frame())
    if filter:
        iterFrames = gdb.backtrace.create_frame_filter (iterFrames)

        # Now wrap in an iterator that numbers the frames.
        iterFrames = itertools.izip (itertools.count (0), iterFrames)

        for iFrame, gdbFrame in iterFrames:
            frames.append(gbt.ColorFrameWrapper(gdbFrame, context, iFrame))
        context.process()

        # Extract sub-range user wants.
        iterFrames = itertools.izip (itertools.count (0), iter(frames))
        if count < 0:
            iterFrames = self.final_n (iterFrames, count)
        elif count > 0:
            iterFrames = itertools.islice (iterFrames, 0, count)

        # zero it...
        pout.i(-100)
        for pair in iterFrames:
            pair[1].describe (pair[0], False)
