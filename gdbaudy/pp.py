import gdb
import os.path
import re

from strictyaml import load
from pyflam import *

RE_TEMPLATE_NAME = re.compile("^([^<]+)<.*>$")

class PrettyPrintCommand(gdb.Command):
    """A prettier version of the gdb "print" command that supports its own
YAML-defined pretty printer definitions in addition to Python-implemented pretty
printers.

The pretty-print wheel is re-implemented because normal pretty-printers are:
- A bit of a hassle to write.
- Biased towards flattening everything to strings suitable for terminal
  display.  The children() and display_hint() mechanism is inspired and allows
  for rich objects to be richly decomposed, but given the hassle, it's tempting
  to distill a complicated object down to a simple string or not at all.

Additionally, the default print logic and heuristics:
- Are too chatty and naive.  For example, static members are frequently boring
  constants.  However, they're also commonly super-important singletons.
  Turning them all off can be ill-advised.

Our driving goals, aware of the above are then:
- Support pretty colorized output at the terminal, necessitating a custom
  command.
- Support JSON output, quickly serializing a data structure to disk for later
  consultation, or as part of semi-automated analysis of an rr-trace, etc.
- Support hybrid HTML/JSON debug logs that can be shared and viewed as pretty
  HTML, but also contain the rich JSON representation that can be analyzed using
  in-page JS tooling or extracted for external use, etc.
- Make it trivially easy to characterize what the most interesting fields of a
  class are in an easily shared YAML configuration file.
- Make it easy to define additional tagged groups of fields in a class that can
  be toggled on/off as based on persistent or session-related interests.  For
  example, omnibus classes like nsGlobalWindow have a lot going on and it's rare
  to care about all of it at once.  Additionally, these groups can be inferred
  based on tags placed on the types of the members.
"""
    def __init__(self):
        gdb.Command.__init__(self, "pp", gdb.COMMAND_NONE)

        config_path = os.path.join(os.path.dirname(__file__), "pp-mozilla.yaml")
        with open(config_path, "r") as f:
            # This recursively flattens the YAML instances to dicts and lists
            # and scalars.
            self.mapping = load(f.read()).data

    ### Log functions
    # These are for both debugging and to let us separate out the actual
    # presentation details.  This is all stream-parsing inspired.  Specific
    # calls are made when entering and exiting a nested representation.
    def _log_traverse(self, val, rule, tname, steps):
        pout.v("{s}Traversing %s %s using steps %s",
               rule["kind"], tname, repr(steps))


    def _log_enter_array(self, val, rule, tname):
        pout.v("{s}Traversing %s %s", rule["kind"], tname)
        pout.i(2)

    def _log_item_in_array(self, i, val):
        self._inspect(val)

    def _log_exit_array(self, val, rule, tname):
        pout.i(-2)

    def _log_terse_object(self, val, rule, tname, fieldDefs):
        fmtbits = []
        fmtvals = []
        # fieldDefs is a list of singleton dictionaries.
        for fieldDef in fieldDefs:
            for fieldName, displayMode in fieldDef.items():
                fmtvals.append(fieldName)
                try:
                    # XXX for now, just coerce to a string, assuming it's
                    # something simple.  Still need to make some higher level
                    # decisions.
                    fmtvals.append(str(val[fieldName]))
                    fmtbits.append('{k}%s: {v}%s')
                except e:
                    pout('{e}Error displaying field {n}%s {e}stack:\n{s}%s',
                         fieldName, e);
                    fmtvals.append('{k}%s: {e}Error')
        pout(' '.join(fmtbits), *fmtvals)

    def _log_enter_detailed_object(self, val, rule, tname):
        pout("{s}Entering {n}%s", tname)
        pout.i(2)

    def _log_exit_detailed_object(self, val, rule, tname):
        pout.i(-2)

    def _traverse(self, val, rule, tname):
        steps = rule["traverse"]
        self._log_traverse(val, rule, tname, steps)

        cur = val
        for step in steps:
            # (really only for the base-case)
            if not cur:
                break
            cur = cur[step]
            ctype = cur.type
            # (don't try and dereference a null pointer)
            if not cur:
                break

            # Dereference the pointer as we step through it.  We can't do
            # anything with a pointer type.  We probably do want to further log
            # this traversal, however.
            if ctype.code == gdb.TYPE_CODE_PTR:
                cur = cur.dereference()

        self._inspect(cur)

    def _iterate(self, val, rule, tname):
        irule = rule["iterate"]
        if "sentinel" in irule:
            # Linked List using a sentinel!

            # extract the field names and type info
            sentinel_name = irule["sentinel"]
            advance_name = irule["advance"]
            t_ptr_type = val.type.template_argument(0).pointer()

            # save off the sentinel's address so we know when we've looped back
            # around to it.
            sentinel = val[sentinel_name]
            pSentinel = sentinel.address

            self._log_enter_array(val, rule, tname)

            try:
                # now walk until we loop
                pNext = sentinel[advance_name]
                i = 0
                while pSentinel != pNext:
                    list_elem = pNext.dereference()
                    list_value = pNext.cast(t_ptr_type)
                    self._log_item_in_array(i, list_value.dereference())
                    pNext = list_elem[advance_name]
                    i += 1
            finally:
                self._log_exit_array(val, rule, tname)

    def _print_terse(self, val, rule, tname):
        # XXX need to figure out the UX of this a bit more.  It seems like
        # perhaps the best course of action is to iterate over the fields,
        # determining they are simple/complex as we go.  If they are simple,
        # we can do some line continuations.  If we hit a complex one, we flush
        # and let it do its rich inspection.
        # XXX we also want to understand whether we're operating in a context
        # where our type name needs to be displayed or not.  This is a function
        # of whether we're part of a homogeneous collection and how verbose our
        # display is, etc.  Arguably that specific call wants to be done by the
        # log stream, since JSON cases possibly want all the detail even if it's
        # excessive.
        self._log_terse_object(val, rule, tname, rule["terse"])

    def _inspect(self, val):
        # figure out the type.
        vtype = val.type
        #pout("vtype: %s %s %s %s", vtype, type(vtype), vtype.tag, type(vtype.tag))
        tmatch = RE_TEMPLATE_NAME.match(vtype.name)
        if tmatch:
            # it was a template!
            tname = tmatch.group(1)
        else:
            tname = vtype.name

        # check if we have a configuration mapping for the type
        #pout("looking up %s in...", tname)
        #pout("%s", repr(self.mapping))
        rule = self.mapping.get(tname)
        if rule:
            # if this was an alias, pierce it
            if isinstance(rule, str):
                rule = self.mapping.get(rule)
            #pout("Found mapping with kind %s", rule.get("kind", "default"))
            if "traverse" in rule:
                self._traverse(val, rule, tname)
            elif "iterate" in rule:
                self._iterate(val, rule, tname)
            elif "terse" in rule:
                self._print_terse(val, rule, tname)
            else:
                pout("{e}Don't understand rule {n}%s {e}for type {n}%s",
                     repr(rule), tname)
            # Handled or errored appropriately, no need to fall through to the
            # default visualizer.
            return
        else:
            pout("{s}No mapping for %s", tname)

        vis = gdb.default_visualizer(val)
        #pout("using default type: %s code: %s tag: %s", val.type.name, val.type.code, val.type.tag)
        if vis:
            pout("{n}%s", vis.to_string())
        else:
            pout("Don't know how to pretty-print %s", val.to_string())

    def invoke(self, arg, from_tty):
        val = gdb.parse_and_eval(arg)
        self._inspect(val)


PrettyPrintCommand()
