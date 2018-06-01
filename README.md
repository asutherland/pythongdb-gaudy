# Gaudy Python GDB Commands #

Extra commands for your GDB, lovingly wrapped in all the colors of the rainbow.
All of the colors, all at once.

## What Do They Do? ##

### cbt: colorizing backtrace ###

A backtrace that uses colors.  

### pp: pretty print ###

A pretty-printer that uses the contents of gdbaudy/pp-mozilla.yaml to know how
to print types.  Rather than printing EVERYTHING, it (recursively) prints the
fields you are interested in as defined in the yaml file.

It can also do some lookup tricks for bitmasks and enums that are expressed as
integers if you tell it the true type of the field and define the fields for
it.  See `LOAD_FLAGS` for example.

#### yaml mapping ####

There are various attempts at fanciness in the config file, those don't really
matter.  If you want to add a type, there are a few ways you'd do it:

Type aliases:
```
AliasFrom: AliasTo
```
This will cause us to lookup the AliasTo type and use its definition in the file.

Normal structure types where you care about stuff:
```
mozilla::dom::Foo:
  simple:
  - field1: true
  - field2: true
  - field3: true
```

You name the fully qualified type-name.  "simple" is the type of pretty
printing to use.  Other supported types are "terse" (try and display all the
children on a single line; only works well for primitive types), "groups"
(which is like simple, but breaks up the fields with labeled group headers),
and "traverse" (which automatically traverses its list of children in sequence
and is suitable for things like smart pointers where the intermediary structure
is just a distraction and all we want is the payload).

#### examples

Interested in some singletons in your process that might have interesting
stuff?  Then checkout
https://github.com/asutherland/pythongdb-gaudy/blob/master/gdbaudy/ppi-mozilla.yaml
and pick one of the singleton keys and do "pp THAT".  (The file is a speculative
idea to add a "ppi" command for pretty-printing points of interest without you
having to remember them all.)

For example, for ContentChild:
```
pp mozilla::dom::ContentChild::sSingleton
```

## How Do I Install Them? ##

### Whoops, Dependencies ###

I've been out of the Python game for quite some time.  Let's just install the
deps globally, why not?

If you're on Ubuntu, your gdb is probably using Python3.  In that case you want to:
* Try: `sudo pip3 install strictyaml`
* Didn't work because no pip3 is installed?  Try: `sudo apt install python3-pip` to get pip for Python3.
* Didn't work because you didn't want to use sudo?  Try `pip3 install --user strictyaml` to install them locally instead of globally.
* Didn't work because you are a time traveler from the past and you're using Python2? `pip install strictyaml` and all the variations above.

### Okay, I did that, so... ###

Add something like the following to your `~/.gdbinit`.  There are better ways
to do this, but this is what I use and my computer has only caught on fire
twice:
```
python import sys
python sys.path[0:0] = ["/path/to/pythongdb-gaudy/"]
# backtrace, no external dependencies
python import gdbaudy.bt
# pretty-printing
python import gdbaudy.pp
```

Then you can use:
- "cbt" as an awesome colorized backtrace
- "cbt terse" produces the bare necessities of a colorized backtrace
- "cbt paste" produces a different bare colorized backtrace suitable for
  copying and pasting somewhere for humans to read without all the noise.
- "cbt full" is like "cbt" but with locals displayed too.
- "pp THING" pretty print THING.  Modify gdbaudy/pp-mozilla.yaml to teach it
   about new types.  Reload using the info below

Things that you used to be able to use but are now bit-rotted or moot:
- "sl" as an awesome colorized / syntax highlighting "list"-like command.  I
  thought this was cool at the time, but searchfox didn't exist then.
- "mbt" as a colorized fused C++/JS mozilla-specific backtrace.  This was a
  neat hack in a pre-JIT world, but has not made any sense since then.  Also,
  SpiderMonkey already has in-tree unwinder support that's disabled by default.
  It aggressively didn't work for me when I tried it in May, 2018, but YMMV.  In
  general, the best approach is just to do `call DumpJSStack()`.  tricelog also
  has some hacky magic to get the info into a string rather than dumping it to
  stderr (via a buffer capped at 2k).

## How Do I Develop Them? ##

That's right, by reading this far, you've earned the right to help develop these
further!

Python has built-in reloading, so after you make sure you have the following in
your `.gdbinit `:
```
python from imp import reload
```
...you can do the following:
- `python reload(gdbaudy.bt)`
- `python reload(gdbaudy.pp)`
