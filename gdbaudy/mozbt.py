import gdb, gdb.backtrace
from gdb.FrameIterator import FrameIterator
import gdbaudy.bt as gbt
import itertools
import os.path

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

def norm_js_path(path):
    # pass chrome paths through intact
    if path.startswith("chrome://"):
        return path
    # eat the beginning of file URLs
    if path.startswith("file://"):
        path = path[7:]
    return os.path.split(path)[1]

def guestload32(addr):
    return int(gdb.parse_and_eval("*(int *)0x%x" % (addr,)))

def get_js_string_from_atom(atom):
    if atom == 0:
        return '<none>'
    # er, we could pull from the struct but I'm cribbing from
    #  jsstack.emt at this point since we really should just be using
    #  archer-mozilla...
    flat_str = atom & 0xfffffff8
    str_len = guestload32(flat_str) & 0xff
    str_addr = guestload32(flat_str + 4)
    
    inferior = gdb.inferiors()[0]
    str_data = str(inferior.read_memory(str_addr, str_len))
    return str_data.decode("utf-16")

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
        regs = forceint(getfield(fp, self.frame_regs))
        if regs:
            self.pc = getfield(regs, self.regs_pc)
        else:
            self.pc = 0

        script = forceint(getfield(fp, self.frame_script))
        if script:
            filename_str = getfield(script, self.script_filename)
            self.filename = norm_js_path(filename_str.string())
            self.line = getfield(script, self.script_lineno)
        else:
            self.filename = '<none>'
            self.line = 0

        print 'building frame', self.filename, self.line

        fun = forceint(getfield(fp, self.frame_fun))
        atom = forceint(getfield(fun, self.func_atom))
        self.func_name = get_js_string_from_atom(atom)
        print '  func:', self.func_name

def forceint(blah):
    return int(str(blah), 16)

def getfield(addr, fielddef):
    #print fielddef.type
    #print ':', addr
    evalstr = "(%s) *0x%x" % (fielddef.type,
                              addr + fielddef.bitpos / 8)
    #print 'evaluating', evalstr
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
        self.fp = forceint(getfield(addr, self.cx_fp))
        self.dormantFrameChain = forceint(
            getfield(addr, self.cx_dormantFrameChain))

    def restoreDormantChain(self):
        print '!!! restore chain'
        print '  before fp:', self.fp, 'dormant', self.dormantFrameChain
        self.fp = self.dormantFrameChain
        if self.fp:
            self.dormantFrameChain = forceint(
                getfield(self.fp, self.frame_dormantNext))
        else:
            self.dormantFrameChain = 0
        print '  after fp:', self.fp, 'dormant', self.dormantFrameChain

    def hackRestore(self):
        '''
        Hack to deal with XPCJSContextStack where it has saved the frame off
        heuristically rather than properly maintaining that as its own context
        construct.
        '''
        # if we have no fp, then restore
        if self.fp == 0:
            self.restoreDormantChain()

    def popUntilFrame(self, syn_frames, bp, prev_bp):
        '''
        Keep popping frames off this context (and generating synthetic frames)
        until we find a frame whose address is in the range defined by bp and
        prev_bp.  bp > prev_bp
        '''
        done = False
        while not done:
            if self.fp == 0:
                raise Exception('We should have a frame!')
            syn_frames.append(JSFrame(self.fp))
            done = bp >= self.fp and self.fp >= prev_bp
            print 'fp: ', self.fp, 'bp', bp, 'prev_bp', prev_bp, 'done', done
            self.fp = forceint(getfield(self.fp, self.frame_down))

class JSFrameHelper(object):
    jsinterp = get_func_block("js_Interpret")
    jsexec = get_func_block("js_Execute")
    jsinvoke = get_func_block("js_Invoke")

    xpcmethod = get_func_block("XPC_WN_CallMethod")

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
            flags = frame.read_var("flags")
            print '*** invoke', flags
            scx = self._get_scx_for_frame(frame)
            # there is a locale frame variable, pop until we get to it
            scx.popUntilFrame(syn_frames, bp, prev_bp)
            show_me = False
            
            # this dude just links his frame in.
            pass

        elif pc >= self.jsexec.start and pc <= self.jsexec.end:
            print '*** exec'
            scx = self._get_scx_for_frame(frame)
            # there is a local 'frame' variable
            scx.popUntilFrame(syn_frames, bp, prev_bp)
            show_me = False
            
            # js_Execute diverts the context's previous fp to
            #  cx->dormantFrameChain and cx->dormantFrameChain to
            #  oldfp->dormantNext.  This means that when we encouter js_Execute,
            #  we want to reverse this operation.
            scx.restoreDormantChain()
        elif (pc >= self.xpcmethod.start and
                  pc <= self.xpcmethod.end):
            print '*** xpc method'
            # we should probably be traversing the XPCJSContextStack
            #  concurrently
            scx = self._get_scx_for_frame(frame)
            scx.hackRestore()

            show_me = False
        # consider suppressing this dude for reasons of boring-osity
        else:
            func_name = frame.name()
            if not func_name:
                pass
            # JS internal APIs are not interesting
            elif (func_name.startswith("js_") or
                  func_name.startswith("JS_")):
                show_me = False
            # XPConnect internals are not interesting
            elif (func_name.startswith("XPC") or
                  func_name.startswith("xpc_")):
                show_me = False
            elif (func_name == "PrepareAndDispatch" or
                  func_name == "NS_InvokeByIndex_P"):
                show_me = False
            
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
            print '===== ', iFrame
            frames.append(gbt.ColorFrameWrapper(gdbFrame, context, iFrame))
        context.process()

        # Extract sub-range user wants.
        iterFrames = itertools.izip (itertools.count (0), iter(frames))

        # zero it...
        pout.i(-100)
        for pair in iterFrames:
            pair[1].describe (pair[0], False, False)
