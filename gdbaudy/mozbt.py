# Prototype mozilla backtrace functionality that unifies JavaScript and C++
#  call stacks.
#
# Andrew Sutherland <asutherland@asutherland.org>

# Depends on our bt.py, which is GPL 3 or later, so we are GPL 3 or later.

import gdb, gdb.backtrace
from gdb.FrameIterator import FrameIterator
import gdbaudy.bt as gbt
import itertools
import os.path

import mozilla.js as mjs

pout = gbt.pout

def get_func_block(funcName):
    # this is a completely and utterly ridiculous thing to have to do...
    pc = gdb.decode_line(funcName)[1][0].pc
    return gdb.block_for_pc(pc)

def norm_js_path(path):
    # pass chrome paths through intact
    if path.startswith("chrome://"):
        return path
    # eat the beginning of file URLs
    if path.startswith("file://"):
        path = path[7:]
    return os.path.split(path)[1]

def get_func_name_from_atom(p_atom):
    if p_atom == 0:
        return '<unnamed function>'
    atom_ptr = mjs.JSAtomPtr(p_atom)
    # eat the quotes
    return atom_ptr.summary()[1:-1]

class JSFrame(object):
    JSFRAME_FUNCTION = 0x2

    '''
    Represents a javascript stack frame.
    '''
    def __init__(self, fp, pc):
        self.pc = pc
        frame = fp.dereference()
        flags = int(frame['flags_'])

        if flags & self.JSFRAME_FUNCTION:
            p_fun = frame['exec']['fun']
            fun = p_fun.dereference()
            p_script = fun['u']['i']['script'];
            p_atom = fun['atom']
            self.func_name = get_func_name_from_atom(p_atom)
        else:
            p_script = frame['exec']['script'];
            self.func_name = '<anon>'
            
        if p_script:
            script = p_script.dereference()
            self.filename = script['filename'].string()
            self.line = int(script['lineno'])
        else:
            self.filename = '<none>'
            self.line = 0

PTR_TYPE = gdb.lookup_type("void").pointer()

class JSScratchContext(object):
    '''
    Corresponds to a JSContext.

    Because the state of a JSContext changes with control-flow, we need to
    reconstruct its state during our traversal of the call stack.
    '''
    def __init__(self, p_cx):
        '''
        Suck up the current state of the context (that we care about).
        '''
        cx = p_cx.dereference()
        p_regs = cx['regs']
        if p_regs:
            self.regs = p_regs.dereference()
        else:
            self.regs = None
            return
        
        self.fp = self.regs['fp']
        self.pc = self.regs['pc']
        self.currentSegment = cx['currentSegment'].dereference()

    def restoreSegment(self):
        p_prevSegment = self.currentSegment['previousInContext']
        if not p_prevSegment:
            raise Exception('Tried to restore nonexistent segment')

        self.currentSegment = p_prevSegment.dereference()
        self.regs = self.currentSegment['suspendedRegs'].dereference()
        self.fp = self.regs['fp']
        self.pc = self.regs['pc']

    def hackRestore(self):
        '''
        Hack to deal with XPCJSContextStack where it has saved the frame off
        heuristically rather than properly maintaining that as its own context
        construct.
        '''
        # if we have no fp, then restore
        if self.fp == 0:
            self.restoreSegment()

    def popUntilFrame(self, syn_frames, stop_at_fp):
        '''
        Keep popping frames off this context (and generating synthetic frames)
        until we find a frame whose address is in the range defined by bp and
        prev_bp.  bp > prev_bp
        '''
        #print 'bp: %x prev_bp: %x' % (bp, prev_bp)
        done = False
        if self.fp == 0:
            #print '  @@ compelling restore based on heuristic'
            self.restoreSegment()

        cur_pc = self.pc
        while not done:
            if self.fp == 0:
                #raise Exception('We should have a frame!')
                self.restoreSegment()
            #print 'fp: %x' % (self.fp,)
            jsframe = JSFrame(self.fp, cur_pc)
            # ignore dummy native frames
            if cur_pc:
                syn_frames.append(jsframe)
            done = self.fp == stop_at_fp

            # each frame stores the pc of its previous frame...
            cur_pc = self.fp.dereference()['prevpc_']
            #print 'fp: ', self.fp, 'term fp', stop_at_fp, 'done', done
            self.fp = self.fp.dereference()['prev_']

class JSFrameHelper(object):

    def __init__(self):
        self._initialized = False

    def _chew_context_list(self, contextList):
        '''
        Given a JSCList contextList of JSContexts, create a JSScratchContext
        instance corresponding to each context and store them indexed by their
        memory address.

        @param contextList the JSCList belonging to a JSRuntime
        '''
        self.contexts = {}

        cur = contextList
        contextListAddr = contextList.address
        while True:
            # get the context
            # force the address into integer space through being a string
            #  since gdb.Value won't let us directly coerce
            context_addr = int(str(cur.address), 16) - self.jscontext_link_offset
            self.contexts[context_addr] = JSScratchContext(context_addr)
            cur = cur['next'].dereference()
            if cur.address == contextListAddr:
                break

    def setup(self):
        if not self._initialized:
            self.jsinterp = get_func_block("js::Interpret")
            self.jsexec = get_func_block("js::Execute")
            # js::Invoke setups up the frame for RunScript, so we use RunScript
            self.jsinvoke = get_func_block("js::RunScript")

            self.xpcmethod = get_func_block("XPC_WN_CallMethod")

            self._initialized = True

        #contextList = gdb.parse_and_eval(
        #    "nsXPConnect::gSelf->mRuntime->mJSRuntime->contextList")
        #self._chew_context_list(contextList)

        self.contexts = {}

    def _get_scx_for_frame(self, frame):
        p_cx = frame.read_var("cx")
        cx_addr = str(p_cx.cast(PTR_TYPE))
        if not cx_addr in self.contexts:
            self.contexts[cx_addr] = JSScratchContext(p_cx)
        return self.contexts[cx_addr]

    def process_frame(self, frame):
        syn_frames = []
        show_me = True

        pc = frame.pc()
        next_frame = frame.newer()

        # Hide the interpreter frame; it had to come in via invoke or execute,
        #  and it will do the popping for us...
        if pc >= self.jsinterp.start and pc <= self.jsinterp.end:
            show_me = False

        elif pc >= self.jsinvoke.start and pc <= self.jsinvoke.end:
            fp = frame.read_var("fp")
            #print '*** invoke', flags
            scx = self._get_scx_for_frame(frame)
            # there is a locale frame variable, pop until we get to it
            scx.popUntilFrame(syn_frames, fp)
            show_me = False
            
            # this dude just links his frame in.
            pass

        elif pc >= self.jsexec.start and pc <= self.jsexec.end:
            #print '*** exec'
            scx = self._get_scx_for_frame(frame)
            fp = frame.read_var("prev")
            # there is a local 'frame' variable
            scx.popUntilFrame(syn_frames, fp)
            show_me = False
            
            # js_Execute pushes a new segment; we need to restore when we see it
            scx.restoreSegment()
        elif (pc >= self.xpcmethod.start and
                  pc <= self.xpcmethod.end):
            #print '*** xpc method'
            # we should probably be traversing the XPCJSContextStack
            #  concurrently
            ### trying out heuristics...
            ##scx = self._get_scx_for_frame(frame)
            ##scx.hackRestore()

            show_me = False
        # consider suppressing this dude for reasons of boring-osity
        else:
            func_name = frame.name()
            if not func_name:
                pass
            # JS internal APIs are not interesting
            elif (func_name.startswith("JS_") or
                  func_name.startswith("js_") or
                  func_name.startswith("js::")):
                show_me = False
            # XPConnect internals are not interesting
            elif (func_name.startswith("XPC") or
                  func_name.startswith("xpc_") or
                  func_name.startswith("nsXPCWrapped") or
                  func_name.startswith("CallMethodHelper::")):
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
    iterFrames = FrameIterator (gdb.newest_frame())
    if filter:
        iterFrames = gdb.backtrace.create_frame_filter (iterFrames)

        # Now wrap in an iterator that numbers the frames.
        iterFrames = itertools.izip (itertools.count (0), iterFrames)

        for iFrame, gdbFrame in iterFrames:
            #print '===== ', iFrame
            frames.append(gbt.ColorFrameWrapper(gdbFrame, context, iFrame))
        context.process()

        # Extract sub-range user wants.
        iterFrames = itertools.izip (itertools.count (0), iter(frames))

        # zero it...
        pout.i(-100)
        for pair in iterFrames:
            pair[1].describe (pair[0], gbt.MODE_NORMAL, False)

class MozBT(gdb.Command):
    """
    Mozilla Backtrace!
    """
    def __init__ (self):
        gdb.Command.__init__ (self, "mbt", gdb.COMMAND_STACK)

    def invoke (self, arg, from_tty):
        mozbt()
MozBT()
