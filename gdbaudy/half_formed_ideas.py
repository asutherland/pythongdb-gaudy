magic_extracted_values = {}
class MagicExtractingBreakpoint(gdb.Breakpoint):
    '''
    This was my first attempt to get the JS stack from DumpJSStack().  The
    bad news is that doing `gdb.execute('call DumpJSStack()', to_string=True)`
    does not work because it throws when the breakpoint gets hit.  Also it was
    sorta dubious, but the general idea for me was that interrupting something
    that definitely cleans up after itself is probably safest.  Our new approach
    may end up leaking memory, at least until rr kills the diverged process,
    which means we can leak with abandon!  Woo!
    '''
    def __init__(self, spec, saveAs, frameIndex, traversal):
        gdb.Breakpoint.__init__(self, spec)

        self.saveAs = saveAs
        self.frameIndex = frameIndex
        self.traversal = traversal

    def stop(self):
        frame = gdb.newest_frame()
        for i in range(self.frameIndex):
            frame = frame.older()

        name, value = magic_capture(self.traversal)

        magic_extracted_values[saveAs] = value

        # keep going
        return False


jsstack_bp = None

def ensure_jsstack_hooked():
    global jsstack_bp
    if jsstack_bp:
        return

    jsstack_bp = MagicExtractingBreakpoint(
                     'DebugDump', 'jsstack', 1, ['buf', 'mTuple', 'mFirstA'])
