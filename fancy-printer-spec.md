
## Standard GDB Pretty Printers ##

### Spec ###

- display_hint: Return any string you want, but gdb understands only "array",
  "map", and "string".  The impact of these is:
  - array
    - The decision whether to pretty print is made on the array pretty printing
      options.  (Only arrays have this setting, maps/strings obey the struct
      option.)
    - The first element in each yielded tuple is ignored.  If the option to
      print array indexes is on, gdb does the counting itself.
    - Each element is printed out comma delimited.
  - map
    - Each element looks like (comma-delimited) `[key value] = [value value],`
      where the "key" values are the even gdb.Values and the "value" values are
      the odd gdb.Values.
    - The first element in each yielded tuple is ignored.
  - string
    - Each element looks like `name = value`, where name is the first element in
      each yielded tuple and value is the printed gdb.Value (which may get
      fancy).
* children: Returns an object implementing the iterator protocol that returns
  2-element tuples consisting of a "name" and something that can be coerced to a
  gdb.Value, which I think just means it should be a gdb.Value.   The semantics
  of the name vary based on the display_hint() return value.
  * string: I always assumed this was nonsensical (why implement children then?)
    but I see now is just rarely used because gdb's default pretty printing
    heuristic is usually fine enough here.  But the pretty printer could perform
    informed pretty printing here.
  * array: The name is probably an index.

## Fancy Printers ##

We hackily build on top of what already exists to the maximum extent possible so
that fancy printers can be used as pretty printers.

### Goals ###

As a refresher, our motivations are supporting:
- Screen real-estate awareness.  The user doesn't want to scroll through 500
  pages of stuff every time they pretty print something.
- Investigation Focus.  The user doesn't care about everything all the time.
- Hierarchy/graph awareness.  If all you have is a leaf node, you might want to
  know about its ancestors.  If we're printing the tree from the root, then the
  leaf nodes don't need to recurse into their ancestors.
- Support machine-readable output that can subsequently be further traversed.
  We want to be able to dump representations to JSON and, in a world where "rr"
  is being used, later re-inspect the object and traverse further, etc.

Just like pretty printers, we want fancy printers to have a very limited, local
awareness of what's going on.  They should be stateless other than caching
things that gdb.Value caching doesn't already cover.

### Spec ###

#### Fancy Printer Methods to Implement ####
