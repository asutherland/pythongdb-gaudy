import gdb
import json
import os.path
import sys
import toml
import traceback


# XXX heuristic smart-pointer piercing. in pp this comes from the yaml
# knowledge-base.  Here we just pierce any mRawPtr we see.  So fancy!
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


def magic_capture(traverseSeq, verbose=False):
    try:
        # traversal is currently hackily derived from the "pp" command's
        # traverse logic.  This all wants to be cleaned up.
        name = traverseSeq[0]

        cur = gdb.parse_and_eval(name)
        cur = maybe_deref(cur)

        # figure out the type; we want to use RTTI if available to downcast all
        # the way.
        vtype = cur.dynamic_type
        # that may have given us a better type, let's re-cast the value too.
        try:
            cur = cur.cast(vtype)
        except:
            pass

        for thing in traverseSeq[1:]:
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


        return name, str(cur)
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
        data['_event'] = execExtractPostColon('when')
        data['_tick'] = execExtractPostColon('when-ticks')
        data['_tid'] = execExtractPostColon('when-tid')
        data['_time'] = execExtractPostColon('elapsed-time', float)

        ## get the thread name.
        # This is a little circumspect because the InferiorThread.name currently
        # isn't populated by rr, but it is provided as the thread's "extra".
        # And we can use "info thread" to retrieve it.
        tnum = gdb.selected_thread().num
        data['_tname'] = execExtractInsideParens('info thread ' + str(tnum))

        data['_spec'] = self.info['spec']

        if self.info.get('capture'):
            for traverseSeq in self.info['capture']:
                name, value = magic_capture(traverseSeq)
                if name is not None:
                    data[name] = value

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

        for funcName, info in data['trace'].items():
            info['spec'] = funcName
            bp = LoggingBreakpoint(self, info)
            bp.enabled = True
            self.breakpoints.append(bp)

    def open_log(self, name):
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

TriceLogCommand()

# Tool to help test the capture logic manually.  The capture logic is a cut down
# version of pp.py that aspires to be fancier.
class TriceCaptureTester(gdb.Command):
    def __init__(self):
        gdb.Command.__init__(self, 'tcaptest', gdb.COMMAND_NONE)

    def invoke(self, arg, from_tty):
        args = eval(arg)
        print('traversal is:', repr(args))
        print(repr(magic_capture(args, True)))

TriceCaptureTester()
