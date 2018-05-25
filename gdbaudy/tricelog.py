import gdb
import json
import os.path
import re
import sys
import toml
import traceback

from gdb.FrameIterator import FrameIterator

RE_IS_GECKO = re.compile('^(gecko|mozilla)')
def normalize_path(path):
    '''
    Naive (compared to ColorFilteringBacktrace's logic) path normalization logi
    to provide paths relative to the source-tree root.
    '''
    if not path:
        return path

    parts = path.split(os.path.sep)
    for iPart, part in enumerate(parts):
        if RE_IS_GECKO.match(part):
            return os.path.sep.join(parts[iPart+1:])
    return path


# XXX heuristic smart-pointer piercing. in pp this comes from the yaml
# knowledge-base.  Here we just pierce any mRawPtr we see.  Note that gdb seems
# clever enough to pierce smart pointers for its normal syntax, so I'm presuming
# it either has its own heuristics or it leverages the explicit operator* or
# operator-> overloads.  (It's _not_ coming from the gecko pretty printers.)
def maybe_pierce(val):
    try:
        return maybe_deref(val['mRawPtr'])
    except:
        return val

# XXX taken from pp.py where it proved useful, but ideally we don't want this
# in here and instead want to create a pp successor that produces a sort of
# incrementally deepening object graph that can be intelligently flattened to
# JSON or a pretty UI.  See that file for more plans.
def maybe_deref(val, vtype=None):
    """Given a value that may be of a pointer to a struct-ish type, dereference
it if it is.  Also pierce references.  References are so wacky!

For use in pretty-printing container scenarios where the value types are
frequently pointers.  We intentionally avoid de-referencing in cases where we're
dealing with things like char* and de-referencing breaks string printing."""
    if not val:
        return val
    if not vtype:
        vtype = val.type
    if (vtype.code == gdb.TYPE_CODE_PTR or
        vtype.code == gdb.TYPE_CODE_REF):
        derefed = val.referenced_value()
        dtype = derefed.type
        # we may be dealing with typedefs now, for example, PRThread is
        # "typedef struct PRThread PRThread", a common C idiom.
        dtype = dtype.strip_typedefs()
        if (dtype.code == gdb.TYPE_CODE_STRUCT or
            dtype.code == gdb.TYPE_CODE_UNION):
           # TODO: maybe this is too simple?  There is TYPE_CODE_TYPEDEF, which
           # could possibly mean typedefs trick us.
           return derefed
        else:
            #pout('{s}not de-refing to %s from %s', dtype, vtype)
            pass
    elif vtype.name:
        val = maybe_pierce(val)
    return val


# helper to invoke gdb and take the answer post-colon, used for rr commands
def execExtractPostColon(cmd, coerce=int):
    s = gdb.execute(cmd, to_string=True)
    return coerce(s[s.index(':') + 2:])

# helper to invoke "info thread N" and extract the thread name from inside the
# parens.  Might also be used someplace else too someday.
def execExtractInsideParens(cmd):
    s = gdb.execute(cmd, to_string=True)
    idxOpen = s.index('(')
    idxClose = s.index(')', idxOpen + 1)
    return s[idxOpen+1:idxClose]


def magic_capture(traverseSeq, verbose=False, stringify=True):
    '''
    Capture helper that takes a list of fields to traverse, stringifying the
    final value when all fields have been traversed.

    traversSeq can either entirely consist of string values, starting from the
    implicit frame context.  Alternately, the first element in the sequence
    can be:
    - A gdb.Value which will be used as the starting value.
    - A gdb.Frame which will be used as the starting value with the next string
      being passed to read_var before resuming normal traversal.
    '''
    try:
        # traversal is currently hackily derived from the "pp" command's
        # traverse logic.  This all wants to be cleaned up.
        thing = traverseSeq[0]
        traverseFrom = 1

        if isinstance(thing, gdb.Value):
            cur = thing
            name = '(value)'
        elif isinstance(thing, gdb.Frame):
            cur = thing.read_var(traverseSeq[1])
            name = '(frame)'
            traverseFrom = 2
        else:
            name = thing
            cur = gdb.parse_and_eval(thing)
        cur = maybe_deref(cur)

        # figure out the type; we want to use RTTI if available to downcast all
        # the way.
        vtype = cur.dynamic_type
        # that may have given us a better type, let's re-cast the value too.
        try:
            cur = cur.cast(vtype)
        except:
            pass

        for thing in traverseSeq[traverseFrom:]:
            name += '.' + thing
            cur = cur[thing]
            # (don't try and dereference a null pointer)
            if not cur:
                break

            cur = maybe_deref(cur)

            # figure out the type; we want to use RTTI if available to downcast all
            # the way.
            vtype = cur.dynamic_type
            # that may have given us a better type, let's re-cast the value too.
            try:
                cur = cur.cast(vtype)
                cur = maybe_deref(cur)
            except:
                pass

        if stringify:
            # Note that this is different than doing value.string() which is
            # only for actual strings.  By doing str(), we'll actually get
            # `0xPOINTER "string contents"`.
            cur = str(cur)
        return name, cur
    except:
        if verbose:
            print('problem evaluating traversal:')
            traceback.print_exc()
        return None, None

class LoggingBreakpoint(gdb.Breakpoint):
    def __init__(self, owner, info):
        gdb.Breakpoint.__init__(self, info['spec'])
        self.owner = owner
        self.info = info

    def _gather_data(self):
        data = {}

        ## RR replay sourced info
        data['event'] = execExtractPostColon('when')
        data['tick'] = execExtractPostColon('when-ticks')
        data['tid'] = execExtractPostColon('when-tid')
        data['time'] = execExtractPostColon('elapsed-time', float)

        ## get the thread name.
        # This is a little circumspect because the InferiorThread.name currently
        # isn't populated by rr, but it is provided as the thread's "extra".
        # And we can use "info thread" to retrieve it.
        tnum = gdb.selected_thread().num
        data['tname'] = execExtractInsideParens('info thread ' + str(tnum))

        data['spec'] = self.info['spec']

        if self.info.get('capture'):
            captured = data['captured'] = {}
            for traverseSeq in self.info['capture']:
                name, value = magic_capture(traverseSeq)
                if name is not None:
                    captured[name] = value

        if self.info.get('stack'):
            frames = data['stack'] = []
            for frame in FrameIterator(gdb.newest_frame()):
                sal = frame.find_sal()
                name = frame.name()

                if not name or (not sal.symtab or not sal.symtab.filename):
                    lib = gdb.solib_name(frame.pc())

                frames.append({
                    'name' : name,
                    'file': sal.symtab and normalize_path(sal.symtab.filename),
                    'line' : sal.line,
                    })

        if self.info.get('jsstack'):
            data['jsstack'] = capture_js_stack()

        return data

    def stop(self):
        if self.owner.ofile:
            data = self._gather_data()
            json.dump(data, self.owner.ofile)
            self.owner.ofile.write('\n')

        # keep going!
        return False

class TriceLogCommand(gdb.Command):
    def __init__(self):
        gdb.Command.__init__(self, 'tricelog', gdb.COMMAND_NONE)

        self.breakpoints = []
        self.ofile = None

    def load_config(self, name):
        data = None
        path = os.path.join(os.path.dirname(__file__), 'trice-' + name + '.toml')
        with open(path) as f:
            data = toml.load(f)

        # automatically open a log file if one's not open, based on the config.
        if data.get('default_log_prefix') and not self.ofile:
            # rr ptid's look like (12481, 12481, 0)
            use_pid = gdb.selected_thread().ptid[0]
            self.open_log('{}-{}.json'.format(
                          data['default_log_prefix'], use_pid))

        # set up breakpoints
        for funcName, info in data['trace'].items():
            info['spec'] = funcName
            bp = LoggingBreakpoint(self, info)
            bp.enabled = True
            self.breakpoints.append(bp)

    def open_log(self, name):
        self.close_log()

        path = os.path.join('/tmp', name)
        self.ofile = open(path, 'w')
        print('opened', path, 'for logging')

    def close_log(self):
        if self.ofile:
            self.ofile.close()
            self.ofile = None

    def invoke(self, arg, from_tty):
        args = gdb.string_to_argv(arg)
        if len(args):
            cmd = args[0]
        else:
            cmd = help

        if cmd == 'load':
            self.load_config(args[1])
            if self.ofile is None:
                print("Loaded config, don't forget to use 'logto'!")
        elif cmd == 'logto':
            self.open_log(args[1])
        elif cmd == 'closelog':
            self.close_log()
        else:
            print('Commands are: load, logto, closelog')

tlc = TriceLogCommand()

# Tool to help test the capture logic manually.  The capture logic is a cut down
# version of pp.py that aspires to be fancier.
class TriceCaptureTester(gdb.Command):
    def __init__(self):
        gdb.Command.__init__(self, 'tcaptest', gdb.COMMAND_NONE)

    def invoke(self, arg, from_tty):
        args = eval(arg)
        print('traversal is:', repr(args))
        print(repr(magic_capture(args, True)))

tct = TriceCaptureTester()

def capture_js_stack(verbose=False):
    '''
    Hacky JS Stack capturing built around dubiously using gdb's function
    call magic.  The main reason this is hacky is that we need to invoke
    JS::FormatStackDump which takes a JS::UniqueChars via move reference.  All
    the notable callers pass a nullptr which gets default constructed and then
    the callee can ignore it.  To make gdb happy we have to provide a pointer to
    a memory location with a null pointer.

    We can't just use DumpJSStack() because:
    - Currently it only uses a 2k internal buffer.  (A template avoids stack
      corruption; one just ends up with truncastion.)
    - The bigger issue is our inability to easily intercept the stdio.  There
      may be a way to do it, but we're also assuming that rr is involved, and
      GDB itself doesn't seem to have an easy way to address the problem even
      if rr wasn't wrapping.  (rr doesn't seem to have an existing gdb pipe for
      this info, but it's possible one can be added).
    - Using a breakpoint inside a call we issue doesn't work because
      gdb.parse_and_eval will throw an exception when the breakpoint triggers,
      so just intercepting DumpJSStack doesn't work as an automated process.
      Note that I haven't looked deeply into whether there are flags that can
      be used here to avoid this behavior, but from a what's-on-the-stack
      invariant, the nested breakpoint fundamentally is re-entrancy/a nested
      event loop, which is obviously not appealing to most run-times.
    '''
    # get the JSContext
    try:
        if not gdb.parse_and_eval('nsContentUtils::sInitialized'):
            return None
        jscx = gdb.parse_and_eval('nsContentUtils::GetCurrentJSContext()')
        # It's possible there's no
        if jscx + 0 == 0:
            return None
    except:
        return None

    # create an empty JS::UniqueChars zeroed allocation.
    ucSize = gdb.parse_and_eval('sizeof(JS::UniqueChars)')
    emptyUniqueChars = gdb.parse_and_eval('calloc(' + str(ucSize) + ', 1)')

    # dump the stack
    # TODO: not leak this.  We just need to pierce to the pointer and free it.
    try:
        # the booleans are: showArgs, showLocals, showThisProps.  Unfortunately,
        # there's no truncation of the arguments, so like one can end up with
        # the massive blocklist JSON in there.  While it's possible to
        # post-process so we don't log the whole thing, the reality is that
        # there is a performance cost to grabbing the args, so for now we're
        # going to just disable them.
        jsstack = gdb.parse_and_eval(
                      'JS::FormatStackDump((JSContext*)' + str(jscx) + ', ' +
                      '*(JS::UniqueChars *)' + str(emptyUniqueChars) + ', ' +
                      'false, false, false)')
    except:
        if verbose:
            print('problem getting JS stack:')
            traceback.print_exc()
        jsstack = None

    # free the allocated memory
    gdb.parse_and_eval('free((void *)' + str(emptyUniqueChars) + ')')

    if not jsstack:
        return jsstack

    # extract the string
    name, stackstr = magic_capture([jsstack, 'mTuple', 'mFirstA'],
                                   verbose=False, stringify=False)
    return stackstr.string()


class PrettyJSStack(gdb.Command):
    def __init__(self):
        gdb.Command.__init__(self, 'pjsstack', gdb.COMMAND_NONE)

    def invoke(self, arg, from_tty):
        stackstr = capture_js_stack()
        print(stackstr)

PrettyJSStack()
