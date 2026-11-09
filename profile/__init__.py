"""profile: student profiles, one JSON file each, in <work-folder>/personal/.

They are made from the <name>.json files in <input-folder>/personal/.
profile_load reads every *.json there into memory, keyed by file name;
browser_view's Profiles pages upload them and show them.

Note: this package shadows the standard library's ``profile`` module (and so
``cProfile``) whenever the repo root is on sys.path.
"""
