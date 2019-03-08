import gdb
import os.path
import re

from strictyaml import load
from pyflam import *

RE_TEMPLATE_NAME = re.compile("^([^<]+)<.*>$")

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
    return val

def is_simple_type(vtype):
    """Return true if the type is a simple native type or a typedef to one.  This
is mainly used to figure out whether a type merits naming it or not, and perhaps
whether it can/should be displayed inline.
"""
    vtype = vtype.strip_typedefs()

    code = vtype.code
    return (code == gdb.TYPE_CODE_INT or
            code == gdb.TYPE_CODE_FLT or
            code == gdb.TYPE_CODE_BOOL)

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


    def _log_bitflag_bits(self, raw_val, bit_pieces, tname):
        pout("{n}%s {s}%x", ' '.join(bit_pieces), raw_val)

    def _log_enter_array(self, val, tname):
        # XXX We're in a weird place here for the identifying type when it comes
        # to "std::map".  The types we perceive may be way too complicated (due
        # to it including the comparator, the allocation, and redundant type
        # info, so we need extra magic.  (So tname is just "std::map" and
        # val.type is the super complicated thing.  We can definitely add
        # heuristics, if only to build on top of the stdc++ lib's pretty
        # printers)
        pout("{n}%s {s}%x", tname, val.address)
        pout.i(2)

    def _log_item_in_array(self, i, val):
        self._inspect(val)

    def _log_exit_array(self, val, tname):
        pout.i(-2)

    def _log_enter_map(self, val, tname):
        pout("{n}%s {s}%x", tname, val.address)
        pout.i(2)

    def _log_item_in_map(self, key, val):
        pout("{k}%s{n}:", key)
        pout.i(2)
        try:
            self._inspect(val)
        finally:
            pout.i(-2)

    def _log_exit_map(self, val, tname):
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
                except Exception as e:
                    pout('{e}Error displaying field {n}%s {e}stack:\n{s}%s',
                         fieldName, e);
                    fmtvals.append('{k}%s: {e}Error')
        pout(' '.join(fmtbits), *fmtvals)

    def _log_enter_detailed_object(self, val, rule, tname):
        pout("{n}%s {s}%x", tname, val.address)
        pout.i(2)

    def _log_enter_object_group(self, groupName, rule):
        pout("{fn}%s:", groupName)
        pout.i(2)

    def _log_field_in_detailed_object(self, key, val, displayMode):
        pout("{k}%s{n}:", key)
        pout.i(2)
        try:
            explicit_type = None
            if displayMode != 'true' and displayMode is not True:
                explicit_type = displayMode
            self._inspect(val, explicit_type)
        finally:
            pout.i(-2)

    def _log_exit_object_group(self, groupName, rule):
        pout.i(-2)

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
            cur = maybe_deref(cur, ctype)

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

            self._log_enter_array(val, tname)

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
                self._log_exit_array(val, tname)

    def _bitflags(self, val, rule, tname):
        brule = rule["bitflags"]
        zeroName = brule["zero"]
        # get the underlying value and throw if the type is wrong.
        val_bits = val | 0
        # this could probably be optimized...
        bit_pieces = []
        for bitDef in brule["bits"]:
            for bitName, bitPos in bitDef.items():
                # we have to parse the bit every time, we didn't provide a
                # specific mapping.
                if (val_bits & (1 << int(bitPos))):
                    bit_pieces.append(bitName)
        self._log_bitflag_bits(val_bits, bit_pieces, tname)

    def _print_enum(self, val, vtype, tname):
        '''HACK Given an explicitly enumerated type, try and match the current value
to one of the enumerated values.  This is really all about nsresult and the
rules about enums and the underlying representing types.  gdb is totally able to
understand that a 0 nsresult is `nsresult::NS_OK`, but it breaks as soon as the
high order bit is flipped because it ends up seeing the underlying int type as
int32_t but the constant is uint32_t.

Even if the necessity of this check is addressed, it might make sense to keep a
first-class understanding of enums so we can pretty print the namespaces.
'''
        # TODO: nsresult really demands caching...
        # Because of enum rules about the representation type perhaps not
        # being reflected into gdb or whatever, we end up seeing nsresult as a
        # signed int32_t, so let's just cast over to unsigned before extracting
        # out instead of doing bit twiddling.
        try:
            tsize = vtype.sizeof
            if tsize <= 4:
                val = val.cast(gdb.lookup_type('uint32_t'))
            else:
                val = val.cast(gdb.lookup_type('uint64_t'))
            num_val = val + 0
        except:
            # maybe we don't have those typedefs?  bollocks.  whatever.
            pass

        for field in vtype.fields():
            #pout('{s}checking %s %x', field.name, field.enumval)
            if field.enumval == num_val:
                # XXX use a logger that maybe prints the namespace as {s}
                pout('{n}%s', field.name)
                return
        pout('{s}unknown enum value {n}%x', num_val)

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

    def _print_simple(self, val, rule, tname):
        # multi-line object display without groups.
        self._log_enter_detailed_object(val, rule, tname)
        try:
            for fieldDef in rule["simple"]:
                for fieldName, displayMode in fieldDef.items():
                    try:
                        self._log_field_in_detailed_object(fieldName, val[fieldName],
                                                           displayMode)
                    except Exception as e:
                        pout("{e}Exception inspecting field %s: %s", fieldName, e)
        finally:
            self._log_exit_detailed_object(val, rule, tname)

    def _print_groups(self, val, rule, tname):
        # multi-line object display without groups.
        self._log_enter_detailed_object(val, rule, tname)

        try:
            for groupDef in rule["groups"]:
                for groupName, fieldDefs in groupDef.items():
                    self._log_enter_object_group(groupName, rule)

                    try:
                        for fieldDef in fieldDefs:
                            for fieldName, displayMode in fieldDef.items():
                                try:
                                    self._log_field_in_detailed_object(fieldName, val[fieldName],
                                                                       displayMode)
                                except Exception as e:
                                    pout("{e}Exception inspecting field %s: %s", fieldName, e)
                    finally:
                        self._log_exit_object_group(groupName, rule)
        finally:
            self._log_exit_detailed_object(val, rule, tname)



    def _gdbvis_array(self, val, vis, tname):
        self._log_enter_array(val, tname)
        # (the index may be a formatted string, not just an integer)
        for indexy, subval in vis.children():
            try:
                self._log_item_in_array(indexy, maybe_deref(subval))
            except Exception as e:
                pout("{e}Exception inspecting: %s", e)
        self._log_exit_array(val, tname)


    def _gdbvis_map(self, val, vis, tname):
        self._log_enter_map(val, tname)
        # (the index may be a formatted string, not just an integer)
        for key, subval in vis.children():
            try:
                self._log_item_in_map(key, maybe_deref(subval))
            except Exception as e:
                pout("{e}Exception inspecting: %s", e)
        self._log_exit_map(val, tname)

    def _inspect(self, val, explicit_type=None):
        '''explicit_type is currently the "displayMode" from when we normally do
"fieldName: true".  It's being introduced to support bitflag mappings for what
are just uint32_t's as far as the source is concerned.  We use the explicit type
to key like it was a normal type.  For bitflags thus far, this could be a
separate namespace, and since we wouldn't want all extra definitions like this
to be inline, it does make sense to just have it be a name that calls out.
We'll see.  This all wants to be cleaner anyways.  Let's just finish exploring
the feature space.
'''
        vtype = val.type
        #pout("!!vtype: %s %s %s %s", vtype, type(vtype), vtype.tag, type(vtype.tag))

        # pierce pointers.  Note that our caller may themselves have invoked
        # maybe_deref, so this could get weird.
        val = maybe_deref(val)

        vtype = val.type
        #pout("!vtype: %s %s %s %s", vtype, type(vtype), vtype.tag, type(vtype.tag))

        vtype = None
        if explicit_type is not None:
            tname = explicit_type
            rule = self.mapping.get(explicit_type)
        else:
            # figure out the type; we want to use RTTI if available to downcast all
            # the way.
            vtype = val.dynamic_type
            # that may have given us a better type, let's re-cast the value too.
            try:
                val = val.cast(vtype)
            except:
                pass

            # we may be a pointer type or other simple type.  In particular, we may
            # be a char*-type thing.  punt to gdb. for now.
            if vtype.name is None:
                # XXX gdb presents strings as `0xNNNN "foo bar"` in a single string
                # which breaks our pretty schema.
                pout("{n}%s", str(val))
                return

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
            # if this was an alias, pierce itgdbvis
            if isinstance(rule, str):
                rule = self.mapping.get(rule)
            #pout("Found mapping with kind %s", rule.get("kind", "default"))
            if "traverse" in rule:
                self._traverse(val, rule, tname)
            elif "iterate" in rule:
                self._iterate(val, rule, tname)
            elif "bitflags" in rule:
                self._bitflags(val, rule, tname)
            elif "terse" in rule:
                self._print_terse(val, rule, tname)
            elif "simple" in rule:
                self._print_simple(val, rule, tname)
            elif "groups" in rule:
                self._print_groups(val, rule, tname)
            else:
                pout("{e}Don't understand rule {n}%s {e}for type {n}%s",
                     repr(rule), tname)
            # Handled or errored appropriately, no need to fall through to the
            # default visualizer.
            return

        # check for heuristic stuff
        if vtype:
            # handle enums
            if vtype.code == gdb.TYPE_CODE_ENUM:
                self._print_enum(val, vtype, tname)
                return
            # just print simple types without complaining about mappings.
            if is_simple_type(vtype):
                pout("{n}%s", str(val))
                return

        pout.v("{s}Falling back to default visualizer for type %s",
               tname)
        vis = gdb.default_visualizer(val)
        if vis and hasattr(vis, 'children'):
            if hasattr(vis, 'display_hint'):
                vis_type = vis.display_hint()
            else:
                # std::set doesn't implement display_hint
                vis_type = "array"
            if vis_type == "array":
                self._gdbvis_array(val, vis, vis.to_string() or tname)
                return
            elif vis_type == "map":
                self._gdbvis_map(val, vis, vis.to_string() or tname)
                return
        elif vis:
            # the string case is effectively equivalent to just handing off to
            # gdb, so leave it up to the fall-through case.
            pass
        else:
            pout("{s}No mapping or pretty-printer for {n}%s{s}, switching to gdb print.", tname)

        # TODO implement our own form of fallback iteration over fields using
        # heuristics here.
        # gdb will do its standard thing here.
        pout("{n}%s", str(val))

    def invoke(self, arg, from_tty):
        verbose = False
        # We want a flag here...
        if arg.startswith('/v'):
            verbose = True
            pout._verbose = True
            arg = arg[3:]
        # zero out our indentation in the event of exceptions breaking things.
        pout.i(-1000)
        val = gdb.parse_and_eval(arg)
        self._inspect(val)
        if verbose:
            pout._verbose = False


PrettyPrintCommand()
