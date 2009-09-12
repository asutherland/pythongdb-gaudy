import gdb

class PrettyPrintCommand(gdb.Command):
    def __init__(self):
        gdb.Command.__init__(self, "pp", gdb.COMMAND_NONE)

    def invoke(self, arg, from_tty):
        val = gdb.parse_and_eval(arg)
        vis = gdb.default_visualizer(val)
        if vis:
            print vis.to_string()
        else:
            print "Don't know how to pretty-print", val

PrettyPrintCommand()
